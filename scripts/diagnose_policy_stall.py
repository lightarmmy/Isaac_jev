"""Counterfactual input sensitivity of a stalled policy; never sends game actions."""
import argparse
import asyncio
from collections import Counter
import copy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
from isaac_jev.actions import MOVE_NAMES, SHOT_NAMES
from isaac_jev.features import policy_observation
from isaac_jev.remote import PolicyClient, validate_description


async def diagnose(args):
    snapshot=json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    frame=snapshot["frame"]
    client=PolicyClient(args.port)
    report={"source_run":frame["run_id"],"source_room":frame["room"]["id"],"source_frame":frame["game_frame"],
        "method":"Counterfactual previous controls only; physical telemetry and history unchanged. No actions sent to game.",
        "results":[]}
    try:
        await client.connect();description=await client.request({"command":"describe"});cfg=validate_description(description)
        for previous in range(45):
            modified=copy.deepcopy(frame);modified["mode"]="ai"
            modified["execution"]={"action_id":previous,"use_active":False}
            prompt,numeric,names=policy_observation(modified,history_facts=snapshot["policy_history"],include_active=False,include_controls=cfg.get("control_context",True))
            if names!=cfg["numeric_names"]:raise ValueError("Numeric contract mismatch")
            reply=await client.request({"command":"predict","prompt":prompt,"numeric":numeric,
                "feature_version":cfg["feature_version"],"head_architecture":cfg["head_architecture"],"rules_sha256":cfg["rules_sha256"]})
            result={"previous_action":previous,"predicted_action":reply["action_id"],
                "move":MOVE_NAMES[reply["move_id"]],"shoot":SHOT_NAMES[reply["shoot_id"]]}
            report["results"].append(result)
        report["same_previous_action"]=sum(r["previous_action"]==r["predicted_action"] for r in report["results"])
        report["prediction_counts"]=dict(Counter(r["predicted_action"] for r in report["results"]))
        destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(report,indent=2),flush=True)
    finally:await client.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("snapshot")
    parser.add_argument("--port",type=int,default=19528)
    parser.add_argument("--output",default=".local/stall-sensitivity-v003.json")
    asyncio.run(diagnose(parser.parse_args()))
