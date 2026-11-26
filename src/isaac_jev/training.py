"""Demonstration dataset, frozen-feature training, and local shadow/AI inference."""
import asyncio
import hashlib
import json
from pathlib import Path
import time
from collections import Counter
from .actions import Action,ACTION_VERSION,action_map,label_from_input
from .features import contract,contract_for,controls,policy_observation,HISTORY_OFFSETS,CONDITIONS,DEPLOYMENT_CONDITION
from .outcomes import segment_frames,collect_player_damage,damage_evidence,label_outcome,event_key
from .recording import iter_messages
from .bridge import request_control
from .rooms import TRAINING_PHASE,combat_active
from .dataset_filters import find_combat_bomb_rooms,UNSUPPORTED_ACTION_FILTER,find_active_item_rooms,ACTIVE_ITEM_FILTER

def split_for_run(run_id):
    bucket=int(hashlib.sha256(run_id.encode()).hexdigest()[:8],16)%10
    return "test" if bucket==0 else "validation" if bucket==1 else "train"

def active_head_eligible(rows,train_ids):
    positive=[rows[i] for i in train_ids if rows[i]["active_label"]]
    # Conservative demo gate, not evidence that active use has generalized.
    return len(positive)>=5 and len({r["run_id"] for r in positive})>=2 and len(positive)<len(train_ids)

def imitation_baselines(rows,train_ids,evaluate_ids):
    if not evaluate_ids:return {"samples":0}
    majority=Counter(rows[i]["action_label"] for i in train_ids).most_common(1)[0][0]
    repeated=move_repeated=shoot_repeated=0
    for index in evaluate_ids:
        row=rows[index]
        values=dict(zip(row["numeric_names"],row["numeric"]))
        if "previous_action_label" in row:previous=row["previous_action_label"]
        elif "previous_action" in values:previous=round(values["previous_action"]*44)
        else:
            previous=max(range(9),key=lambda i:values[f"previous_move_{i}"])*5+max(range(5),key=lambda i:values[f"previous_shoot_{i}"])
        repeated+=int(previous==row["action_label"])
        move_repeated+=int(previous//5==row["action_label"]//5)
        shoot_repeated+=int(previous%5==row["action_label"]%5)
    return {"samples":len(evaluate_ids),"repeat_previous_action_accuracy":repeated/len(evaluate_ids),
            "repeat_previous_move_accuracy":move_repeated/len(evaluate_ids),
            "repeat_previous_shoot_accuracy":shoot_repeated/len(evaluate_ids),
            "training_majority_action_accuracy":sum(rows[i]["action_label"]==majority for i in evaluate_ids)/len(evaluate_ids)}


def previous_action_label(row):
    if "previous_action_label" in row:return row["previous_action_label"]
    values=dict(zip(row["numeric_names"],row["numeric"]))
    return max(range(9),key=lambda i:values[f"previous_move_{i}"])*5+max(range(5),key=lambda i:values[f"previous_shoot_{i}"])

def prepare_dataset(recordings:Path,output:Path,stride=1,split_overrides=None,outcome_window_frames=30,exclude_active_items=False,action_lag_frames=1,control_context=True,feature_profile=None):
    preparation_started=time.monotonic()
    def report_stage(stage):
        print(f"Dataset preparation: {stage}; elapsed={time.monotonic()-preparation_started:.1f}s",flush=True)
    profile=contract(action_lag_frames,control_context,feature_profile)
    extended=feature_profile=='combat-build-terrain-weapons-v5'
    phase=lambda f:combat_active(f,extended)
    def messages():return iter_messages(recordings,include_diagnostics=False,include_session=True,only_closed=True)
    if action_lag_frames!=1 and not exclude_active_items:raise ValueError("Delayed action labels currently require active items disabled")
    if stride<1:raise ValueError("stride must be positive")
    if not 1<=outcome_window_frames<=150:raise ValueError("Outcome window must be 1..150 frames")
    split_overrides=dict(split_overrides or {})
    if any(not isinstance(run,str) or split not in {"train","validation","test"} for run,split in split_overrides.items()):
        raise ValueError("Invalid whole-run split override")
    output.mkdir(parents=True,exist_ok=True)
    counts={"train":0,"validation":0,"test":0};runs={k:set() for k in counts};active_count=0
    # Item use can occur before MC_PRE_UPDATE in the same logic frame. A
    # complete event pass also handles events transported after their frame.
    uses=set();available_runs=set();source_sessions=set()
    report_stage('indexing recordings')
    for message in messages():
        if message.get("type")=="frame":
            available_runs.add(message["run_id"])
            source_sessions.add(Path(message["_recording_session"]).parent.name)
        if message.get("type")=="event" and message.get("event")=="item_used" and message.get("active_slot")==0:
            uses.add((*event_key(message),message.get("item_id")))
    missing=set(split_overrides)-available_runs
    if missing:raise ValueError(f"Split overrides reference unrecorded runs: {sorted(missing)}")
    report_stage(f'indexed {len(source_sessions)} sessions; collecting damage events')
    callbacks=collect_player_damage(messages())
    report_stage('identifying combat bomb rooms')
    bomb_rooms,bomb_triggers=find_combat_bomb_rooms(messages(),phase)
    report_stage('identifying active-item rooms')
    active_rooms,active_inputs=(find_active_item_rooms(messages(),
                                                    {key[:3] for key in uses}) if exclude_active_items else (set(),0))
    unaligned=0;active_attempts=0;outcome_counts={split:Counter() for split in counts};reasons=Counter();damage_counts=Counter()
    excluded_bomb_pairs=0
    excluded_active_pairs=0
    report_stage('writing combat samples')
    with (output/"samples.jsonl").open("w",encoding="utf-8") as out:
        for segment in segment_frames(messages()):
            unaligned+=1
            evidence=damage_evidence(segment,callbacks)
            segment_split=split_overrides.get(segment[0]["run_id"],split_for_run(segment[0]["run_id"]))
            damage_counts[segment_split]+=sum(v=="confirmed_player_damage" for v in evidence.values())
            if (segment[0]["run_id"],segment[0]["room"]["id"]) in bomb_rooms:
                excluded_bomb_pairs+=sum(combat_active(a) and combat_active(b) for a,b in zip(segment,segment[1:]))
                continue
            if (segment[0]["run_id"],segment[0]["room"]["id"]) in active_rooms:
                excluded_active_pairs+=sum(combat_active(a) and combat_active(b) for a,b in zip(segment,segment[1:]))
                continue
            for index in range(len(segment)-action_lag_frames):
                observation,frame=segment[index],segment[index+action_lag_frames]
                if frame["player"].get("dead") or observation["player"].get("dead"):continue
                if not observation["player"].get("controls_enabled",True):continue
                if not all(phase(f) and f["player"].get("controls_enabled",True) and not f["player"].get("dead")
                           for f in segment[index:index+action_lag_frames+1]):continue
                action,attempt=label_from_input(frame["human_input"])
                item=observation["player"].get("active",{})
                active=bool(attempt and item.get("ready") and (*event_key(frame),item.get("id")) in uses)
                active_attempts+=int(attempt)
                # Keep rare active-item edges even when movement samples are subsampled.
                release=extended and action%5==0 and controls(observation)[0]%5!=0
                if frame["game_frame"]%stride and not active and not release:continue
                outcome=label_outcome(segment,index,evidence,outcome_window_frames,action_lag_frames,phase if extended else None)
                past=segment[max(0,index-max(HISTORY_OFFSETS)):index]
                prompt,numeric,names=policy_observation(observation,past,condition=outcome["label"],include_active=not exclude_active_items,include_controls=control_context,feature_profile=feature_profile)
                deployment_prompt=prompt.rsplit("\nDesired outcome condition:",1)[0]+"\nDesired outcome condition: "+DEPLOYMENT_CONDITION+".\nChoose combat controls."
                deployment_numeric=list(numeric)
                for label in CONDITIONS:deployment_numeric[names.index(f"condition_{label}")]=float(label==DEPLOYMENT_CONDITION)
                split=split_overrides.get(frame["run_id"],split_for_run(frame["run_id"]));counts[split]+=1;runs[split].add(frame["run_id"])
                active_count+=int(active);outcome_counts[split][outcome["label"]]+=1;reasons[outcome["reason"]]+=1
                row={"run_id":frame["run_id"],"room_id":frame["room"]["id"],"game_frame":observation["game_frame"],"action_frame":frame["game_frame"],
                     "split":split,"prompt":prompt,"numeric":numeric,"numeric_names":names,
                     "deployment_prompt":deployment_prompt,"deployment_numeric":deployment_numeric,
                     "action_label":action,"move_label":action//5,"shoot_label":action%5,
                     "active_label":active,"active_attempt":attempt,"active_item_id":item.get("id",0),"outcome":outcome}
                row["previous_action_label"]=controls(observation)[0] # Evaluation/training weights only, never policy input.
                if extended:
                    from .weapons import weapon_state
                    row['weapon_family']=weapon_state(observation)['name'];row['weapon_charge_known']=weapon_state(observation)['charge_known'];row['release_transition']=release
                out.write(json.dumps(row,ensure_ascii=False,separators=(",",":"),allow_nan=False)+"\n")
                if sum(counts.values())%1024==0:report_stage(f'wrote {sum(counts.values())} samples')
    summary={**profile,"counts":counts,"runs":{k:len(v) for k,v in runs.items()},"active_triggers":active_count,
             "split_rule":"whole-run explicit overrides; otherwise sha256(run_id) modulo 10: 0=test, 1=validation, 2..9=train","stride":stride,
             "split_overrides":split_overrides,"run_splits":{run:split_overrides.get(run,split_for_run(run)) for run in sorted(available_runs)},
             "segment_boundaries":unaligned,"active_attempts":active_attempts,"diagnostic_sessions_excluded":True,
             "outcome_window_frames":outcome_window_frames,"outcome_counts":{k:dict(v) for k,v in outcome_counts.items()},
             "confirmed_damage_events_by_split":dict(damage_counts),
             "outcome_reasons":dict(reasons),"outcome_warning":"Observed trajectory association, not causal action correctness",
             "training_phase":profile.get('training_phase',TRAINING_PHASE),"unsupported_action_filter":UNSUPPORTED_ACTION_FILTER,"source_sessions":sorted(source_sessions),
             "excluded_combat_bomb_rooms":[{"run_id":run,"room_id":room} for run,room in sorted(bomb_rooms)],
             "combat_bomb_triggers":bomb_triggers,"combat_bomb_pairs_excluded_before_stride":excluded_bomb_pairs}
    summary.update({"exclude_active_items":exclude_active_items,"active_context":not exclude_active_items,
                    "active_item_filter":ACTIVE_ITEM_FILTER if exclude_active_items else None,
                    "excluded_active_rooms":[{"run_id":run,"room_id":room} for run,room in sorted(active_rooms)],
                    "active_item_attempts_filtered":active_inputs,"active_pairs_excluded_before_stride":excluded_active_pairs})
    (output/"dataset.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    report_stage(f'complete: {sum(counts.values())} samples')
    if not counts["train"] or not counts["validation"]:
        print("Collect more independent runs: supervised model selection needs both train and validation groups.")

def _dependencies():
    try:
        import torch,numpy
        from safetensors.torch import save_file,load_file
    except ImportError as exc:
        raise RuntimeError("Training/inference extras are missing: install the project with pip install -e '.[train]' in the GPU environment") from exc
    return torch,numpy,save_file,load_file

def train(args):
    if args.epochs<1:raise ValueError("epochs must be positive")
    feature_batch=getattr(args,"feature_batch_size",16)
    if feature_batch<1:raise ValueError("feature_batch_size must be positive")
    feature_pad=getattr(args,"feature_pad_multiple",0)
    if feature_pad<0 or (feature_pad and args.max_length%feature_pad):raise ValueError("Feature padding must divide token budget")
    patience=getattr(args,"patience",8);min_delta=getattr(args,"min_delta",0.005)
    if patience<1 or min_delta<0:raise ValueError("patience must be positive and min_delta nonnegative")
    torch,np,save_file,_=_dependencies()
    cpu_threads=getattr(args,"cpu_threads",1)
    move_boost=getattr(args,"move_transition_weight",1.0);shoot_boost=getattr(args,"shoot_transition_weight",1.0)
    if cpu_threads<1 or not 1<=move_boost<=25 or not 1<=shoot_boost<=25:raise ValueError("Invalid CPU/transition weighting settings")
    torch.set_num_threads(cpu_threads)
    from .model import FrozenQwen,CombatHead
    from torch.utils.data import DataLoader,TensorDataset
    dataset=Path(args.dataset);output=Path(args.output)
    meta=json.loads((dataset/"dataset.json").read_text(encoding="utf-8"))
    expected_phase='enemy-projectile-imminent-hazard-v2' if meta.get('feature_version')=='combat-build-terrain-weapons-v5' else TRAINING_PHASE
    if meta.get("training_phase")!=expected_phase:raise ValueError("Rebuild the dataset for the selected combat phases")
    if meta.get("unsupported_action_filter")!=UNSUPPORTED_ACTION_FILTER:raise ValueError("Rebuild the dataset to exclude combat rooms with human bomb input")
    profile=contract_for(meta)
    if any(meta.get(k)!=v for k,v in profile.items()):raise ValueError("Dataset feature/rule contract differs from the current code")
    rows=[json.loads(l) for l in (dataset/"samples.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    groups={}
    for row in rows:groups.setdefault(row["run_id"],set()).add(row["split"])
    if any(len(splits)>1 for splits in groups.values()):raise ValueError("A run appears in multiple dataset splits")
    train_ids=[i for i,r in enumerate(rows) if r["split"]=="train"]
    val_ids=[i for i,r in enumerate(rows) if r["split"]=="validation"]
    test_ids=[i for i,r in enumerate(rows) if r["split"]=="test"]
    if not train_ids or not val_ids:raise ValueError("Need independent train and validation runs; record more runs before training")
    output.mkdir(parents=True,exist_ok=True)
    torch.manual_seed(42)
    encoder=FrozenQwen(args.model,args.revision,args.device,max_length=args.max_length)
    size=encoder.embedding_size;revision=encoder.revision
    emb=np.empty((len(rows),size),dtype=np.float16)
    order=sorted(range(len(rows)),key=lambda i:len(rows[i]["prompt"]))
    feature_started=time.monotonic()
    def encode_features(prompts):
        return encoder.encode(prompts,pad_to_multiple_of=feature_pad) if feature_pad else encoder.encode(prompts)
    for start in range(0,len(rows),feature_batch):
        ids=order[start:start+feature_batch]
        emb[ids]=encode_features([rows[i]["prompt"] for i in ids]).cpu().to(torch.float16).numpy()
        completed=start+len(ids)
        if completed==feature_batch or completed%256<feature_batch or completed==len(rows):
            print(f"Frozen features: {completed}/{len(rows)}; elapsed={time.monotonic()-feature_started:.1f}s",flush=True)
    deployment_emb=emb.copy()
    changed=[i for i in val_ids+test_ids if rows[i]["deployment_prompt"]!=rows[i]["prompt"]]
    changed.sort(key=lambda i:len(rows[i]["deployment_prompt"]))
    for start in range(0,len(changed),feature_batch):
        ids=changed[start:start+feature_batch]
        deployment_emb[ids]=encode_features([rows[i]["deployment_prompt"] for i in ids]).cpu().to(torch.float16).numpy()
    del encoder
    if args.device.startswith("cuda"):torch.cuda.empty_cache()
    np.savez_compressed(output/"frozen_features.npz",embedding=emb,deployment_embedding=deployment_emb)
    numeric=np.asarray([r["numeric"] for r in rows],dtype=np.float32)
    # Features already use bounded physical units; binary/category indicators are never z-scored.
    mean=np.zeros(numeric.shape[1],dtype=np.float32);std=np.ones(numeric.shape[1],dtype=np.float32)
    deployment_numeric=np.asarray([r["deployment_numeric"] for r in rows],dtype=np.float32)
    action=np.asarray([r["action_label"] for r in rows],dtype=np.int64)
    previous=np.asarray([previous_action_label(r) for r in rows],dtype=np.int64)
    active=np.asarray([r["active_label"] for r in rows],dtype=np.float32)
    sample_weight=np.asarray([r["outcome"]["weight"] for r in rows],dtype=np.float32)
    positive=float(active[train_ids].sum());negative=len(train_ids)-positive
    active_present=not meta.get("exclude_active_items",False)
    active_trained=active_present and active_head_eligible(rows,train_ids)
    if not active_present:print("Active output omitted: active-item rooms and context excluded by dataset policy.",flush=True)
    elif not active_trained:print(f"Active head disabled: {int(positive)} positive samples; requires >=5 positives across >=2 training runs and negatives.",flush=True)
    weight=min(30,negative/max(1,positive))
    model=CombatHead(size,numeric.shape[1],active_enabled=active_present).to(args.device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=1e-3)
    bce=torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight,device=args.device))
    def tensors(ids,deployment=False):
        e,n=(deployment_emb,deployment_numeric) if deployment else (emb,numeric)
        return TensorDataset(torch.from_numpy(e[ids]),torch.from_numpy(n[ids]),
                             torch.from_numpy(action[ids]),torch.from_numpy(active[ids]),torch.from_numpy(sample_weight[ids]),
                             torch.from_numpy(previous[ids]))
    def evaluate(ids,deployment=False):
        if not ids:return {"samples":0}
        model.eval();correct=move_correct=shoot_correct=0;loss_sum=0;pred_pos=true_pos=gold_pos=0
        move_changes=shoot_changes=move_change_correct=shoot_change_correct=idle_predictions=0;prediction_counts=Counter()
        with torch.no_grad():
            for e,n,a,u,w,p in DataLoader(tensors(ids,deployment),batch_size=256):
                e,n,a,u,p=[x.to(args.device) for x in (e,n,a,u,p)]
                move_logits,shoot_logits,active_logits=model(e,n)
                loss_sum+=(torch.nn.functional.cross_entropy(move_logits,a//5,reduction="sum")+
                           torch.nn.functional.cross_entropy(shoot_logits,a%5,reduction="sum")).item()
                m,s=move_logits.argmax(-1),shoot_logits.argmax(-1)
                move_correct+=(m==a//5).sum().item();shoot_correct+=(s==a%5).sum().item()
                correct+=(m*5+s==a).sum().item()
                mc,sc=a//5!=p//5,a%5!=p%5
                move_changes+=mc.sum().item();shoot_changes+=sc.sum().item()
                move_change_correct+=((m==a//5)&mc).sum().item();shoot_change_correct+=((s==a%5)&sc).sum().item()
                predicted_action=m*5+s;idle_predictions+=(predicted_action==0).sum().item()
                prediction_counts.update(predicted_action.cpu().tolist())
                predicted=active_logits.sigmoid()>0.8
                pred_pos+=predicted.sum().item();gold_pos+=(u>0.5).sum().item();true_pos+=(predicted&(u>0.5)).sum().item()
        return {"samples":len(ids),"action_accuracy":correct/len(ids),"action_nll":loss_sum/len(ids),
                "move_accuracy":move_correct/len(ids),"shoot_accuracy":shoot_correct/len(ids),
                "move_changes":{"samples":move_changes,"accuracy":move_change_correct/move_changes if move_changes else None},
                "shoot_changes":{"samples":shoot_changes,"accuracy":shoot_change_correct/shoot_changes if shoot_changes else None},
                "idle_prediction_fraction":idle_predictions/len(ids),"prediction_counts":dict(prediction_counts),
                "active_precision":true_pos/pred_pos if active_trained and pred_pos else None,
                "active_recall":true_pos/gold_pos if active_trained and gold_pos else None}
    positive_val=[i for i in val_ids if rows[i]["outcome"]["label"]==DEPLOYMENT_CONDITION]
    positive_train=[i for i in train_ids if rows[i]["outcome"]["label"]==DEPLOYMENT_CONDITION]
    if not positive_train:raise ValueError("No positive training trajectories; cannot deploy a positive-conditioned policy")
    if not positive_val:print("WARNING: no positive validation trajectories; model selection uses conditional imitation only.",flush=True)
    best=float("inf");meaningful_best=float("inf");stale_epochs=0;best_epoch=0;history=[]
    for epoch in range(args.epochs):
        model.train()
        for e,n,a,u,w,p in DataLoader(tensors(train_ids),batch_size=128,shuffle=True):
            e,n,a,u,w,p=[x.to(args.device) for x in (e,n,a,u,w,p)]
            move_logits,shoot_logits,active_logits=model(e,n)
            mw=w*(1+(move_boost-1)*(a//5!=p//5));sw=w*(1+(shoot_boost-1)*(a%5!=p%5))
            move_loss=torch.nn.functional.cross_entropy(move_logits,a//5,reduction="none")
            shoot_loss=torch.nn.functional.cross_entropy(shoot_logits,a%5,reduction="none")
            loss=(move_loss*mw).sum()/mw.sum()+(shoot_loss*sw).sum()/sw.sum()
            if active_trained:loss=loss+0.25*bce(active_logits,u)
            optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
        metrics=evaluate(positive_val or val_ids,deployment=bool(positive_val))
        train_metrics=evaluate(train_ids)
        history.append({"epoch":epoch+1,"train":train_metrics,"validation_selection":metrics})
        epoch_dir=output/"head_checkpoints"
        epoch_dir.mkdir(exist_ok=True)
        epoch_state={k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()}
        save_file(epoch_state,str(epoch_dir/f"epoch-{epoch+1:03d}.safetensors"))
        save_file(epoch_state,str(output/"last_head.safetensors"))
        (epoch_dir/"index.json").write_text(json.dumps({"retention":"all-epochs","epochs":history},indent=2),encoding="utf-8")
        print(f"epoch {epoch+1}: train={train_metrics}; validation={metrics}",flush=True)
        if metrics["action_nll"]<best:
            best=metrics["action_nll"]
            best_epoch=epoch+1
            save_file({k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()},str(output/"head.safetensors"))
        if metrics["action_nll"]<meaningful_best-min_delta:
            meaningful_best=metrics["action_nll"];stale_epochs=0
        else:stale_epochs+=1
        if stale_epochs>=patience:
            print(f"Early stopping at epoch {epoch+1}; best checkpoint epoch {best_epoch}.",flush=True)
            break
    from safetensors.torch import load_file
    model.load_state_dict(load_file(str(output/"head.safetensors")))
    config={**profile,"model":args.model,"revision":revision,"embedding_size":size,"numeric_size":numeric.shape[1],
            "artifact_purpose":"limited-data-imitation-training",
            "transition_loss_weights":{"move":move_boost,"shoot":shoot_boost},
            "feature_padding_multiple":feature_pad,
            "training_phase":meta['training_phase'],
            "unsupported_action_filter":UNSUPPORTED_ACTION_FILTER,
            "outcome_window_frames":meta["outcome_window_frames"],"training_conditions":list(CONDITIONS),
            "model_selection":"positive-validation-joint-nll" if positive_val else "conditional-validation-joint-nll-no-positive-validation",
            "head_hidden_size":256,"max_length":args.max_length,"dtype":"bfloat16","hold_frames":3,
            "active_head_trained":active_trained,"active_threshold":0.8,
            "active_head_present":active_present,"active_context":meta.get("active_context",True),
            "dataset_sha256":hashlib.sha256((dataset/"samples.jsonl").read_bytes()).hexdigest(),
            "early_stopping":{"maximum_epochs":args.epochs,"completed_epochs":len(history),"best_epoch":best_epoch,
                              "patience":patience,"min_delta":min_delta},
            "checkpoint_retention":"all-epochs","checkpoint_epoch":best_epoch,
            "active_enable_min_positives":5,"active_enable_min_positive_runs":2,
            "active_item_ids":sorted({rows[i]["active_item_id"] for i in train_ids if rows[i]["active_label"]}),
            "numeric_names":rows[0]["numeric_names"]}
    (output/"policy_config.json").write_text(json.dumps(config,indent=2),encoding="utf-8")
    (output/"normalization.json").write_text(json.dumps({"kind":"fixed-physical-scales-v1","mean":mean.tolist(),"std":std.tolist()}),encoding="utf-8")
    (output/"action_map.json").write_text(json.dumps(action_map(),indent=2),encoding="utf-8")
    def split_evaluation(ids):
        return {"conditioned_imitation":evaluate(ids),"fixed_positive_all":evaluate(ids,deployment=True),
                'by_weapon_family':{family:evaluate([i for i in ids if rows[i].get('weapon_family','tears')==family],deployment=True) for family in sorted({rows[i].get('weapon_family','tears') for i in ids})},
                'charge_known_samples':sum(rows[i].get('weapon_charge_known',False) for i in ids),
                'release_transitions':evaluate([i for i in ids if rows[i].get('release_transition')],deployment=True),
                "fixed_positive_on_observed_positive":evaluate([i for i in ids if rows[i]["outcome"]["label"]==DEPLOYMENT_CONDITION],deployment=True),
                "by_observed_outcome":{label:evaluate([i for i in ids if rows[i]["outcome"]["label"]==label]) for label in CONDITIONS},
                "warning":"Matching negative demonstrations under positive is not a deployment objective; offline agreement is not live safety"}
    (output/"evaluation.json").write_text(json.dumps({"validation":split_evaluation(val_ids),"test":split_evaluation(test_ids),"history":history,
        "baselines":{"validation":imitation_baselines(rows,train_ids,val_ids),"test":imitation_baselines(rows,train_ids,test_ids),
                     "positive_validation":imitation_baselines(rows,positive_train,positive_val)},
        "active_positive_train":positive,"scope":"Offline imitation metrics, not live clear-room performance"},indent=2),encoding="utf-8")
    files=["head.safetensors","policy_config.json","normalization.json","action_map.json","evaluation.json"]
    (output/"manifest.json").write_text(json.dumps({f:hashlib.sha256((output/f).read_bytes()).hexdigest() for f in files},indent=2),encoding="utf-8")

async def infer(args):
    if getattr(args,"max_seconds",None) is not None and args.max_seconds<=0:raise ValueError("max_seconds must be positive")
    startup_timeout=getattr(args,"startup_timeout",600)
    if startup_timeout<=0:raise ValueError("startup_timeout must be positive")
    torch,np,_,load_file=_dependencies()
    from .model import FrozenQwen,head_from_config,decode_actions
    root=Path(args.checkpoint)
    cfg=json.loads((root/"policy_config.json").read_text(encoding="utf-8"))
    if any(cfg.get(k)!=v for k,v in contract_for(cfg).items()):raise ValueError("Checkpoint rules/features differ from this runtime")
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    for file,digest in manifest.items():
        if (root/file).resolve().parent!=root.resolve():raise ValueError("Invalid manifest path")
        if hashlib.sha256((root/file).read_bytes()).hexdigest()!=digest:raise ValueError(f"Checksum failed: {file}")
    encoder=FrozenQwen(cfg["model"],cfg["revision"],args.device,cfg["dtype"],cfg["max_length"])
    head=head_from_config(cfg).to(args.device)
    head.load_state_dict(load_file(str(root/"head.safetensors")));head.eval()
    norm=json.loads((root/"normalization.json").read_text(encoding="utf-8"))
    mean,std=np.asarray(norm["mean"]),np.asarray(norm["std"])
    # Warm up before taking control; the first kernel invocation must not delay a live AI action.
    from .mock import sample_frame
    from .features import extended_profile
    feature_profile=cfg.get('feature_version') if extended_profile(cfg.get('feature_version')) else None
    encoder.encode([policy_observation(sample_frame(),condition=cfg['deployment_condition'],include_active=cfg.get("active_context",True),include_controls=cfg.get("control_context",True),feature_profile=feature_profile)[0]])
    print("Policy ready: unpause the game; the requested mode starts on a fresh frame.",flush=True)
    previous=None;running_since=None;waiting_since=time.monotonic();mode_requested=False
    try:
        while True:
            if running_since is not None and getattr(args,"max_seconds",None) is not None and time.monotonic()-running_since>=args.max_seconds:
                print("Timed inference check completed.",flush=True);break
            request={"command":"get_frame","include_history":True}
            if cfg.get('feature_version', '').startswith('combat-semantic-v7-'):request['include_semantic_history']=True
            reply=await request_control(request,port=args.port);frame=reply.get("frame")
            if not reply["status"].get("connected"):raise RuntimeError("Isaac disconnected")
            if not mode_requested:
                age=reply["status"].get("last_frame_age_ms")
                if frame and age is not None and age<100:
                    mode_reply=await request_control({"command":"set_mode","mode":args.mode},port=args.port)
                    if not mode_reply.get("ok"):raise RuntimeError("Cannot enter requested control mode: "+str(mode_reply))
                    mode_requested=True
                else:
                    if time.monotonic()-waiting_since>startup_timeout:raise RuntimeError("No fresh game frame; unpause the game before inference")
                    await asyncio.sleep(0.05)
                continue
            if not frame or frame["mode"]!=args.mode:
                if running_since is not None:print("Human takeover / mode changed; inference stopped.");break
                if time.monotonic()-waiting_since>startup_timeout:raise RuntimeError("No fresh game frame; unpause the game before inference")
                await asyncio.sleep(0.05);continue
            if running_since is None:running_since=time.monotonic()
            key=(frame["run_id"],frame["game_frame"])
            if key==previous:await asyncio.sleep(0.01);continue
            previous=key
            if frame["player"].get("dead") or (not combat_active(frame) and args.mode=="ai"):
                await asyncio.sleep(0.05);continue
            if "policy_history" not in reply:raise RuntimeError("Restart the bridge to enable v3 short-term history")
            prompt,numeric,names=policy_observation(frame,condition=cfg["deployment_condition"],history_facts=reply['semantic_history'] if cfg.get('feature_version', '').startswith('combat-semantic-v7-') else reply['policy_history'],include_active=cfg.get("active_context",True),include_controls=cfg.get("control_context",True),feature_profile=feature_profile)
            if names!=cfg["numeric_names"]:raise ValueError("numeric feature order mismatch")
            start=time.perf_counter()
            with torch.no_grad():
                move_logits,shoot_logits,active_logits=head(encoder.encode([prompt]),torch.tensor((np.asarray(numeric)-mean)/std,dtype=torch.float32,device=args.device).unsqueeze(0))
                decoded_move,decoded_shoot=decode_actions(head,move_logits,shoot_logits)
                move_id=decoded_move.item();shoot_id=decoded_shoot.item()
                active_probability=active_logits.sigmoid().item()
            active=frame["player"].get("active",{})
            use=bool(cfg["active_head_trained"] and active.get("ready") and active.get("id") in cfg["active_item_ids"] and active_probability>=cfg["active_threshold"])
            action=Action(move_id*5+shoot_id,use,cfg["hold_frames"])
            model_ms=(time.perf_counter()-start)*1000
            result=await request_control(action.as_command(frame),port=args.port)
            print(json.dumps({"frame":key,"action":action.action_id,"active_probability":round(active_probability,3),
                "move_id":move_id,"shoot_id":shoot_id,"condition":cfg["deployment_condition"],
                "use_active":use,"model_ms":round(model_ms,1),"command_accepted":result.get("ok"),
                "control_injected":args.mode=="ai" and bool(result.get("ok")),"command_error":result.get("error")}),flush=True)
    finally:
        await request_control({"command":"set_mode","mode":"manual"},port=args.port)
