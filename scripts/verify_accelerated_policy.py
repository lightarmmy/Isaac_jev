"""Compare accelerated features/actions with the unchanged training feature cache."""
import hashlib
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
os.environ["HF_HUB_CACHE"]=str(ROOT/".cache/huggingface/hub")
os.environ["HF_HUB_OFFLINE"]="1";os.environ["TRANSFORMERS_OFFLINE"]="1"
os.environ["TRITON_CACHE_DIR"]=str(ROOT/".cache/triton")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--pad-multiple",type=int,default=512)
    parser.add_argument("--checkpoint",default="artifacts/combat-v003")
    parser.add_argument("--dataset",default="datasets/combat-v003")
    parser.add_argument("--batch-size",type=int,default=4)
    parser.add_argument("--output",default=".local/accelerated-padded-equivalence.json")
    args=parser.parse_args()
    import torch,numpy as np
    from safetensors.torch import load_file
    from isaac_jev.model import FrozenQwen,CombatHead
    torch.set_num_threads(1)
    if args.batch_size<1:parser.error("batch-size must be positive")
    root=ROOT/args.checkpoint
    cfg=json.loads((root/"policy_config.json").read_text())
    dataset=ROOT/args.dataset/"samples.jsonl"
    assert hashlib.sha256(dataset.read_bytes()).hexdigest()==cfg["dataset_sha256"]
    rows=[json.loads(l) for l in dataset.read_text().splitlines()]
    cache=np.load(root/"frozen_features.npz")
    ids=[i for i,r in enumerate(rows) if r["split"] in {"validation","test"}]
    # Fixed representative subset, chosen without looking at model accuracy.
    selected=ids[::max(1,len(ids)//32)][:32]
    encoder=FrozenQwen(cfg["model"],cfg["revision"],"cuda",cfg["dtype"],cfg["max_length"])
    head=CombatHead(cfg["embedding_size"],cfg["numeric_size"],cfg["head_hidden_size"],active_enabled=False).cuda().eval()
    head.load_state_dict(load_file(str(root/"head.safetensors")))
    norm=json.loads((root/"normalization.json").read_text())
    mean=torch.tensor(norm["mean"],device="cuda");std=torch.tensor(norm["std"],device="cuda")
    report={"samples":len(selected),"cuda":torch.version.cuda,"torch_cxx11_abi":torch._C._GLIBCXX_USE_CXX11_ABI,
            "pad_multiple":args.pad_multiple,
            "results":[]}
    with torch.no_grad():
        for start in range(0,len(selected),args.batch_size):
            batch=selected[start:start+args.batch_size]
            accelerated=encoder.encode([rows[i]["deployment_prompt"] for i in batch],pad_to_multiple_of=args.pad_multiple or None)
            original=torch.tensor(cache["deployment_embedding"][batch],device="cuda").float()
            numeric=(torch.tensor([rows[i]["deployment_numeric"] for i in batch],device="cuda")-mean)/std
            old_move,old_shoot,_=head(original,numeric)
            new_move,new_shoot,_=head(accelerated,numeric)
            cosine=torch.nn.functional.cosine_similarity(original,accelerated,dim=-1)
            for j,i in enumerate(batch):
                report["results"].append({"row":i,"cosine":float(cosine[j].item()),
                    "max_abs_feature_difference":float((original[j]-accelerated[j]).abs().max().item()),
                    "same_action":bool(old_move[j].argmax()==new_move[j].argmax() and old_shoot[j].argmax()==new_shoot[j].argmax())})
    report["minimum_cosine"]=min(r["cosine"] for r in report["results"])
    report["same_action_samples"]=sum(r["same_action"] for r in report["results"])
    (ROOT/args.output).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="results"},indent=2),flush=True)


if __name__=="__main__":main()
