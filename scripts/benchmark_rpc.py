"""Persistent connection RTT and actual policy response latency over an SSH tunnel."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import statistics
import time

ROOT=Path(__file__).resolve().parents[1]


def summary(values):
    ordered=sorted(values)
    return {"samples":len(values),"median_ms":round(statistics.median(values),3),
            "p95_ms":round(ordered[math.ceil(len(ordered)*0.95)-1],3),
            "min_ms":round(ordered[0],3),"max_ms":round(ordered[-1],3)}


async def benchmark(args):
    reader,writer=await asyncio.open_connection("127.0.0.1",args.port,limit=2*1024*1024)
    async def request(payload):
        started=time.perf_counter()
        writer.write((json.dumps(payload,allow_nan=False)+"\n").encode());await writer.drain()
        line=await asyncio.wait_for(reader.readline(),60)
        if not line:raise RuntimeError("Benchmark endpoint disconnected")
        reply=json.loads(line)
        if not reply.get("ok"):raise RuntimeError(str(reply))
        return reply,(time.perf_counter()-started)*1000
    report={"transport":"persistent TCP through SSH local forwarding","rtt":{},"policy":[]}
    try:
        for _ in range(5):await request({"command":"ping"})
        for size in (0,4096,16384):
            times=[]
            for _ in range(40):
                _,elapsed=await request({"command":"ping","padding":"x"*size});times.append(elapsed)
            report["rtt"][str(size)]=summary(times)
            print(json.dumps({"payload_bytes":size,**report["rtt"][str(size)]}),flush=True)
        if args.network_only:
            report["scope"]="Network round-trip only; no GPU inference"
            (ROOT/args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
            return
        description,_=await request({"command":"describe"})
        cfg=description["config"];report["backend"]=description["backend"]
        metadata_path=ROOT/args.dataset/"dataset.json"
        if metadata_path.exists():
            metadata=json.loads(metadata_path.read_text())
            for key in ("feature_version","rules_sha256","head_architecture","observation_to_action_frame_lag"):
                if metadata.get(key)!=cfg.get(key):raise ValueError("Benchmark input/server contract mismatch: "+key)
            if metadata.get("control_context",True)!=cfg.get("control_context",True):raise ValueError("Benchmark control context mismatch")
        rows=[json.loads(line) for line in (ROOT/args.dataset/"samples.jsonl").read_text(encoding="utf-8").splitlines()]
        ordered=sorted(rows,key=lambda r:len(r["deployment_prompt"]))
        def payload_for(row):
            return {"command":"predict","prompt":row["deployment_prompt"],"numeric":row["deployment_numeric"],
                    "feature_version":cfg["feature_version"],"head_architecture":cfg["head_architecture"],
                    "rules_sha256":cfg["rules_sha256"]}
        for label,row in zip(("short","median","long"),(ordered[0],ordered[len(ordered)//2],ordered[-1])):
            payload=payload_for(row)
            first,first_ms=await request(payload)
            elapsed=[];model=[]
            for _ in range(args.samples):
                reply,total=await request(payload);elapsed.append(total);model.append(reply["model_ms"])
            case={"case":label,"tokens":first["tokens"],"first_response_ms":round(first_ms,3),"request_bytes":len(json.dumps(payload).encode()),"total":summary(elapsed),
                  "model":summary(model),"transport_and_handler":summary([a-b for a,b in zip(elapsed,model)]),
                  "within_200ms_total":all(ms<200 for ms in elapsed)}
            report["policy"].append(case);print(json.dumps(case),flush=True)
        mixed=[]
        for i in range(args.mixed_samples):
            row=ordered[i*(len(ordered)-1)//max(1,args.mixed_samples-1)]
            reply,total=await request(payload_for(row))
            mixed.append({"tokens":reply["tokens"],"total_ms":round(total,3),"model_ms":reply["model_ms"]})
        if mixed:
            report["mixed_lengths"]={**summary([r["total_ms"] for r in mixed]),"results":mixed,
                "within_200ms_total":all(r["total_ms"]<200 for r in mixed)}
            print(json.dumps({"mixed_lengths":{k:v for k,v in report["mixed_lengths"].items() if k!="results"}}),flush=True)
        report["scope"]="Prepared policy inputs through response; telemetry, feature construction and game execution excluded"
        (ROOT/args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    finally:
        if args.shutdown:
            try:await request({"command":"shutdown"})
            except (ConnectionError,RuntimeError):pass
        writer.close()
        try:await writer.wait_closed()
        except ConnectionError:pass


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=19528)
    parser.add_argument("--samples",type=int,default=25)
    parser.add_argument("--mixed-samples",type=int,default=30)
    parser.add_argument("--dataset",default="datasets/combat-v003")
    parser.add_argument("--output",default=".local/server-latency-v003.json")
    parser.add_argument("--shutdown",action="store_true")
    parser.add_argument("--network-only",action="store_true")
    args=parser.parse_args()
    if args.samples<1 or args.mixed_samples<0:parser.error("Invalid sample count")
    asyncio.run(benchmark(args))
