"""Serial nine-way study: immutable data, shared text caches, all epoch heads, and deployable contracts."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.experiments import EXPERIMENTS,PREFIX,transform_prompt
from isaac_jev.features import contract
from isaac_jev.quality_labels import branch_weights


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False),encoding='utf-8');temporary.replace(path)


def condition_prompt(prompt,condition):
    return prompt.rsplit('\nDesired outcome condition:',1)[0]+'\nDesired outcome condition: '+condition+'.\nChoose combat controls.'


class ExistingService:
    """Temporarily release the shared GPU for new frozen text views, then restore the exact existing config."""
    def __init__(self,base):self.base=Path(base);self.paused=False;self.checkpoint=None
    def rpc(self,command):
        with socket.create_connection(('127.0.0.1',19528),timeout=5) as sock:
            sock.sendall((json.dumps({'command':command})+'\n').encode())
            return json.loads(sock.makefile('rb').readline())
    def pause(self):
        reply=self.rpc('describe');self.checkpoint=reply['backend']['checkpoint']
        pid=int((self.base/'.local/policy-service.pid').read_text());proc=Path(f'/proc/{pid}')
        assert (proc/'cwd').resolve()==self.base.resolve()
        assert any(x.endswith(b'scripts/policy_rpc.py') for x in (proc/'cmdline').read_bytes().split(b'\0'))
        assert self.rpc('shutdown').get('shutdown');self.paused=True
        deadline=time.monotonic()+30
        while proc.exists() and time.monotonic()<deadline:time.sleep(.2)
        if proc.exists():raise RuntimeError('Existing model process did not stop')
    def restore(self):
        if not self.paused:return
        subprocess.run([str(self.base/'.venv/bin/python'),'scripts/start_policy_service.py'],cwd=self.base,check=True)
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            try:
                reply=self.rpc('describe')
                if reply['backend']['checkpoint']==self.checkpoint:self.paused=False;return
            except (OSError,ValueError,KeyError):pass
            time.sleep(1)
        raise RuntimeError('Existing epoch7 service did not restore')


def run(args):
    os.environ.setdefault('TRITON_CACHE_DIR',str(Path(args.source)/'.cache/triton'))
    import numpy as np
    import torch
    from safetensors.torch import load_file,save_file
    from torch.utils.data import DataLoader,TensorDataset
    from isaac_jev.model import FrozenQwen,head_from_config,decode_actions
    from isaac_jev.training import previous_action_label
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    source=Path(args.source);output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    status_path=output/'status.json'
    state={'state':'preparing','experiments':{},'epochs_per_experiment':args.epochs,'started_at_unix':time.time()}
    def report(stage,**details):
        state.update(state=stage,**details,updated_at_unix=time.time());atomic_json(status_path,state)
        print(json.dumps({'stage':stage,**details},ensure_ascii=False),flush=True)
    cfg=json.loads((source/'artifacts/combat-v005/policy_config.json').read_text())
    samples=source/'datasets/combat-v005/samples.jsonl'
    assert digest(samples)==cfg['dataset_sha256']
    rows=[json.loads(line) for line in samples.read_text().splitlines()]
    annotations=[json.loads(line) for line in Path(args.annotations).read_text().splitlines()]
    assert len(rows)==len(annotations)
    for i,(row,ann) in enumerate(zip(rows,annotations)):
        assert all(row[k]==ann[k] for k in ('run_id','room_id','game_frame','action_frame','action_label','split','outcome')),(i,'annotation order mismatch')
    annotations_sha=digest(args.annotations)
    source_cache=np.load(source/'artifacts/combat-v005/frozen_features.npz')
    base_emb=source_cache['embedding'];base_dep=source_cache['deployment_embedding']
    assert base_emb.shape==base_dep.shape==(len(rows),cfg['embedding_size'])
    numeric=np.asarray([r['numeric'] for r in rows],dtype=np.float32)
    deployment_numeric=np.asarray([r['deployment_numeric'] for r in rows],dtype=np.float32)
    action=np.asarray([r['action_label'] for r in rows],dtype=np.int64)
    previous=np.asarray([previous_action_label(r) for r in rows],dtype=np.int64)
    train_ids=[i for i,r in enumerate(rows) if r['split']=='train']
    val_ids=[i for i,r in enumerate(rows) if r['split']=='validation' and r['outcome']['label']=='positive']
    test_ids=[i for i,r in enumerate(rows) if r['split']=='test' and r['outcome']['label']=='positive']
    original_eval=json.loads((source/'artifacts/combat-v005/evaluation.json').read_text())
    groups={i:('boss' if annotations[i].get('room_type')==5 else 'ordinary')+('/near' if annotations[i]['gap_start_estimate']<24 else '/far') for i in val_ids+test_ids}
    names=rows[0]['numeric_names'];assert names==cfg['numeric_names']
    cache_root=output/'feature_caches';cache_root.mkdir(exist_ok=True)
    embeddings={'full':(base_emb,base_dep)}
    service=ExistingService(args.service_root)
    encoder=None
    try:
        report('preflight',samples=len(rows),annotation_sha256=annotations_sha)
        os.environ.update(HF_HUB_CACHE=str(source/'.cache/huggingface/hub'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
        # The original model's CPU initialization consumes RNG; preserve its state for a reproducible E01.
        torch.manual_seed(42)
        if args.encoder=='rpc':
            from isaac_jev.rpc_features import PolicyFeatureEncoder
            encoder=PolicyFeatureEncoder(cfg)
            service.checkpoint=encoder.backend
            torch.manual_seed(42)
            original_to=torch.nn.Module.to
            def cpu_initialization(module,*a,**k):
                return module if a and str(a[0]).startswith('cuda') else original_to(module,*a,**k)
            torch.nn.Module.to=cpu_initialization
            try:temporary=FrozenQwen(cfg['model'],cfg['revision'],'cuda',cfg['dtype'],cfg['max_length'])
            finally:torch.nn.Module.to=original_to
            del temporary
            import gc
            gc.collect()
            report('extracting_features',shared_encoder=service.checkpoint)
        else:
            service.pause();report('extracting_features',service_temporarily_paused=service.checkpoint)
            encoder=FrozenQwen(cfg['model'],cfg['revision'],'cuda',cfg['dtype'],cfg['max_length'])
        initial_cpu_rng=torch.get_rng_state().clone()
        initial_cuda_rng=torch.cuda.get_rng_state().clone()
        def extract(prompts,ids,target,stage):
            ids=sorted(ids,key=lambda i:len(prompts[i]))
            lengths=[len(encoder.tokenizer.encode(prompts[i])) for i in ids]
            if lengths and max(lengths)>cfg['max_length']:raise ValueError(f'{stage}: prompt token budget exceeded ({max(lengths)})')
            report('extracting_features',view=stage,completed=0,total=len(ids),longest_tokens=max(lengths,default=0))
            started=time.monotonic()
            for start in range(0,len(ids),args.feature_batch_size):
                batch=ids[start:start+args.feature_batch_size]
                target[batch]=encoder.encode([prompts[i] for i in batch],pad_to_multiple_of=512).cpu().to(torch.float16).numpy()
                done=start+len(batch)
                if done%256<args.feature_batch_size or done==len(ids):
                    report('extracting_features',view=stage,completed=done,total=len(ids),elapsed_seconds=round(time.monotonic()-started,1))
        short_labels=[a['short_outcome']['label'] for a in annotations]
        short_emb=base_emb.copy()
        changed=[i for i,r in enumerate(rows) if short_labels[i]!=r['outcome']['label']]
        short_prompts=[condition_prompt(r['prompt'],short_labels[i]) for i,r in enumerate(rows)]
        # Reuse known positive validation/test features where their prompts match exactly.
        reuse=[i for i in changed if rows[i]['split']!='train' and short_labels[i]=='positive']
        short_emb[reuse]=base_dep[reuse]
        reuse_set=set(reuse)
        extract(short_prompts,[i for i in changed if i not in reuse_set],short_emb,'short12-relabelled')
        embeddings['short12']=(short_emb,base_dep)
        for scheme in ('focused','tactical'):
            paths=(cache_root/(scheme+'-training.npy'),cache_root/(scheme+'-deployment.npy'))
            manifest=cache_root/(scheme+'-manifest.json')
            prompts=[transform_prompt(r['prompt'],r['numeric'],names,scheme) for r in rows]
            dep_prompts=[condition_prompt(p,'positive') for p in prompts]
            lineage={'source_dataset_sha256':cfg['dataset_sha256'],'prompt_scheme':scheme,'contract':contract(4,False,PREFIX+('e05' if scheme=='focused' else 'e06'))}
            valid=manifest.exists() and json.loads(manifest.read_text()).get('lineage')==lineage
            if valid:
                saved=json.loads(manifest.read_text());valid=all(p.exists() and digest(p)==saved['files'][p.name] for p in paths)
            if not valid:
                partial=cache_root/(scheme+'-partial.json')
                resumable=partial.exists() and json.loads(partial.read_text()).get('lineage')==lineage and all(p.exists() for p in paths)
                if resumable:
                    emb=np.load(paths[0],mmap_mode='r+');dep=np.load(paths[1],mmap_mode='r+')
                    assert emb.shape==dep.shape==base_emb.shape and emb.dtype==dep.dtype==np.float16
                else:
                    emb=np.lib.format.open_memmap(paths[0],mode='w+',dtype=np.float16,shape=base_emb.shape)
                    dep=np.lib.format.open_memmap(paths[1],mode='w+',dtype=np.float16,shape=base_emb.shape)
                    atomic_json(partial,{'lineage':lineage,'origin':'this-suite','batch_size':args.feature_batch_size})
                ready=np.isfinite(emb).all(axis=1)&np.any(emb!=0,axis=1)
                missing=np.flatnonzero(~ready).tolist()
                report('extracting_features',view=scheme,resumed_rows=int(ready.sum()),remaining_rows=len(missing),batch_size=args.feature_batch_size)
                extract(prompts,missing,emb,scheme)
                dep[:]=emb
                alternate=[i for i,r in enumerate(rows) if r['split']!='train' and dep_prompts[i]!=prompts[i]]
                extract(dep_prompts,alternate,dep,scheme+'-deployment-positive')
                emb.flush();dep.flush()
                atomic_json(manifest,{'lineage':lineage,'files':{p.name:digest(p) for p in paths},
                    'extraction':{'resumed_rows':int(ready.sum()),'batch_size_for_new_rows':args.feature_batch_size,
                                  'partial_resume_provenance':json.loads(partial.read_text())}})
                del emb,dep
            embeddings[scheme]=tuple(np.load(p,mmap_mode='r') for p in paths)
        del encoder;encoder=None;torch.cuda.empty_cache()
        service.restore();report('service_restored',checkpoint=service.checkpoint)
    finally:
        if encoder is not None:del encoder;torch.cuda.empty_cache()
        service.restore()
    results=[]
    for key in args.experiments.split(','):
        if key not in EXPERIMENTS:raise ValueError('Unknown experiment '+key)
        spec=EXPERIMENTS[key];profile=contract(4,False,PREFIX+key)
        destination=output/('combat-v006-'+key)
        if (destination/'suite-result.json').exists():
            result=json.loads((destination/'suite-result.json').read_text());results.append(result);continue
        destination.mkdir(exist_ok=True)
        emb,dep=embeddings['short12' if spec['label_scheme']=='short12' else spec['prompt_scheme']]
        observed_numeric=numeric.copy()
        if spec['label_scheme']=='short12':
            for condition in ('positive','negative','unknown'):
                observed_numeric[:,names.index('condition_'+condition)]=np.asarray([c==condition for c in short_labels])
        weights=np.asarray([branch_weights(a,r['outcome'],spec['label_scheme']) for a,r in zip(annotations,rows)],dtype=np.float32)
        model_cfg={**cfg,**profile,'dataset_sha256':cfg['dataset_sha256'],'annotations_sha256':annotations_sha,
                   'checkpoint_retention':'all-epochs','outcome_window_frames':12 if spec['label_scheme']=='short12' else 30,
                   'source_checkpoint':'combat-v005','selection_metric':'stratified-positive-validation-joint-nll',
                   'source_model_selection':cfg['model_selection'],'model_selection':'stratified-positive-validation-joint-nll',
                   'new_feature_extraction_batch_size':args.feature_batch_size,
                   'artifact_purpose':'controlled-nine-way-imitation-experiment','experiment_specification':spec}
        torch.set_rng_state(initial_cpu_rng);torch.cuda.set_rng_state(initial_cuda_rng)
        model=head_from_config(model_cfg).cuda();optimizer=torch.optim.AdamW(model.parameters(),lr=1e-3,weight_decay=1e-3)
        def tensors(ids,deployment=False):
            e,n=(dep,deployment_numeric) if deployment else (emb,observed_numeric)
            return TensorDataset(torch.from_numpy(np.asarray(e[ids])),torch.from_numpy(n[ids]),torch.from_numpy(action[ids]),
                                 torch.from_numpy(previous[ids]),torch.from_numpy(weights[ids]),torch.tensor(ids))
        def evaluate(ids,deployment=True):
            model.eval();total=move_total=shoot_total=correct_m=correct_s=stays=idle=start_total=start_correct=0
            group_loss=defaultdict(list);prediction_counts=Counter()
            with torch.no_grad():
                for e,n,a,p,w,indices in DataLoader(tensors(ids,deployment),batch_size=256):
                    e,n,a,p=[x.cuda() for x in (e,n,a,p)]
                    ml,sl,_=model(e,n)
                    m_loss=torch.nn.functional.cross_entropy(ml,a//5,reduction='none')
                    s_loss=torch.nn.functional.cross_entropy(sl,a%5,reduction='none')
                    move_total+=m_loss.sum().item();shoot_total+=s_loss.sum().item()
                    # Match the original sum order for the reproducibility check.
                    total+=(torch.nn.functional.cross_entropy(ml,a//5,reduction='sum')+torch.nn.functional.cross_entropy(sl,a%5,reduction='sum')).item()
                    m,s=decode_actions(model,ml,sl);correct_m+=(m==a//5).sum().item();correct_s+=(s==a%5).sum().item()
                    stays+=(m==0).sum().item();idle+=((m==0)&(s==0)).sum().item();prediction_counts.update((m*5+s).cpu().tolist())
                    starts=(p//5==0)&(a//5!=0);start_total+=starts.sum().item();start_correct+=((m!=0)&starts).sum().item()
                    for index,loss in zip(indices.tolist(),(m_loss+s_loss).cpu().tolist()):group_loss[groups.get(index,'train')].append(loss)
            per_group={g:sum(x)/len(x) for g,x in group_loss.items()}
            return {'samples':len(ids),'action_nll':total/len(ids),'move_nll':move_total/len(ids),'shoot_nll':shoot_total/len(ids),
                    'move_accuracy':correct_m/len(ids),'shoot_accuracy':correct_s/len(ids),'stay_fraction':stays/len(ids),
                    'idle_fraction':idle/len(ids),'move_start_recall':start_correct/start_total if start_total else None,
                    'move_start_samples':start_total,'stratified_nll':sum(per_group.values())/len(per_group),'group_nll':per_group,
                    'prediction_counts':dict(prediction_counts)}
        history=[];best=float('inf');best_epoch=None;original_check=[]
        report('training',experiment=key,epoch=0,total_epochs=args.epochs)
        for epoch in range(1,args.epochs+1):
            model.train()
            for e,n,a,p,w,indices in DataLoader(tensors(train_ids),batch_size=128,shuffle=True):
                e,n,a,p,w=[x.cuda() for x in (e,n,a,p,w)]
                ml,sl,_=model(e,n)
                mw=w[:,0]*(1+2*(a//5!=p//5));sw=w[:,1]*(1+7*(a%5!=p%5))
                loss_m=torch.nn.functional.cross_entropy(ml,a//5,reduction='none')
                loss_s=torch.nn.functional.cross_entropy(sl,a%5,reduction='none')
                loss=(loss_m*mw).sum()/mw.sum()+(loss_s*sw).sum()/sw.sum()
                optimizer.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
            val=evaluate(val_ids);train=evaluate(train_ids,False)
            history.append({'epoch':epoch,'validation':val,'train':train})
            if key=='e01' and epoch<=7:
                old=original_eval['history'][epoch-1]['validation_selection']
                matches=all(abs(val[k]-old[k])<1e-7 for k in ('action_nll','move_accuracy','shoot_accuracy'))
                original_check.append(matches)
                if not matches:raise RuntimeError('E01 failed to reproduce the original training metrics')
                if epoch==2:
                    original=load_file(str(source/'artifacts/combat-v005/head.safetensors'))
                    if not all(torch.equal(model.state_dict()[k].cpu(),v) for k,v in original.items()):raise RuntimeError('E01 epoch2 parameters differ')
            if val['stratified_nll']<best:best=val['stratified_nll'];best_epoch=epoch
            epoch_root=destination/'checkpoints'/f'epoch-{epoch:03d}';epoch_root.mkdir(parents=True,exist_ok=True)
            save_file({k:v.detach().cpu().contiguous() for k,v in model.state_dict().items()},str(epoch_root/'head.safetensors'))
            checkpoint_cfg={**model_cfg,'checkpoint_epoch':epoch,'early_stopping':{'maximum_epochs':args.epochs,'completed_epochs':epoch,
                             'best_epoch':best_epoch,'patience':None,'strategy':'fixed-epochs-all-heads-retained'}}
            atomic_json(epoch_root/'policy_config.json',checkpoint_cfg)
            for filename in ('normalization.json','action_map.json'):shutil.copy2(source/'artifacts/combat-v005'/filename,epoch_root/filename)
            atomic_json(epoch_root/'evaluation.json',{'epoch':epoch,'validation':val,'scope':'Validation only; no test-driven checkpoint selection'})
            files=('head.safetensors','policy_config.json','normalization.json','action_map.json','evaluation.json')
            atomic_json(epoch_root/'manifest.json',{f:digest(epoch_root/f) for f in files})
            torch.save({'epoch':epoch,'optimizer':optimizer.state_dict(),'cpu_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state(),
                        'dataset_sha256':cfg['dataset_sha256'],'annotations_sha256':annotations_sha},destination/'last_training_state.pt')
            atomic_json(destination/'history.json',history)
            state['experiments'][key]={'completed_epochs':epoch,'best_epoch':best_epoch,'best_score':best}
            report('training',experiment=key,epoch=epoch,total_epochs=args.epochs,validation=val)
        best_root=destination/'checkpoints'/f'epoch-{best_epoch:03d}'
        for file in best_root.iterdir():
            if file.is_file():shutil.copy2(file,destination/file.name)
        selected_cfg=json.loads((destination/'policy_config.json').read_text())
        selected_cfg['early_stopping']['completed_epochs']=args.epochs
        selected_cfg['early_stopping']['best_epoch']=best_epoch
        atomic_json(destination/'policy_config.json',selected_cfg)
        atomic_json(destination/'manifest.json',{f:digest(destination/f) for f in ('head.safetensors','policy_config.json','normalization.json','action_map.json','evaluation.json')})
        model.load_state_dict(load_file(str(destination/'head.safetensors')))
        tested=evaluate(test_ids)
        result={'id':key,**spec,'epochs':args.epochs,'selected_epoch':best_epoch,'validation':history[best_epoch-1]['validation'],
                'test_on_observed_positive':tested,'e01_original_first7_identical':all(original_check) if key=='e01' else None,
                'checkpoint':str(destination),'all_epochs':str(destination/'checkpoints'),'scope':'Offline imitation; live performance to be tested by user'}
        atomic_json(destination/'suite-result.json',result);results.append(result)
        del model,optimizer;torch.cuda.empty_cache()
        atomic_json(output/'results.json',{'experiments':results,'selection_uses_test':False,'epochs_per_experiment':args.epochs})
    report('complete',completed_experiments=len(results),service_checkpoint=service.checkpoint)


if __name__=='__main__':
    if os.name!='nt':
        import faulthandler
        import signal
        faulthandler.enable()
        faulthandler.register(signal.SIGUSR1,file=sys.stderr,all_threads=True)
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('/xufanghui/isaac-jev-v005'))
    p.add_argument('--annotations',type=Path,required=True);p.add_argument('--output',type=Path,default=ROOT/'artifacts/combat-v006')
    p.add_argument('--service-root',type=Path,default=Path('/xufanghui/isaac-jev'));p.add_argument('--epochs',type=int,default=30)
    p.add_argument('--feature-batch-size',type=int,default=1);p.add_argument('--experiments',default=','.join(EXPERIMENTS))
    p.add_argument('--encoder',choices=['rpc','dedicated'],default='rpc')
    a=p.parse_args()
    if a.epochs<7 or a.feature_batch_size<1:p.error('Use at least seven epochs and a positive feature batch size')
    run(a)
