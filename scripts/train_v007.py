"""Serial S01/S02 study: shared frozen state features, ordinal feedback, all heads retained."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.features import contract
from isaac_jev.features_v007 import PREFIX,CANDIDATE_FIELDS,option_texts


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)


def run(args):
    import numpy as np
    import torch
    from safetensors.torch import save_file,load_file
    from torch.utils.data import DataLoader,TensorDataset
    from isaac_jev.model import head_from_config,decode_actions,TextOptionCombatHead,load_extended_numeric_state
    from isaac_jev.rpc_features import PolicyFeatureEncoder
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    source=args.source;dataset=args.dataset;output=args.output;output.mkdir(parents=True,exist_ok=True)
    status={'stage':'preflight','epochs_per_group':args.epochs,'groups':{},'started_at':time.time()}
    def report(stage,**data):
        status.update(stage=stage,**data,updated_at=time.time());write_json(output/'status.json',status)
        print(json.dumps({'stage':stage,**data},ensure_ascii=False),flush=True)
    meta=json.loads((dataset/'dataset.json').read_text());sample_path=dataset/'samples.jsonl'
    assert digest(sample_path)==meta['samples_sha256']
    rows=[json.loads(line) for line in sample_path.open(encoding='utf-8')]
    names=json.loads((dataset/'numeric_names.json').read_text());options=json.loads((dataset/'option_texts.json').read_text())
    assert options==option_texts()
    base=json.loads((source/'artifacts/combat-v005/policy_config.json').read_text())
    os.environ.update(HF_HUB_CACHE=str(source/'.cache/huggingface/hub'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    cache=output/'features';cache.mkdir(exist_ok=True)
    encoder=PolicyFeatureEncoder(base)
    lengths=[len(encoder.tokenizer.encode(row['prompt'])) for row in rows]
    write_json(output/'token-lengths.json',{'samples':len(rows),'min':min(lengths),'max':max(lengths),
        'over_budget':sum(length>base['max_length'] for length in lengths),'longest_rows':sorted(range(len(rows)),key=lambda i:lengths[i],reverse=True)[:10]})
    assert max(lengths)<=base['max_length'],('Prompt token budget exceeded',max(lengths))
    report('extracting_features',total=len(rows),completed=0,max_tokens=max(lengths),shared_encoder=encoder.backend)
    lineage={'dataset_sha256':meta['samples_sha256'],'revision':base['revision'],'rules_sha256':contract(4,False,args.feature_profile or PREFIX+'s01')['rules_sha256']}
    feature_path=cache/'state.npy';progress_path=cache/'done.npy';lineage_path=cache/'lineage.json'
    if lineage_path.exists():
        assert json.loads(lineage_path.read_text())==lineage
        features=np.load(feature_path,mmap_mode='r+');done=np.load(progress_path,mmap_mode='r+')
    else:
        features=np.lib.format.open_memmap(feature_path,mode='w+',dtype=np.float16,shape=(len(rows),base['embedding_size']))
        done=np.lib.format.open_memmap(progress_path,mode='w+',dtype=np.bool_,shape=(len(rows),));done[:]=False;done.flush()
        write_json(lineage_path,lineage)
    if args.reuse_features_from is not None and not done.all():
        parent=args.reuse_features_from
        assert digest(args.reuse_dataset)==meta['parent_dataset_sha256']
        parent_rows=[json.loads(line) for line in args.reuse_dataset.open()]
        parent_lineage=json.loads((parent/'lineage.json').read_text())
        assert parent_lineage['dataset_sha256']==meta['parent_dataset_sha256']
        assert parent_lineage['revision']==lineage['revision'] and parent_lineage['rules_sha256']==lineage['rules_sha256']
        assert np.load(parent/'done.npy').all()
        previous_features=np.load(parent/'state.npy',mmap_mode='r')
        for i,row in enumerate(rows):
            old=parent_rows[row['original_row_index']]
            assert all(row[key]==old[key] for key in ('prompt','numeric','action_label','split'))
            features[i]=previous_features[row['original_row_index']]
        features.flush();done[:]=True;done.flush()
        shutil.copy2(parent/'option.npy',cache/'option.npy')
        report('reusing_verified_features',completed=len(rows),total=len(rows),parent_dataset_sha256=meta['parent_dataset_sha256'])
    order=sorted((i for i in range(len(rows)) if not done[i]),key=lambda i:lengths[i]);started=time.monotonic()
    for count,index in enumerate(order,1):
        value=encoder.encode([rows[index]['prompt']]).numpy()[0]
        assert np.isfinite(value).all() and np.any(value!=0)
        features[index]=value
        if count%128==0 or count==len(order):
            features.flush()
            for processed in order[max(0,count-128):count]:done[processed]=True
            done.flush()
            report('extracting_features',completed=int(done.sum()),total=len(rows),elapsed_seconds=round(time.monotonic()-started,1))
    assert done.all()
    option_path=cache/'option.npy'
    if option_path.exists():option_embeddings=np.load(option_path)
    else:
        option_embeddings=encoder.encode(options).numpy();np.save(option_path,option_embeddings)
    assert option_embeddings.shape==(45,base['embedding_size'])
    write_json(cache/'manifest.json',{'lineage':lineage,'state_sha256':digest(feature_path),'option_sha256':digest(option_path),'encoder':encoder.backend})
    del encoder
    numeric=np.asarray([row['numeric'] for row in rows],dtype=np.float32)
    actions=np.asarray([row['action_label'] for row in rows],dtype=np.int64)
    previous=np.asarray([row['previous_action_label'] for row in rows],dtype=np.int64)
    grades=np.asarray([row['quality']['grades'] for row in rows],dtype=np.int64)
    confidence=np.asarray([row['quality']['confidence'] for row in rows],dtype=np.float32)
    weights=np.asarray([[row['quality']['move_weight'],row['quality']['shoot_weight']] for row in rows],dtype=np.float32)
    splits={split:np.asarray([i for i,row in enumerate(rows) if row['split']==split],dtype=np.int64) for split in ('train','validation','test')}
    assert {key:len(value) for key,value in splits.items()}==meta['counts']
    mean=numeric[splits['train']].mean(0);std=numeric[splits['train']].std(0);std=np.where(std<1e-4,1,std)
    if args.initialize_from is not None:
        original_norm=json.loads((args.initialize_from/'normalization.json').read_text())
        mean=np.asarray(original_norm['mean'],dtype=np.float32);std=np.asarray(original_norm['std'],dtype=np.float32)
        if len(mean)<len(names):
            assert args.transfer_input_contract
            mean=np.concatenate((mean,np.zeros(len(names)-len(mean),dtype=np.float32)))
            std=np.concatenate((std,np.ones(len(names)-len(std),dtype=np.float32)))
    normalized=(numeric-mean)/std
    candidate_indices=[[names.index(f'option_{action}_{field}') for field in CANDIDATE_FIELDS] for action in range(45)]
    norm={'mean':mean.tolist(),'std':std.tolist(),'fitted_on':'initialization_training_split' if args.initialize_from else 'training_split_only'}
    all_results=[]
    for key in args.groups:
        destination=output/('combat-v007-'+key);destination.mkdir(exist_ok=True)
        if (destination/'result.json').exists():
            all_results.append(json.loads((destination/'result.json').read_text()));continue
        profile=args.feature_profile or PREFIX+key
        cfg={**base,**contract(4,False,profile),'semantic_experiment_id':key,'numeric_size':len(names),'numeric_names':names,
             'candidate_numeric_indices':candidate_indices,'dataset_sha256':meta['samples_sha256'],
             'source_dataset_sha256':meta['source_sha256'],'head_hidden_size':256,'active_context':False,'active_head_present':False,
             'active_head_trained':False,'checkpoint_retention':'all_epochs','training_objective':'weighted imitation + masked ordinal auxiliary',
             'selection_metric':'stratified_quality_weighted_validation_nll','training_conditions':['safe_progress'],
             'outcome_window_frames':60,'option_encoding':'same frozen Qwen once per static option; dynamic relations in candidate facts',
             'quality_loss_weight':.1,'joint_loss_weight':.25 if key=='s02' else 0.,
             'source_reference_commit':'6b3b90ed419c91e9741690b551ca4093e2a9206e'}
        torch.manual_seed(42);torch.cuda.manual_seed_all(42)
        model=head_from_config(cfg).cuda()
        if isinstance(model,TextOptionCombatHead):model.option_embeddings.copy_(torch.from_numpy(option_embeddings).cuda())
        if args.initialize_from is not None:
            init_cfg=json.loads((args.initialize_from/'policy_config.json').read_text())
            assert init_cfg['head_architecture']==cfg['head_architecture']
            assert args.transfer_input_contract or init_cfg['rules_sha256']==cfg['rules_sha256']
            for name,expected in json.loads((args.initialize_from/'manifest.json').read_text()).items():
                assert digest(args.initialize_from/name)==expected
            init_state=load_file(str(args.initialize_from/'head.safetensors'))
            if args.transfer_input_contract:
                assert cfg['numeric_names'][:init_cfg['numeric_size']]==init_cfg['numeric_names']
                load_extended_numeric_state(model,init_state,init_cfg['numeric_size'])
            else:model.load_state_dict(init_state)
            cfg['initialized_from']={'checkpoint':str(args.initialize_from),'head_sha256':digest(args.initialize_from/'head.safetensors')}
        cfg['training_run_id']=args.run_label;cfg['learning_rate']=args.learning_rate
        optimizer=torch.optim.AdamW(model.parameters(),lr=args.learning_rate,weight_decay=1e-3)
        trainable=sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
        cfg['trainable_parameters']=trainable
        def loader(ids,shuffle=False):
            return DataLoader(TensorDataset(torch.from_numpy(np.asarray(features[ids])),torch.from_numpy(normalized[ids]),
                torch.from_numpy(actions[ids]),torch.from_numpy(previous[ids]),torch.from_numpy(weights[ids]),
                torch.from_numpy(grades[ids]),torch.from_numpy(confidence[ids]),torch.from_numpy(ids)),batch_size=128,shuffle=shuffle)
        def branch_logits(first,second):
            if key=='s02':
                joint=first.reshape(-1,9,5)
                return joint.logsumexp(2),joint.logsumexp(1)
            return first,second
        def evaluate(ids):
            model.eval();m_correct=s_correct=joint_correct=stays=idle=0;ml_sum=sl_sum=0.;weighted_m=weighted_s=wm_sum=ws_sum=0.
            group_values=defaultdict(lambda:[0.,0.,0.,0.]);quality_loss=quality_weight=0.;predictions=Counter()
            with torch.no_grad():
                for e,n,a,p,w,g,c,indices in loader(ids):
                    e,n,a,w,g,c=[value.cuda() for value in (e,n,a,w,g,c)]
                    first,second,_=model(e,n);ml,sl=branch_logits(first,second)
                    m,s=decode_actions(model,first,second)
                    lm=torch.nn.functional.cross_entropy(ml,a//5,reduction='none');ls=torch.nn.functional.cross_entropy(sl,a%5,reduction='none')
                    ml_sum+=lm.sum().item();sl_sum+=ls.sum().item()
                    weighted_m+=(lm*w[:,0]).sum().item();wm_sum+=w[:,0].sum().item()
                    weighted_s+=(ls*w[:,1]).sum().item();ws_sum+=w[:,1].sum().item()
                    m_correct+=(m==a//5).sum().item();s_correct+=(s==a%5).sum().item();joint_correct+=((m*5+s)==a).sum().item()
                    stays+=(m==0).sum().item();idle+=((m==0)&(s==0)).sum().item();predictions.update((m*5+s).cpu().tolist())
                    q=model.quality_logits(e,n,a)
                    ql=torch.nn.functional.cross_entropy(q.reshape(-1,5),g.clamp_min(0).reshape(-1),reduction='none').reshape(-1,7)
                    mask=c*(g>=0);quality_loss+=(ql*mask).sum().item();quality_weight+=mask.sum().item()
                    for index,x,y,weight in zip(indices.tolist(),lm.cpu().tolist(),ls.cpu().tolist(),w.cpu().tolist()):
                        row=rows[index];group='boss' if row['room_type']==5 else 'ordinary'
                        values=group_values[group];values[0]+=x*weight[0];values[1]+=weight[0];values[2]+=y*weight[1];values[3]+=weight[1]
            per_group={group:values[0]/max(1e-9,values[1])+values[2]/max(1e-9,values[3]) for group,values in group_values.items()}
            return {'samples':len(ids),'move_nll':ml_sum/len(ids),'shoot_nll':sl_sum/len(ids),
                    'quality_weighted_nll':weighted_m/max(1e-9,wm_sum)+weighted_s/max(1e-9,ws_sum),
                    'selection_nll':sum(per_group.values())/len(per_group),'group_weighted_nll':per_group,
                    'move_accuracy':m_correct/len(ids),'shoot_accuracy':s_correct/len(ids),'joint_accuracy':joint_correct/len(ids),
                    'stay_fraction':stays/len(ids),'idle_fraction':idle/len(ids),'ordinal_aux_nll':quality_loss/max(1e-9,quality_weight),
                    'predicted_actions':dict(predictions),'scope':'Offline weighted imitation; no measured live survival or DPS'}
        initial_validation=evaluate(splits['validation']) if args.initialize_from else None
        if initial_validation is not None:write_json(destination/'initialization-evaluation.json',initial_validation)
        history=[];best=float('inf');best_epoch=None
        for epoch in range(1,args.epochs+1):
            model.train()
            for e,n,a,p,w,g,c,_ in loader(splits['train'],True):
                e,n,a,p,w,g,c=[value.cuda() for value in (e,n,a,p,w,g,c)]
                first,second,_=model(e,n);ml,sl=branch_logits(first,second)
                mw=w[:,0]*(1+2*(a//5!=p//5));sw=w[:,1]*(1+7*(a%5!=p%5))
                lm=torch.nn.functional.cross_entropy(ml,a//5,reduction='none');ls=torch.nn.functional.cross_entropy(sl,a%5,reduction='none')
                loss=(lm*mw).sum()/mw.sum().clamp_min(1e-9)+(ls*sw).sum()/sw.sum().clamp_min(1e-9)
                if key=='s02':
                    jw=torch.minimum(mw,sw)
                    jl=torch.nn.functional.cross_entropy(first,a,reduction='none')
                    loss=loss+.25*(jl*jw).sum()/jw.sum().clamp_min(1e-9)
                q=model.quality_logits(e,n,a)
                ql=torch.nn.functional.cross_entropy(q.reshape(-1,5),g.clamp_min(0).reshape(-1),reduction='none').reshape(-1,7)
                mask=c*(g>=0);loss=loss+.1*(ql*mask).sum()/mask.sum().clamp_min(1e-9)
                optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
            validation=evaluate(splits['validation']);history.append({'epoch':epoch,'validation':validation})
            if validation['selection_nll']<best:best=validation['selection_nll'];best_epoch=epoch
            retained=destination/'checkpoints'/f'epoch-{epoch:03d}';retained.mkdir(parents=True,exist_ok=True)
            checkpoint_cfg={**cfg,'checkpoint_epoch':epoch,'early_stopping':{'strategy':'fixed_epochs_all_heads_retained','completed_epochs':epoch,'maximum_epochs':args.epochs,'best_epoch':best_epoch}}
            if args.run_label:checkpoint_cfg['checkpoint_label']=f'{args.run_label}-{key}-epoch{epoch:03d}'
            save_file({name:value.detach().cpu().contiguous() for name,value in model.state_dict().items()},str(retained/'head.safetensors'))
            write_json(retained/'policy_config.json',checkpoint_cfg);write_json(retained/'normalization.json',norm)
            write_json(retained/'option_texts.json',options)
            shutil.copy2(source/'artifacts/combat-v005/action_map.json',retained/'action_map.json')
            write_json(retained/'evaluation.json',{'epoch':epoch,'validation':validation})
            write_json(retained/'manifest.json',{path.name:digest(path) for path in retained.iterdir() if path.is_file() and path.name!='manifest.json'})
            torch.save({'epoch':epoch,'model':model.state_dict(),'optimizer':optimizer.state_dict(),'cpu_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state()},destination/'last_training_state.pt')
            write_json(destination/'history.json',history)
            status['groups'][key]={'completed_epochs':epoch,'best_epoch':best_epoch,'selection_nll':best}
            report('training',group=key,epoch=epoch,trainable_parameters=trainable,validation=validation)
        selected=destination/'checkpoints'/f'epoch-{best_epoch:03d}'
        for path in selected.iterdir():shutil.copy2(path,destination/path.name)
        selected_cfg=json.loads((destination/'policy_config.json').read_text())
        selected_cfg['early_stopping']['completed_epochs']=args.epochs
        write_json(destination/'policy_config.json',selected_cfg)
        write_json(destination/'manifest.json',{path.name:digest(path) for path in destination.iterdir()
                   if path.name in {'head.safetensors','policy_config.json','normalization.json','action_map.json','option_texts.json','evaluation.json'}})
        for directory in [destination,*sorted((destination/'checkpoints').iterdir())]:
            for filename,expected in json.loads((directory/'manifest.json').read_text()).items():
                target=(directory/filename).resolve()
                assert target.parent==directory.resolve() and digest(target)==expected
        model.load_state_dict(load_file(str(destination/'head.safetensors')))
        test=evaluate(splits['test']);result={'group':key,'epochs':args.epochs,'selected_epoch':best_epoch,'trainable_parameters':trainable,
            'validation':history[best_epoch-1]['validation'],'test':test,'checkpoint':str(destination),'retained_heads':args.epochs,
            'selection_uses_test':False,'scope':'Offline training complete; gameplay comparison remains separate'}
        if initial_validation is not None:result['initialization_validation']=initial_validation
        write_json(destination/'result.json',result);all_results.append(result)
        # Cache/real RPC agreement and option-order invariance use validation fixtures only.
        if profile.startswith('combat-tactical-v10-'):
            from isaac_jev.features_v010 import observation
            history_key='tactical_history'
        elif profile.startswith('combat-feedback-v9-'):
            from isaac_jev.features_v009 import observation
            history_key='feedback_history'
        else:
            from isaac_jev.features_v007 import observation
            history_key='semantic_history'
        fixtures=[json.loads(line) for line in (dataset/'runtime-fixtures.jsonl').open()]
        fixtures=[f for f in fixtures if rows[f['row_index']]['split']=='validation']
        fixtures=fixtures[:32]
        encoder=PolicyFeatureEncoder(base);matches=0;cosines=[]
        model.eval()
        for fixture in fixtures:
            index=fixture['row_index'];text,values,fields=observation(fixture['frame'],history_facts=fixture[history_key],profile=profile)
            assert text==rows[index]['prompt'] and fields==names and values==rows[index]['numeric']
            live=encoder.encode([text]).cuda().float();saved=torch.from_numpy(np.asarray(features[index:index+1])).cuda().float()
            cosine=torch.nn.functional.cosine_similarity(live,saved).item();cosines.append(cosine)
            n=torch.from_numpy(normalized[index:index+1]).cuda()
            with torch.no_grad():
                first,second,_=model(live,n);m,s=decode_actions(model,first,second)
                gold_first,gold_second,_=model(saved,n);gm,gs=decode_actions(model,gold_first,gold_second)
                assert (m.item(),s.item())==(gm.item(),gs.item());matches+=1
                if key=='s02':
                    permutation=torch.randperm(45,device='cuda')
                    alternate=model.score_candidates(live,n,permutation)
                    assert torch.allclose(alternate,first[:,permutation],atol=1e-5,rtol=1e-5)
            assert cosine>.999
        del encoder
        write_json(destination/'verification.json',{'runtime_fixture_matches':matches,'live_feature_rpc_checks':len(fixtures),
                   'minimum_cosine':min(cosines),'candidate_order_invariance':key=='s02','checkpoint_manifest_checked':True,
                   'scope':'Fresh GPU encoder RPC + retained head; no game input or model switch'})
        del model,optimizer;torch.cuda.empty_cache()
        write_json(output/'results.json',{'groups':all_results,'serial_order':args.groups,'selection_uses_test':False})
    report('complete',completed_groups=len(args.groups),retained_heads=args.epochs*len(args.groups))


if __name__=='__main__':
    if os.name!='nt':
        import faulthandler,signal
        faulthandler.enable();faulthandler.register(signal.SIGUSR1,file=sys.stderr,all_threads=True)
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=Path('/xufanghui/isaac-jev-v005'))
    parser.add_argument('--dataset',type=Path,default=ROOT/'datasets/combat-v007');parser.add_argument('--output',type=Path,default=ROOT/'artifacts/combat-v007')
    parser.add_argument('--epochs',type=int,default=30)
    parser.add_argument('--groups',nargs='+',choices=('s01','s02'),default=['s01','s02'])
    parser.add_argument('--initialize-from',type=Path)
    parser.add_argument('--learning-rate',type=float,default=1e-3)
    parser.add_argument('--run-label',default='')
    parser.add_argument('--reuse-features-from',type=Path)
    parser.add_argument('--reuse-dataset',type=Path)
    parser.add_argument('--feature-profile')
    parser.add_argument('--transfer-input-contract',action='store_true')
    args=parser.parse_args()
    if args.epochs<1:parser.error('epochs must be positive')
    if args.initialize_from and len(args.groups)!=1:parser.error('Initialization requires exactly one matching group')
    if args.reuse_features_from and not args.reuse_dataset:parser.error('Feature reuse requires parent dataset rows')
    if args.learning_rate<=0:parser.error('Learning rate must be positive')
    run(args)
