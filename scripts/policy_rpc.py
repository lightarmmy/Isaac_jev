"""Temporary loopback-only inference endpoint for SSH-tunnel latency measurements."""
import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))


def prediction_fields(request,cfg):
    if request.get("feature_version")!=cfg["feature_version"] or request.get("head_architecture")!=cfg["head_architecture"]:
        raise ValueError("Policy contract mismatch")
    prompt=request.get("prompt");numeric=request.get("numeric")
    if not isinstance(prompt,str) or not prompt or len(prompt)>100000:raise ValueError("Invalid prompt")
    if not isinstance(numeric,list) or len(numeric)!=cfg["numeric_size"]:raise ValueError("Numeric feature count mismatch")
    if any(type(x) not in (float,int) or not math.isfinite(x) for x in numeric):raise ValueError("Numeric features must be finite numbers")
    return prompt,numeric


async def serve(args):
    import torch
    from safetensors.torch import load_file
    from isaac_jev.features import contract
    from isaac_jev.model import FrozenQwen,CombatHead
    from isaac_jev.protocol import MAX_MESSAGE_BYTES
    cache=ROOT/".local/offline-hf" if (ROOT/".local/offline-hf").exists() else ROOT/".cache/huggingface/hub"
    os.environ["HF_HUB_CACHE"]=str(cache)
    os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1"
    checkpoint=(ROOT/args.checkpoint).resolve()
    for name,digest in json.loads((checkpoint/"manifest.json").read_text()).items():
        path=(checkpoint/name).resolve()
        if path.parent!=checkpoint or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError("Checkpoint checksum mismatch")
    cfg=json.loads((checkpoint/"policy_config.json").read_text())
    if any(cfg.get(k)!=v for k,v in contract().items()):raise ValueError("Checkpoint/runtime mismatch")
    encoder=FrozenQwen(cfg["model"],cfg["revision"],"cuda",cfg["dtype"],cfg["max_length"])
    head=CombatHead(cfg["embedding_size"],cfg["numeric_size"],cfg["head_hidden_size"],active_enabled=cfg.get("active_head_present",True)).cuda().eval()
    head.load_state_dict(load_file(str(checkpoint/"head.safetensors")))
    norm=json.loads((checkpoint/"normalization.json").read_text())
    mean=torch.tensor(norm["mean"],device="cuda");std=torch.tensor(norm["std"],device="cuda")
    stop=asyncio.Event()
    async def client(reader,writer):
        try:
            while line:=await reader.readline():
                try:
                    if len(line)>MAX_MESSAGE_BYTES:raise ValueError("Request too large")
                    request=json.loads(line)
                    if not isinstance(request,dict):raise ValueError("Request must be an object")
                    command=request.get("command")
                    if command=="ping":response={"ok":True,"pong":True}
                    elif command=="shutdown":response={"ok":True,"shutdown":True}
                    elif command=="predict":
                        prompt,numeric=prediction_fields(request,cfg)
                        torch.cuda.synchronize();started=time.perf_counter()
                        with torch.no_grad():
                            n=(torch.tensor(numeric,device="cuda")-mean)/std
                            move,shoot,active=head(encoder.encode([prompt]),n.unsqueeze(0))
                            m,s=move.argmax(-1).item(),shoot.argmax(-1).item()
                        torch.cuda.synchronize()
                        response={"ok":True,"move_id":m,"shoot_id":s,"action_id":m*5+s,
                                  "use_active":False,"model_ms":round((time.perf_counter()-started)*1000,3)}
                    else:raise ValueError("Unknown command")
                except (ValueError,TypeError,KeyError) as exc:response={"ok":False,"error":str(exc)}
                writer.write((json.dumps(response,allow_nan=False)+"\n").encode());await writer.drain()
                if response.get("shutdown"):stop.set();break
        finally:
            writer.close();await writer.wait_closed()
    server=await asyncio.start_server(client,"127.0.0.1",args.port,limit=MAX_MESSAGE_BYTES+1)
    print(json.dumps({"ready":True,"host":"127.0.0.1","port":args.port,"gpu":torch.cuda.get_device_name(0),
                      "purpose":"benchmark only; no game control"}),flush=True)
    try:
        async with server:await stop.wait()
    finally:
        server.close();await server.wait_closed()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--checkpoint",default="artifacts/combat-v003")
    parser.add_argument("--port",type=int,default=19528)
    asyncio.run(serve(parser.parse_args()))
