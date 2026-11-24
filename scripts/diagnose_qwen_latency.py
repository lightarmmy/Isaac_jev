"""Separate host work and GPU forward latency without changing the model or resource limits."""
import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--output",default=".local/qwen-latency-diagnosis.json")
    args=parser.parse_args()
    import torch
    from isaac_jev.model import FrozenQwen
    cache=ROOT/".cache/huggingface/hub" if (ROOT/".cache/huggingface/hub").exists() else ROOT/".local/offline-hf"
    os.environ["HF_HUB_CACHE"]=str(cache);os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1"
    cfg=json.loads((ROOT/"artifacts/combat-v003/policy_config.json").read_text())
    rows=[json.loads(l) for l in (ROOT/"datasets/combat-v003/samples.jsonl").read_text(encoding="utf-8").splitlines()]
    model=FrozenQwen(cfg["model"],cfg["revision"],"cuda",cfg["dtype"],cfg["max_length"])
    ordered=sorted(rows,key=lambda r:len(model.tokenizer.encode(r["deployment_prompt"])))
    chosen=[ordered[0],ordered[len(ordered)//2],ordered[-1]]
    defaults=torch.get_num_threads();report={"default_threads":defaults,"cases":[]}
    reference={}
    for threads in (defaults,1,4,8):
        torch.set_num_threads(threads)
        for label,row in zip(("short","median","long"),chosen):
            prompt=row["deployment_prompt"];model.encode([prompt]);torch.cuda.synchronize()
            measurements=[]
            for _ in range(3):
                t=time.perf_counter()
                encoded=model.tokenizer([prompt],padding=True,truncation=False,return_tensors="pt")
                tokenize=(time.perf_counter()-t)*1000
                t=time.perf_counter();encoded={k:v.cuda() for k,v in encoded.items() if k in {"input_ids","attention_mask"}}
                torch.cuda.synchronize();transfer=(time.perf_counter()-t)*1000
                begin=torch.cuda.Event(enable_timing=True);end=torch.cuda.Event(enable_timing=True)
                torch.cuda.synchronize();t=time.perf_counter();begin.record()
                with torch.no_grad():output=model.backbone(**encoded,use_cache=False,return_dict=True).last_hidden_state[:,-1].float()
                end.record();torch.cuda.synchronize();forward=(time.perf_counter()-t)*1000
                measurements.append({"tokenize_ms":tokenize,"transfer_ms":transfer,"forward_wall_ms":forward,"cuda_elapsed_ms":begin.elapsed_time(end)})
            if label not in reference:reference[label]=output.detach().clone()
            case={"threads":threads,"case":label,"tokens":encoded["input_ids"].shape[-1],
                  **{k:round(statistics.median(m[k] for m in measurements),3) for k in measurements[0]},
                  "max_abs_output_diff":float((output-reference[label]).abs().max().item())}
            report["cases"].append(case);print(json.dumps(case),flush=True)
    (ROOT/args.output).write_text(json.dumps(report,indent=2))


if __name__=="__main__":main()
