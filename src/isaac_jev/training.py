"""Demonstration dataset, frozen-feature training, and local shadow/AI inference."""
import asyncio
import hashlib
import json
from pathlib import Path
import time
from .actions import Action,ACTION_VERSION,action_map,label_from_input
from .features import contract,policy_observation
from .recording import iter_messages
from .bridge import request_control

def split_for_run(run_id):
    bucket=int(hashlib.sha256(run_id.encode()).hexdigest()[:8],16)%10
    return "test" if bucket==0 else "validation" if bucket==1 else "train"

def prepare_dataset(recordings:Path,output:Path,stride=1):
    if stride<1:raise ValueError("stride must be positive")
    output.mkdir(parents=True,exist_ok=True)
    counts={"train":0,"validation":0,"test":0};runs={k:set() for k in counts};active_count=0
    # Item use can occur before MC_PRE_UPDATE in the same logic frame. A
    # complete event pass also handles events transported after their frame.
    uses={(m["run_id"],m["game_frame"],m.get("item_id"))
          for m in iter_messages(recordings,include_diagnostics=False)
          if m.get("type")=="event" and m.get("event")=="item_used" and m.get("active_slot")==0}
    preceding={};unaligned=0;active_attempts=0
    with (output/"samples.jsonl").open("w",encoding="utf-8") as out:
        for frame in iter_messages(recordings,include_diagnostics=False):
            if frame.get("type")!="frame":continue
            observation=preceding.get(frame["run_id"])
            preceding[frame["run_id"]]=frame
            if not observation or observation["game_frame"]+1!=frame["game_frame"] or observation["room"]["id"]!=frame["room"]["id"]:
                unaligned+=1;continue
            if frame.get("mode")!="manual" or observation.get("mode")!="manual":continue
            if frame.get("capture_phase")!="pre_update" or observation.get("capture_phase")!="pre_update":continue
            if frame.get("supported_player_count",1)!=1 or observation.get("supported_player_count",1)!=1 or frame["player"].get("dead"):continue
            if not observation["player"].get("controls_enabled",True):continue
            if observation["room"].get("clear") and not any(e.get("kind")=="projectile" for e in observation["entities"]):continue
            action,attempt=label_from_input(frame["human_input"])
            item=observation["player"].get("active",{})
            active=bool(attempt and item.get("ready") and (frame["run_id"],frame["game_frame"],item.get("id")) in uses)
            active_attempts+=int(attempt)
            # Keep rare active-item edges even when movement samples are subsampled.
            if frame["game_frame"]%stride and not active:continue
            prompt,numeric,names=policy_observation(observation)
            split=split_for_run(frame["run_id"]);counts[split]+=1;runs[split].add(frame["run_id"])
            active_count+=int(active)
            row={"run_id":frame["run_id"],"room_id":frame["room"]["id"],"game_frame":observation["game_frame"],"action_frame":frame["game_frame"],
                 "split":split,"prompt":prompt,"numeric":numeric,"numeric_names":names,
                 "action_label":action,"active_label":active,"active_attempt":attempt,"active_item_id":item.get("id",0)}
            out.write(json.dumps(row,ensure_ascii=False,separators=(",",":"),allow_nan=False)+"\n")
    summary={**contract(),"counts":counts,"runs":{k:len(v) for k,v in runs.items()},"active_triggers":active_count,
             "split_rule":"sha256(run_id) modulo 10: 0=test, 1=validation, 2..9=train","stride":stride,
             "unaligned_frames_skipped":unaligned,"active_attempts":active_attempts,"diagnostic_sessions_excluded":True}
    (output/"dataset.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=False,indent=2))
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
    torch,np,save_file,_=_dependencies()
    from .model import FrozenQwen,CombatHead
    from torch.utils.data import DataLoader,TensorDataset
    dataset=Path(args.dataset);output=Path(args.output)
    meta=json.loads((dataset/"dataset.json").read_text(encoding="utf-8"))
    if any(meta.get(k)!=v for k,v in contract().items()):raise ValueError("Dataset feature/rule contract differs from the current code")
    rows=[json.loads(l) for l in (dataset/"samples.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    train_ids=[i for i,r in enumerate(rows) if r["split"]=="train"]
    val_ids=[i for i,r in enumerate(rows) if r["split"]=="validation"]
    test_ids=[i for i,r in enumerate(rows) if r["split"]=="test"]
    if not train_ids or not val_ids:raise ValueError("Need independent train and validation runs; record more runs before training")
    output.mkdir(parents=True,exist_ok=True)
    torch.manual_seed(42)
    encoder=FrozenQwen(args.model,args.revision,args.device,max_length=args.max_length)
    embeddings=[]
    for start in range(0,len(rows),4):
        embeddings.append(encoder.encode([r["prompt"] for r in rows[start:start+4]]).cpu().to(torch.float16).numpy())
        if start%100==0:print(f"Frozen features: {start}/{len(rows)}",flush=True)
    emb=np.concatenate(embeddings);size=encoder.embedding_size;revision=encoder.revision
    del encoder
    if args.device.startswith("cuda"):torch.cuda.empty_cache()
    np.savez_compressed(output/"frozen_features.npz",embedding=emb)
    numeric=np.asarray([r["numeric"] for r in rows],dtype=np.float32)
    mean=numeric[train_ids].mean(axis=0);std=np.maximum(numeric[train_ids].std(axis=0),1e-3)
    numeric=(numeric-mean)/std
    action=np.asarray([r["action_label"] for r in rows],dtype=np.int64)
    active=np.asarray([r["active_label"] for r in rows],dtype=np.float32)
    positive=float(active[train_ids].sum());negative=len(train_ids)-positive
    active_trained=positive>0 and negative>0
    weight=min(30,negative/max(1,positive))
    model=CombatHead(size,numeric.shape[1]).to(args.device)
    optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=1e-3)
    bce=torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(weight,device=args.device))
    def tensors(ids):return TensorDataset(torch.from_numpy(emb[ids]),torch.from_numpy(numeric[ids]),
                                          torch.from_numpy(action[ids]),torch.from_numpy(active[ids]))
    def evaluate(ids):
        if not ids:return {"samples":0}
        model.eval();correct=0;loss_sum=0;pred_pos=true_pos=gold_pos=0
        with torch.no_grad():
            for e,n,a,u in DataLoader(tensors(ids),batch_size=256):
                e,n,a,u=[x.to(args.device) for x in (e,n,a,u)]
                logits,active_logits=model(e,n)
                loss_sum+=torch.nn.functional.cross_entropy(logits,a,reduction="sum").item()
                correct+=(logits.argmax(-1)==a).sum().item()
                predicted=active_logits.sigmoid()>0.8
                pred_pos+=predicted.sum().item();gold_pos+=(u>0.5).sum().item();true_pos+=(predicted&(u>0.5)).sum().item()
        return {"samples":len(ids),"action_accuracy":correct/len(ids),"action_nll":loss_sum/len(ids),
                "active_precision":true_pos/max(1,pred_pos),"active_recall":true_pos/max(1,gold_pos)}
    best=float("inf");history=[]
    for epoch in range(args.epochs):
        model.train()
        for e,n,a,u in DataLoader(tensors(train_ids),batch_size=128,shuffle=True):
            e,n,a,u=[x.to(args.device) for x in (e,n,a,u)]
            logits,active_logits=model(e,n)
            loss=torch.nn.functional.cross_entropy(logits,a)
            if active_trained:loss=loss+0.25*bce(active_logits,u)
            optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
        metrics=evaluate(val_ids);history.append(metrics)
        print(f"epoch {epoch+1}: {metrics}",flush=True)
        if metrics["action_nll"]<best:
            best=metrics["action_nll"]
            save_file({k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()},str(output/"head.safetensors"))
    from safetensors.torch import load_file
    model.load_state_dict(load_file(str(output/"head.safetensors")))
    config={**contract(),"model":args.model,"revision":revision,"embedding_size":size,"numeric_size":numeric.shape[1],
            "head_hidden_size":256,"max_length":args.max_length,"dtype":"bfloat16","hold_frames":3,
            "active_head_trained":active_trained,"active_threshold":0.8,
            "active_item_ids":sorted({rows[i]["active_item_id"] for i in train_ids if rows[i]["active_label"]}),
            "numeric_names":rows[0]["numeric_names"]}
    (output/"policy_config.json").write_text(json.dumps(config,indent=2),encoding="utf-8")
    (output/"normalization.json").write_text(json.dumps({"mean":mean.tolist(),"std":std.tolist()}),encoding="utf-8")
    (output/"action_map.json").write_text(json.dumps(action_map(),indent=2),encoding="utf-8")
    (output/"evaluation.json").write_text(json.dumps({"validation":evaluate(val_ids),"test":evaluate(test_ids),"history":history,
        "active_positive_train":positive,"scope":"Offline imitation metrics, not live clear-room performance"},indent=2),encoding="utf-8")
    files=["head.safetensors","policy_config.json","normalization.json","action_map.json","evaluation.json"]
    (output/"manifest.json").write_text(json.dumps({f:hashlib.sha256((output/f).read_bytes()).hexdigest() for f in files},indent=2),encoding="utf-8")

async def infer(args):
    torch,np,_,load_file=_dependencies()
    from .model import FrozenQwen,CombatHead
    root=Path(args.checkpoint)
    cfg=json.loads((root/"policy_config.json").read_text(encoding="utf-8"))
    if any(cfg.get(k)!=v for k,v in contract().items()):raise ValueError("Checkpoint rules/features differ from this runtime")
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    for file,digest in manifest.items():
        if (root/file).resolve().parent!=root.resolve():raise ValueError("Invalid manifest path")
        if hashlib.sha256((root/file).read_bytes()).hexdigest()!=digest:raise ValueError(f"Checksum failed: {file}")
    encoder=FrozenQwen(cfg["model"],cfg["revision"],args.device,cfg["dtype"],cfg["max_length"])
    head=CombatHead(cfg["embedding_size"],cfg["numeric_size"],cfg["head_hidden_size"]).to(args.device)
    head.load_state_dict(load_file(str(root/"head.safetensors")));head.eval()
    norm=json.loads((root/"normalization.json").read_text(encoding="utf-8"))
    mean,std=np.asarray(norm["mean"]),np.asarray(norm["std"])
    # Warm up before taking control; the first kernel invocation must not delay a live AI action.
    from .mock import sample_frame
    encoder.encode([policy_observation(sample_frame())[0]])
    await request_control({"command":"set_mode","mode":args.mode},port=args.port)
    await asyncio.sleep(0.1)
    previous=None
    try:
        while True:
            reply=await request_control({"command":"get_frame"},port=args.port);frame=reply.get("frame")
            if not frame or not reply["status"].get("connected"):raise RuntimeError("Isaac disconnected")
            if frame["mode"]!=args.mode:print("Human takeover / mode changed; inference stopped.");break
            key=(frame["run_id"],frame["game_frame"])
            if key==previous:await asyncio.sleep(0.01);continue
            previous=key
            if frame["player"].get("dead") or frame["room"].get("clear"):
                await asyncio.sleep(0.05);continue
            prompt,numeric,names=policy_observation(frame)
            if names!=cfg["numeric_names"]:raise ValueError("numeric feature order mismatch")
            start=time.perf_counter()
            with torch.no_grad():
                logits,active_logits=head(encoder.encode([prompt]),torch.tensor((np.asarray(numeric)-mean)/std,dtype=torch.float32,device=args.device).unsqueeze(0))
                probabilities=logits.softmax(-1)[0].cpu().tolist()
                active_probability=active_logits.sigmoid().item()
            active=frame["player"].get("active",{})
            use=bool(cfg["active_head_trained"] and active.get("ready") and active.get("id") in cfg["active_item_ids"] and active_probability>=cfg["active_threshold"])
            action=Action(max(range(45),key=lambda i:probabilities[i]),use,cfg["hold_frames"])
            result=await request_control(action.as_command(frame),port=args.port)
            print(json.dumps({"frame":key,"action":action.action_id,"active_probability":round(active_probability,3),
                "use_active":use,"model_ms":round((time.perf_counter()-start)*1000,1),"applied":result.get("ok")}),flush=True)
    finally:
        await request_control({"command":"set_mode","mode":"manual"},port=args.port)
