"""Offline GPU timing on recorded prompts; never sends game controls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--checkpoint",default="artifacts/combat-v002")
    parser.add_argument("--dataset",default="datasets/combat-v002")
    parser.add_argument("--repeats",type=int,default=2)
    parser.add_argument("--output",default=".local/local-policy-benchmark.json")
    args=parser.parse_args()
    if args.repeats<1:parser.error("repeats must be positive")
    os.environ["HF_HUB_CACHE"]=str(ROOT/".local/offline-hf")
    os.environ["HF_HUB_OFFLINE"]="1"
    os.environ["TRANSFORMERS_OFFLINE"]="1"
    import torch
    from safetensors.torch import load_file
    from isaac_jev.features import contract
    from isaac_jev.model import FrozenQwen,CombatHead
    if not torch.cuda.is_available():raise RuntimeError("Install CUDA Torch in the project venv first")
    checkpoint=ROOT/args.checkpoint
    for name,digest in json.loads((checkpoint/"manifest.json").read_text()).items():
        path=(checkpoint/name).resolve()
        if path.parent!=checkpoint.resolve() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError("Checkpoint manifest failed")
    cfg=json.loads((checkpoint/"policy_config.json").read_text())
    if any(cfg.get(k)!=v for k,v in contract().items()):raise ValueError("Checkpoint contract mismatch")
    rows=[json.loads(line) for line in (ROOT/args.dataset/"samples.jsonl").read_text(encoding="utf-8").splitlines()]
    encoder=FrozenQwen(cfg["model"],cfg["revision"],"cuda",cfg["dtype"],cfg["max_length"])
    head=CombatHead(cfg["embedding_size"],cfg["numeric_size"],cfg["head_hidden_size"]).cuda().eval()
    head.load_state_dict(load_file(str(checkpoint/"head.safetensors")))
    ordered=sorted(rows,key=lambda r:len(encoder.tokenizer.encode(r["deployment_prompt"])))
    norm=json.loads((checkpoint/"normalization.json").read_text())
    mean=torch.tensor(norm["mean"],device="cuda");std=torch.tensor(norm["std"],device="cuda")
    def predict(row):
        vector=(torch.tensor(row["deployment_numeric"],device="cuda")-mean)/std
        with torch.no_grad():return head(encoder.encode([row["deployment_prompt"]]),vector.unsqueeze(0))
    predict(ordered[0]);torch.cuda.synchronize()
    cases=[]
    for label,row in zip(("short","median","long"),(ordered[0],ordered[len(ordered)//2],ordered[-1])):
        durations=[]
        for _ in range(args.repeats):
            torch.cuda.synchronize();started=time.perf_counter()
            move,shoot,active=predict(row)
            torch.cuda.synchronize();durations.append((time.perf_counter()-started)*1000)
        case={"case":label,"tokens":len(encoder.tokenizer.encode(row["deployment_prompt"])),
              "median_ms":round(statistics.median(durations),2),"times_ms":[round(x,2) for x in durations],
              "move_id":move.argmax(-1).item(),"shoot_id":shoot.argmax(-1).item(),
              "condition":"positive","within_200ms_model_only":all(x<200 for x in durations)}
        cases.append(case);print(json.dumps(case),flush=True)
    report={"torch":torch.__version__,"gpu":torch.cuda.get_device_name(0),
            "peak_allocated_mib":round(torch.cuda.max_memory_allocated()/2**20,1),"cases":cases,
            "scope":"Offline model latency only; game/bridge scheduling adds latency; no clear-room performance claim"}
    (ROOT/args.output).write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2),flush=True)


if __name__=="__main__":main()
