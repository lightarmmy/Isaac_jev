"""Audit input timing by room/visit; supports explicitly identified unfinished gzip logs."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import sys
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from isaac_jev.rooms import combat_active


def read_records(path,state):
    stream=zlib.decompressobj(31);pending=b""
    with Path(path).open("rb") as source:
        while chunk:=source.read(256*1024):
            pending+=stream.decompress(chunk)
            lines=pending.split(b"\n");pending=lines.pop()
            for line in lines:
                if line.strip():yield json.loads(line)
    state["gzip_complete"]=stream.eof
    state["trailing_partial_line"]=bool(pending.strip())


def distribution(values):
    return {"count":len(values),"min":min(values),"median":statistics.median(values),"max":max(values)} if values else None


def audit(path,run_id=None,room_id=None):
    state={};groups=defaultdict(lambda:{"frames":[],"commands":[],"starts":[],"ends":[],"acks":[]})
    command_groups={};epoch=0;previous=None
    for row in read_records(path,state):
        if row.get("type")=="frame":
            identity=(row["run_id"],row["game_frame"])
            if previous is None or identity[0]!=previous[0] or identity[1]<=previous[1]:epoch+=1
            previous=identity
            key=(epoch,row["run_id"],row["room"]["id"])
            groups[key]["frames"].append({"n":row["game_frame"],"time":row.get("received_at_unix"),
                "ai":row.get("mode")=="ai","combat":combat_active(row) and not row["player"].get("dead"),
                "applied":row.get("applied_controls"),"pending":bool(row.get("execution")),
                "started":(row.get("execution") or {}).get("started_frame")})
        elif row.get("command")=="action":
            key=(epoch,row.get("run_id"),row.get("room_id"));command_groups[row["request_id"]]=key
            groups[key]["commands"].append(row)
        elif row.get("type")=="command_ack" or row.get("event") in {"action_started","action_ended","action_discarded"}:
            key=command_groups.get(row.get("request_id"))
            if key:
                field="acks" if row.get("type")=="command_ack" else "starts" if row.get("event")=="action_started" else "ends"
                groups[key][field].append(row)
    report=[]
    for (epoch,run,room),group in groups.items():
        if run_id and run!=run_id or room_id and room!=room_id:continue
        ai=[f for f in group["frames"] if f["ai"]];combat=[f for f in ai if f["combat"]]
        if not ai:continue
        for phase,frames in (("all_ai",ai),("combat",combat)):
            if not frames:continue
            numbers={f["n"] for f in frames};low,high=min(numbers),max(numbers)
            commands=[c for c in group["commands"] if c["observation_frame"] in numbers]
            ids={c["request_id"] for c in commands}
            starts=[s for s in group["starts"] if s["request_id"] in ids]
            acks=[a for a in group["acks"] if a["request_id"] in ids]
            ends={e["request_id"]:e["game_frame"] for e in group["ends"] if e.get("event")=="action_ended"}
            # Union, not sum: replacements and room transitions truncate holds.
            inferred=set()
            for s in starts:
                end=min(s["stop_frame"],ends.get(s["request_id"],s["stop_frame"]))
                inferred.update(n for n in range(s["game_frame"],end) if n in numbers)
            actual=set();pressed=set()
            for f in group["frames"]:
                a=f["applied"] or {};n=a.get("game_frame")
                if a.get("run_id")!=run or a.get("room_id")!=room or n not in numbers:continue
                if a.get("requests"):actual.add(n)
                if any(any(r.get("pressed",{}).values()) for r in a.get("requests",[])):pressed.add(n)
            has_actual=any(f["applied"] is not None for f in frames)
            times=[c["sent_at_unix"] for c in commands if "sent_at_unix" in c]
            report.append({"epoch":epoch,"run_id":run,"room_id":room,"phase":phase,
                "recorded_frames":len(numbers),"frame_range":[low,high],"missing_frames":high-low+1-len(numbers),
                "wall_seconds":round(frames[-1]["time"]-frames[0]["time"],3) if frames[0]["time"] and frames[-1]["time"] else None,
                "commands":len(commands),"ack_reasons":dict(Counter(("accepted:" if a.get("ok") else "rejected:")+str(a.get("reason")) for a in acks)),
                "action_starts":len(starts),"start_delay_frames":distribution([s["game_frame"]-s["observation_frame"] for s in starts]),
                "scheduled_hold_frames":distribution([s["stop_frame"]-s["game_frame"] for s in starts]),
                "median_send_interval_ms":round(statistics.median((b-a)*1000 for a,b in zip(times,times[1:])),3) if len(times)>1 else None,
                "event_interval_union_frames":len(inferred),"event_interval_union_fraction":round(len(inferred)/len(numbers),4),
                "actual_query_frames":len(actual) if has_actual else None,
                "actual_query_fraction":round(len(actual)/len(numbers),4) if has_actual else None,
                "actual_nonzero_frames":len(pressed) if has_actual else None,
                "note":"Actual query coverage uses deduplicated applied_controls frame keys. Event intervals are an estimate for old logs; an ACK is not execution."})
    return {"recording":str(Path(path).resolve()),**state,"groups":report}


def main():
    p=argparse.ArgumentParser();p.add_argument("recording",type=Path);p.add_argument("--run-id");p.add_argument("--room-id");p.add_argument("--output",type=Path)
    args=p.parse_args();path=args.recording/"frames.jsonl.gz" if args.recording.is_dir() else args.recording
    result=audit(path,args.run_id,args.room_id);text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+"\n",encoding="utf-8")
    print(text)


if __name__=="__main__":main()
