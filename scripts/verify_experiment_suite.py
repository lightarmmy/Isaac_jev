"""Verify every retained artifact and sample batch=1 encoder/head agreement against the training caches."""
from collections import defaultdict
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts'))
from train_experiment_suite import ExistingService,atomic_json,digest
from isaac_jev.experiments import transform_prompt
from isaac_jev.features import contract_for


def verify():
    os.environ.setdefault('TRITON_CACHE_DIR',str(Path('/xufanghui/isaac-jev-v005/.cache/triton')))
    import numpy as np
    import torch
    from safetensors.torch import load_file
    from isaac_jev.model import FrozenQwen,head_from_config,decode_actions
    from policy_rpc import prediction_fields
    torch.set_num_threads(1);torch.set_num_interop_threads(1)
    source=Path('/xufanghui/isaac-jev-v005');suite=ROOT/'artifacts/combat-v006'
    results=json.loads((suite/'results.json').read_text())['experiments']
    assert len(results)==9
    count=0;selected=[]
    for result in results:
        root=Path(result['checkpoint'])
        retained=sorted((root/'checkpoints').glob('epoch-*'))
        assert len(retained)==30
        for folder in [root]+retained:
            cfg=json.loads((folder/'policy_config.json').read_text())
            assert all(cfg.get(k)==v for k,v in contract_for(cfg).items())
            for file,expected in json.loads((folder/'manifest.json').read_text()).items():
                assert (folder/file).resolve().parent==folder.resolve()
                assert digest(folder/file)==expected
            if folder!=root:count+=1
        cfg=json.loads((root/'policy_config.json').read_text())
        head=head_from_config(cfg).eval();head.load_state_dict(load_file(str(root/'head.safetensors')))
        selected.append((result['id'],cfg,head))
    rows=[json.loads(x) for x in (source/'datasets/combat-v005/samples.jsonl').read_text().splitlines()]
    ids=[i for i,r in enumerate(rows) if r['split'] in ('validation','test')]
    ids=[ids[int(j*(len(ids)-1)/31)] for j in range(32)]
    base=np.load(source/'artifacts/combat-v005/frozen_features.npz')['deployment_embedding']
    caches={'full':base,'focused':np.load(suite/'feature_caches/focused-deployment.npy',mmap_mode='r'),
            'tactical':np.load(suite/'feature_caches/tactical-deployment.npy',mmap_mode='r')}
    cfg=selected[0][1]
    os.environ.update(HF_HUB_CACHE=str(source/'.cache/huggingface/hub'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    service=ExistingService('/xufanghui/isaac-jev');encoder=None
    agreement=defaultdict(int);max_delta=defaultdict(float);cosines=defaultdict(list)
    try:
        from isaac_jev.rpc_features import PolicyFeatureEncoder
        encoder=PolicyFeatureEncoder(cfg)
        for scheme in ('full','focused','tactical'):
            targets=[item for item in selected if item[1]['prompt_scheme']==scheme]
            for index in ids:
                row=rows[index]
                prompt=transform_prompt(row['deployment_prompt'],row['deployment_numeric'],row['numeric_names'],scheme)
                actual=encoder.encode([prompt],pad_to_multiple_of=512).cpu().float()
                cached=torch.from_numpy(np.array(caches[scheme][index:index+1])).float()
                cosines[scheme].append(float(torch.nn.functional.cosine_similarity(actual,cached).item()))
                n=torch.tensor([row['deployment_numeric']],dtype=torch.float32)
                for key,config,head in targets:
                    payload={'prompt':prompt,'numeric':row['deployment_numeric'],**{k:config[k] for k in ('feature_version','head_architecture','rules_sha256')}}
                    prediction_fields(payload,config)
                    with torch.no_grad():
                        am,ash,_=head(actual,n);cm,csh,_=head(cached,n)
                        a_m,a_s=decode_actions(head,am,ash);c_m,c_s=decode_actions(head,cm,csh)
                        agreement[key]+=int(a_m.item()==c_m.item() and a_s.item()==c_s.item())
                        max_delta[key]=max(max_delta[key],float((am.softmax(-1)-cm.softmax(-1)).abs().max()),
                                           float((ash.softmax(-1)-csh.softmax(-1)).abs().max()))
            print(json.dumps({'verified_view':scheme,'minimum_feature_cosine':min(cosines[scheme])}),flush=True)
        assert all(min(x)>.999 for x in cosines.values()),'Batch=1 text features differ materially from cached features'
        assert all(v<.03 for v in max_delta.values()),'Head probabilities differ materially between cache and batch=1'
        result={'retained_epochs_verified':count,'selected_artifacts_verified':len(selected),'samples_per_experiment':len(ids),
                'feature_cosine_minimum':{k:min(v) for k,v in cosines.items()},
                'action_agreement':dict(agreement),'maximum_probability_delta':dict(max_delta),
                'scope':'Shared live Qwen encoder RPC plus loaded CPU heads and RPC input validation, compared with frozen training caches; no gameplay performance claim'}
        atomic_json(suite/'verification.json',result)
        print(json.dumps(result),flush=True)
    finally:
        if encoder is not None:del encoder;torch.cuda.empty_cache()
        service.restore()


if __name__=='__main__':verify()
