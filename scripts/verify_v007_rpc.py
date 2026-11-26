"""Read-only RPC compatibility checks using the existing GPU encoder and saved heads.

The temporary loopback service runs the decision head on CPU and calls the warmed
GPU encoder. It checks complete prediction payloads without switching the game policy.
Native policy_rpc.py supports the same heads for later GPU deployment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]


def rpc(payload,port,timeout=20):
    with socket.create_connection(('127.0.0.1',port),timeout=timeout) as sock:
        sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        sock.sendall((json.dumps(payload,separators=(',',':'))+'\n').encode())
        result=json.loads(sock.makefile('rb').readline())
    if not result.get('ok'):raise RuntimeError(result)
    return result


def serve(checkpoint,port):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    from isaac_jev.model import head_from_config,decode_actions
    from isaac_jev.remote import validate_description
    from isaac_jev.rpc_features import PolicyFeatureEncoder
    from scripts.policy_rpc import prediction_fields
    torch.set_num_threads(1)
    cfg=json.loads((checkpoint/'policy_config.json').read_text())
    for filename,expected in json.loads((checkpoint/'manifest.json').read_text()).items():
        path=(checkpoint/filename).resolve()
        assert path.parent==checkpoint.resolve() and hashlib.sha256(path.read_bytes()).hexdigest()==expected
    validate_description({'config':cfg})
    encoder=PolicyFeatureEncoder(cfg);head=head_from_config(cfg).eval()
    head.load_state_dict(load_file(str(checkpoint/'head.safetensors')))
    norm=json.loads((checkpoint/'normalization.json').read_text());mean=torch.tensor(norm['mean']);std=torch.tensor(norm['std'])
    stopped=threading.Event();lock=threading.Lock()
    backend={'checkpoint':cfg.get('checkpoint_label') or f"combat-v007-{cfg['semantic_experiment_id']}-epoch{cfg['checkpoint_epoch']:03d}",
             'shared_gpu_encoder':encoder.backend,'head_device':'cpu','purpose':'isolated read-only verification'}
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                try:
                    request=json.loads(line);command=request.get('command')
                    if command=='describe':response={'ok':True,'config':cfg,'backend':backend}
                    elif command=='shutdown':response={'ok':True,'shutdown':True};stopped.set()
                    elif command=='predict':
                        prompt,numeric=prediction_fields(request,cfg)
                        with lock:
                            started=time.perf_counter();embedding=encoder.encode([prompt]);encoded=time.perf_counter()
                            with torch.no_grad():
                                first,second,_=head(embedding,(torch.tensor(numeric)-mean).unsqueeze(0)/std)
                                m,s=decode_actions(head,first,second)
                            finished=time.perf_counter()
                        response={'ok':True,'action_id':int(m.item())*5+int(s.item()),'move_id':int(m.item()),'shoot_id':int(s.item()),
                                  'use_active':False,'observation':request.get('observation'),
                                  'model_ms':round((finished-started)*1000,3),'gpu_encoder_rpc_ms':round((encoded-started)*1000,3),
                                  'head_cpu_ms':round((finished-encoded)*1000,3)}
                    else:raise ValueError('Unsupported verification command')
                except Exception as error:response={'ok':False,'error':str(error)}
                self.wfile.write((json.dumps(response)+'\n').encode());self.wfile.flush()
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address=True;daemon_threads=True
    with Server(('127.0.0.1',port),Handler) as server:
        server.timeout=.2
        print(json.dumps({'ready':True,'port':port,'backend':backend}),flush=True)
        while not stopped.is_set():server.handle_request()


def verify(args):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    from isaac_jev.model import head_from_config,decode_actions
    from isaac_jev.remote import prediction_action,validate_description
    torch.set_num_threads(1)
    dataset=args.workspace/'datasets/combat-v007';artifacts=args.workspace/'artifacts/combat-v007'
    rows=[json.loads(line) for line in (dataset/'samples.jsonl').open()]
    features=np.load(artifacts/'features/state.npy',mmap_mode='r')
    fixtures=[json.loads(line) for line in (dataset/'runtime-fixtures.jsonl').open()]
    fixtures=[f for f in fixtures if rows[f['row_index']]['split']=='validation'][:32]
    assert len(fixtures)==32
    results=[]
    for key in args.groups:
        checkpoint=artifacts/('combat-v007-'+key)
        cfg=json.loads((checkpoint/'policy_config.json').read_text())
        if cfg['feature_version'].startswith('combat-tactical-v10-'):
            from isaac_jev.features_v010 import observation
            history_key='tactical_history'
        elif cfg['feature_version'].startswith('combat-feedback-v9-'):
            from isaac_jev.features_v009 import observation
            history_key='feedback_history'
        else:
            from isaac_jev.features_v007 import observation
            history_key='semantic_history'
        log_path=artifacts/('rpc-'+key+'.log')
        with log_path.open('wb') as log:
            process=subprocess.Popen([sys.executable,'-u',__file__,'--server','--checkpoint',str(checkpoint),'--port',str(args.port)],
                    cwd=args.workspace,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        ready=False
        try:
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError(log_path.read_text())
                try:
                    description=rpc({'command':'describe'},args.port);ready=True;break
                except (OSError,ValueError):time.sleep(.25)
            if not ready:raise RuntimeError('Read-only verification service readiness timed out')
            assert validate_description(description)==cfg
            head=head_from_config(cfg).eval();head.load_state_dict(load_file(str(checkpoint/'head.safetensors')))
            norm=json.loads((checkpoint/'normalization.json').read_text());mean=torch.tensor(norm['mean']);std=torch.tensor(norm['std'])
            timings=[];matches=0
            for fixture in fixtures:
                i=fixture['row_index'];frame=fixture['frame']
                prompt,numeric,names=observation(frame,history_facts=fixture[history_key],profile=cfg['feature_version'])
                assert prompt==rows[i]['prompt'] and numeric==rows[i]['numeric'] and names==cfg['numeric_names']
                identity={'run_id':frame['run_id'],'room_id':frame['room']['id'],'game_frame':frame['game_frame']}
                payload={'command':'predict','prompt':prompt,'numeric':numeric,'observation':identity,
                         **{field:cfg[field] for field in ('feature_version','head_architecture','rules_sha256')}}
                reply=rpc(payload,args.port)
                action=prediction_action(reply,frame,cfg)
                with torch.no_grad():
                    first,second,_=head(torch.tensor(np.asarray(features[i:i+1]).copy()),(torch.tensor(numeric)-mean).unsqueeze(0)/std)
                    m,s=decode_actions(head,first,second)
                assert action.action_id==int(m.item())*5+int(s.item())
                matches+=1;timings.append({field:reply[field] for field in ('model_ms','gpu_encoder_rpc_ms','head_cpu_ms')})
            result={'group':key,'checkpoint':description['backend']['checkpoint'],'fresh_prediction_rpc_matches':matches,'samples':len(fixtures),
                    'head_device':'cpu','backbone':'existing warmed GPU encoder','timings':timings,
                    'no_game_inputs':True,'no_live_policy_switch':True,'scope':'Protocol/contract/cached-action verification; not native deployment latency'}
            (checkpoint/'rpc-verification.json').write_text(json.dumps(result,indent=2));results.append(result)
            print(json.dumps({key:value for key,value in result.items() if key!='timings'}),flush=True)
        finally:
            if ready:
                try:rpc({'command':'shutdown'},args.port)
                except OSError:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # This Popen is our own isolated verification process, not the game policy.
                process.terminate();process.wait(timeout=10)
    (artifacts/'rpc-verification.json').write_text(json.dumps({'groups':results},indent=2))


if __name__=='__main__':
    os.environ.update(HF_HUB_CACHE=str(ROOT/'.cache/huggingface/hub'),
                      HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                      TOKENIZERS_PARALLELISM='false')
    parser=argparse.ArgumentParser();parser.add_argument('--workspace',type=Path,default=ROOT);parser.add_argument('--port',type=int,default=19728)
    parser.add_argument('--groups',nargs='+',choices=('s01','s02'),default=['s01','s02'])
    parser.add_argument('--server',action='store_true');parser.add_argument('--checkpoint',type=Path)
    args=parser.parse_args()
    if args.server:serve(args.checkpoint,args.port)
    else:verify(args)
