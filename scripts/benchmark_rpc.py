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
        cfg=json.loads((ROOT/args.checkpoint/"policy_config.json").read_text())
        rows=[json.loads(line) for line in (ROOT/args.dataset/"samples.jsonl").read_text(encoding="utf-8").splitlines()]
        from transformers import AutoTokenizer
        snapshot=ROOT/".local/offline-hf"/("models--"+cfg["model"].replace("/","--"))/"snapshots"/cfg["revision"]
        tokenizer=AutoTokenizer.from_pretrained(snapshot,local_files_only=True)
        ordered=sorted(rows,key=lambda r:len(tokenizer.encode(r["deployment_prompt"])))
        for label,row in zip(("short","median","long"),(ordered[0],ordered[len(ordered)//2],ordered[-1])):
            payload={"command":"predict","prompt":row["deployment_prompt"],"numeric":row["deployment_numeric"],
                     "feature_version":cfg["feature_version"],"head_architecture":cfg["head_architecture"]}
            await request(payload)  # Warm up this sequence length before measurements.
            elapsed=[];model=[]
            for _ in range(5):
                reply,total=await request(payload);elapsed.append(total);model.append(reply["model_ms"])
            case={"case":label,"tokens":len(tokenizer.encode(row["deployment_prompt"])),"request_bytes":len(json.dumps(payload).encode()),"total":summary(elapsed),
                  "model":summary(model),"transport_and_handler":summary([a-b for a,b in zip(elapsed,model)]),
                  "within_200ms_total":all(ms<200 for ms in elapsed)}
            report["policy"].append(case);print(json.dumps(case),flush=True)
        report["scope"]="Prepared policy inputs through response; telemetry, feature construction and game execution excluded"
        (ROOT/args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    finally:
        if args.shutdown:
            try:await request({"command":"shutdown"})
            except (ConnectionError,RuntimeError):pass
        writer.close();await writer.wait_closed()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=19528)
    parser.add_argument("--checkpoint",default="artifacts/combat-v003")
    parser.add_argument("--dataset",default="datasets/combat-v003")
    parser.add_argument("--output",default=".local/server-latency-v003.json")
    parser.add_argument("--shutdown",action="store_true")
    parser.add_argument("--network-only",action="store_true")
    asyncio.run(benchmark(parser.parse_args()))
