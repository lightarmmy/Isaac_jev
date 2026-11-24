"""Export evaluation/configuration and representative latency inputs; never weights."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--checkpoint",default="artifacts/combat-v004")
    parser.add_argument("--dataset",default="datasets/combat-v004")
    parser.add_argument("--output",default=".local/v004-review.zip")
    args=parser.parse_args();artifact=ROOT/args.checkpoint;dataset=ROOT/args.dataset
    cfg=json.loads((artifact/"policy_config.json").read_text())
    rows=[json.loads(line) for line in (dataset/"samples.jsonl").read_text().splitlines()]
    if hashlib.sha256((dataset/"samples.jsonl").read_bytes()).hexdigest()!=cfg["dataset_sha256"]:
        raise ValueError("Dataset/checkpoint mismatch")
    ordered=sorted(rows,key=lambda row:len(row["deployment_prompt"]))
    ids=sorted({0,len(rows)//2,len(rows)-1,*[i*(len(rows)-1)//29 for i in range(30)]})
    samples=[{k:ordered[i][k] for k in ("deployment_prompt","deployment_numeric")} for i in ids]
    metadata={k:cfg[k] for k in ("feature_version","rules_sha256","head_architecture","observation_to_action_frame_lag")}
    metadata.update({"control_context":cfg.get("control_context",True),"samples":len(samples),
                     "purpose":"latency-only representatives, not a training dataset","source_dataset_sha256":cfg["dataset_sha256"]})
    output=ROOT/args.output;output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("policy_config.json","evaluation.json"):
            archive.write(artifact/name,name)
        archive.write(dataset/"dataset.json","dataset.json")
        archive.writestr("benchmark/samples.jsonl","\n".join(json.dumps(row,separators=(",",":")) for row in samples)+"\n")
        archive.writestr("benchmark/dataset.json",json.dumps(metadata,indent=2))
    print(json.dumps({"reports":str(output),"bytes":output.stat().st_size,"weights_included":False}))


if __name__=="__main__":main()
