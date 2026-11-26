"""Persistent loopback-only GPU policy endpoint, accessed through SSH forwarding."""
import argparse
import base64
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))


def prediction_fields(request,cfg):
    for key in ("feature_version","head_architecture","rules_sha256"):
        if request.get(key)!=cfg[key]:raise ValueError("Policy contract mismatch: "+key)
    prompt=request.get("prompt");numeric=request.get("numeric")
    if not isinstance(prompt,str) or not prompt or len(prompt)>100000:raise ValueError("Invalid prompt")
    if not isinstance(numeric,list) or len(numeric)!=cfg["numeric_size"]:raise ValueError("Numeric feature count mismatch")
    if any(type(x) not in (float,int) or not math.isfinite(x) for x in numeric):raise ValueError("Numeric features must be finite numbers")
    return prompt,numeric


def feature_texts(request,cfg):
    texts=request.get('texts')
    if request.get('model_revision')!=cfg['revision']:raise ValueError('Backbone revision mismatch')
    if not isinstance(texts,list) or len(texts)!=1 or not isinstance(texts[0],str) or not 1<=len(texts[0])<=100000:
        raise ValueError('Feature extraction accepts one bounded text')
    return texts


async def serve(args):
    os.environ["TRITON_CACHE_DIR"]=str(ROOT/".cache/triton")
    os.environ["TORCH_EXTENSIONS_DIR"]=str(ROOT/".cache/torch-extensions")
    os.environ["TOKENIZERS_PARALLELISM"]="false"
    cache=ROOT/".local/offline-hf" if (ROOT/".local/offline-hf").exists() else ROOT/".cache/huggingface/hub"
    os.environ["HF_HUB_CACHE"]=str(cache)
    os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1"
    import torch
    torch.set_num_threads(args.cpu_threads);torch.set_num_interop_threads(1)
    from safetensors.torch import load_file
    from isaac_jev.features import contract_for
    from isaac_jev.model import FrozenQwen,head_from_config,decode_actions
    from isaac_jev.protocol import MAX_MESSAGE_BYTES
    checkpoint=(ROOT/args.checkpoint).resolve()
    for name,digest in json.loads((checkpoint/"manifest.json").read_text()).items():
        path=(checkpoint/name).resolve()
        if path.parent!=checkpoint or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError("Checkpoint checksum mismatch")
    cfg=json.loads((checkpoint/"policy_config.json").read_text())
    if any(cfg.get(k)!=v for k,v in contract_for(cfg).items()):raise ValueError("Checkpoint/runtime mismatch")
    if cfg.get("active_head_present") or cfg.get("active_context"):raise ValueError("Deploy the movement/shooting-only checkpoint")
    if args.pad_multiple and cfg["max_length"]%args.pad_multiple:raise ValueError("Pad multiple must divide token budget")
    encoder=FrozenQwen(cfg["model"],cfg["revision"],"cuda",cfg["dtype"],cfg["max_length"])
    head=head_from_config(cfg).cuda().eval()
    head.load_state_dict(load_file(str(checkpoint/"head.safetensors")))
    norm=json.loads((checkpoint/"normalization.json").read_text())
    mean=torch.tensor(norm["mean"],device="cuda");std=torch.tensor(norm["std"],device="cuda")
    warm_lengths=list(range(args.pad_multiple,cfg["max_length"]+1,args.pad_multiple)) if args.pad_multiple else [512,1024,2048,cfg["max_length"]]
    token=encoder.tokenizer.encode("Isaac",add_special_tokens=False)[0]
    for length in warm_lengths:
        ids=torch.full((1,length),token,dtype=torch.long,device="cuda");mask=torch.ones_like(ids)
        if args.pad_multiple:mask[:,max(1,length-17):]=0;ids[mask==0]=encoder.tokenizer.pad_token_id
        with torch.inference_mode():
            for _ in range(2):
                output=encoder.backbone(input_ids=ids,attention_mask=mask,use_cache=False,return_dict=True)
                head(output.last_hidden_state[:,max(0,length-18)].float(),mean.unsqueeze(0))
        torch.cuda.synchronize();print(json.dumps({"warmup_tokens":length}),flush=True)
    rows_path=ROOT/args.warmup_dataset/"samples.jsonl"
    if rows_path.exists():
        rows=[json.loads(line) for line in rows_path.read_text().splitlines()]
        if cfg.get('semantic_experiment_id'):
            for row in rows:
                row['deployment_prompt']=row['prompt'];row['deployment_numeric']=row['numeric']
        if cfg.get('experiment_id'):
            from isaac_jev.experiments import transform_prompt
            for row in rows:
                row['deployment_prompt']=transform_prompt(row['deployment_prompt'],row['deployment_numeric'],row['numeric_names'],cfg['prompt_scheme'])
        ordered=sorted(rows,key=lambda row:len(row["deployment_prompt"]))
        for row in (ordered[0],ordered[len(ordered)//2],ordered[-1]):
            with torch.inference_mode():encoder.encode([row["deployment_prompt"]],pad_to_multiple_of=args.pad_multiple or None)
    torch.cuda.synchronize()
    def package_version(name):
        try:return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:return None
    checkpoint_label=cfg.get('checkpoint_label') or (f"combat-v007-{cfg['semantic_experiment_id']}-epoch{cfg['checkpoint_epoch']:03d}" if cfg.get('semantic_experiment_id') else
                      f"combat-v006-{cfg['experiment_id']}-epoch{cfg['checkpoint_epoch']:03d}" if cfg.get('experiment_id') else checkpoint.name)
    backend={"checkpoint":checkpoint_label,"gpu":torch.cuda.get_device_name(0),"torch":torch.__version__,"cpu_threads":torch.get_num_threads(),
        "interop_threads":torch.get_num_interop_threads(),"flash_linear_attention":package_version("flash-linear-attention"),
        "causal_conv1d":package_version("causal-conv1d"),"pad_multiple":args.pad_multiple,
        "max_tokens":cfg["max_length"],"dtype":cfg["dtype"],"warmup_lengths":warm_lengths}
    stop=asyncio.Event();gpu_busy=False
    for sig in (signal.SIGINT,signal.SIGTERM):asyncio.get_running_loop().add_signal_handler(sig,stop.set)

    def predict(prompt,numeric):
        worker_started=time.perf_counter()
        with torch.inference_mode():
            torch.cuda.synchronize();started=time.perf_counter()
            embedding=encoder.encode([prompt],pad_to_multiple_of=args.pad_multiple or None)
            n=(torch.tensor(numeric,device="cuda")-mean)/std
            move,shoot,_=head(embedding,n.unsqueeze(0))
            decoded_move,decoded_shoot=decode_actions(head,move,shoot)
            m,s=decoded_move.item(),decoded_shoot.item()
            torch.cuda.synchronize()
            model_finished=time.perf_counter()
            tokens=len(encoder.tokenizer.encode(prompt))
            finished=time.perf_counter()
            result={"ok":True,"move_id":m,"shoot_id":s,"action_id":m*5+s,"use_active":False,
                "model_ms":round((model_finished-started)*1000,3),"tokens":tokens,
                "worker_ms":round((finished-worker_started)*1000,3),
                "pre_model_ms":round((started-worker_started)*1000,3),
                "post_model_ms":round((finished-model_finished)*1000,3)}
            if cfg.get('semantic_experiment_id')=='s02':
                from isaac_jev.features_v007 import option_texts
                result['selected_option_text']=option_texts()[m*5+s]
            return result

    # GPU execution uses one worker thread; warm that thread's CUDA context as well.
    if rows_path.exists():
        await asyncio.to_thread(predict,ordered[len(ordered)//2]["deployment_prompt"],ordered[len(ordered)//2]["deployment_numeric"])

    async def client(reader,writer):
        nonlocal gpu_busy
        try:
            while line:=await reader.readline():
                request_started=time.perf_counter()
                try:
                    if len(line)>MAX_MESSAGE_BYTES:raise ValueError("Request too large")
                    request=json.loads(line)
                    if not isinstance(request,dict):raise ValueError("Request must be an object")
                    command=request.get("command")
                    if command=="ping":response={"ok":True,"pong":True}
                    elif command=="describe":response={"ok":True,"config":cfg,"backend":backend}
                    elif command=="shutdown":response={"ok":True,"shutdown":True}
                    elif command=="encode_features":
                        texts=feature_texts(request,cfg)
                        if gpu_busy:response={"ok":False,"error":"GPU busy"}
                        else:
                            gpu_busy=True
                            try:
                                def extract():
                                    with torch.inference_mode():return encoder.encode(texts,pad_to_multiple_of=args.pad_multiple).cpu().to(torch.float16).numpy().tobytes()
                                encoded=await asyncio.to_thread(extract)
                                response={"ok":True,"embedding_size":cfg['embedding_size'],"dtype":"float16","model_revision":cfg['revision'],
                                          "features_base64":base64.b64encode(encoded).decode('ascii')}
                            finally:gpu_busy=False
                    elif command=="predict":
                        prompt,numeric=prediction_fields(request,cfg)
                        if gpu_busy:response={"ok":False,"error":"GPU busy; do not queue stale observations"}
                        else:
                            gpu_busy=True
                            try:response=await asyncio.to_thread(predict,prompt,numeric)
                            finally:gpu_busy=False
                            response["observation"]=request.get("observation")
                            response["server_ms"]=round((time.perf_counter()-request_started)*1000,3)
                    else:raise ValueError("Unknown command")
                except (ValueError,TypeError,KeyError,RuntimeError) as exc:response={"ok":False,"error":str(exc)}
                writer.write((json.dumps(response,allow_nan=False)+"\n").encode());await writer.drain()
                if response.get("shutdown"):stop.set();break
        except (ConnectionError,ValueError,asyncio.LimitOverrunError):pass
        finally:
            writer.close()
            try:await writer.wait_closed()
            except ConnectionError:pass
    server=await asyncio.start_server(client,"127.0.0.1",args.port,limit=MAX_MESSAGE_BYTES+1)
    ready={"ready":True,"pid":os.getpid(),"host":"127.0.0.1","port":args.port,"backend":backend,
           "checkpoint":str(checkpoint),"purpose":"remote combat policy; local client defaults to shadow"}
    ready_path=ROOT/".local/policy-service-ready.json"
    ready_path.parent.mkdir(parents=True,exist_ok=True);ready_path.write_text(json.dumps(ready,indent=2))
    print(json.dumps(ready),flush=True)
    try:
        async with server:await stop.wait()
    finally:
        server.close();await server.wait_closed();ready_path.unlink(missing_ok=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--checkpoint",default="artifacts/combat-v003")
    parser.add_argument("--port",type=int,default=19528)
    parser.add_argument("--cpu-threads",type=int,default=1)
    parser.add_argument("--pad-multiple",type=int,default=512)
    parser.add_argument("--warmup-dataset",default="datasets/combat-v003")
    args=parser.parse_args()
    if args.cpu_threads<1 or args.pad_multiple<0:parser.error("Invalid thread/padding settings")
    asyncio.run(serve(args))
