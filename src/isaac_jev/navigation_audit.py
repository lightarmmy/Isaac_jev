"""Audit navigation tasks using controller events and raw, consecutive game frames."""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import zlib

from .actions import MOVES
from .pickups import acquisition_confirmed, pickup_consumed
from .rooms import combat_active


def records(path, state):
    """Read complete lines from a snapshot of an optionally unfinished recording."""
    path=Path(path);compressed=path.suffix==".gz"
    decoder=zlib.decompressobj(31) if compressed else None
    pending=b""
    with path.open("rb") as source:
        remaining=path.stat().st_size
        while remaining:
            chunk=source.read(min(256*1024,remaining));remaining-=len(chunk)
            if not chunk:break
            pending+=decoder.decompress(chunk) if compressed else chunk
            lines=pending.split(b"\n");pending=lines.pop()
            for line in lines:
                if line.strip():yield json.loads(line)
    state["gzip_complete"]=decoder.eof if compressed else None
    state["trailing_partial_line"]=bool(pending.strip())


def timestamp(row):
    if row.get("time"):return datetime.fromisoformat(row["time"]).timestamp()
    return row.get("received_at_unix")


def in_task(row,start,end):
    if row.get("run_id")!=start.get("run_id"):return False
    t=timestamp(row)
    return t is not None and timestamp(start)<=t<=timestamp(end)


def analyze_task(start,end,frames,events):
    task_id=start["task_id"];target=start["target"]
    frames=[f for f in frames if in_task(f,start,end)]
    logs=[e for e in events if in_task(e,start,end) and e.get("task_id",task_id)==task_id]
    logs.sort(key=timestamp)
    # Old logs have no task UUID on action diagnostics. Their attribution is
    # inferred only inside the enclosing task's run/time interval.
    sent=[e for e in logs if e.get("event")=="action_sent"]
    requests={e["request_id"]:e.get("controller_phase") for e in logs
              if e.get("event")=="action_ack" and e.get("request_id")}
    phase="navigation";cursor=0;consumed_frame=None;confirmed_frame=None
    classified=[];before=start.get("before") or (frames[0]["player"] if frames else {})
    pickup=target.get("kind") in {"collect_pickup","collect_treasure_item"}
    for f in frames:
        now=timestamp(f)
        while cursor<len(logs) and timestamp(logs[cursor])<=now:
            e=logs[cursor];cursor+=1
            if e["event"]=="combat_started":phase="combat"
            elif e["event"]=="action_sent":
                phase=e.get("controller_phase","unknown")
                if (e.get("navigation") or {}).get("stage")=="awaiting_pickup_confirmation":phase="confirmation"
                elif (e.get("navigation") or {}).get("stage")=="settling":phase="settling_or_handoff"
            elif e["event"]=="pickup_consumed":phase="confirmation"
        if pickup:
            entity=next((e for e in f.get("entities",[]) if e.get("uid")==target.get("target_id")),None)
            consumed=pickup_consumed(target,entity,before,f["player"])
            if consumed and consumed_frame is None:consumed_frame=f["game_frame"]
            if consumed and acquisition_confirmed(target,before,f["player"]) and confirmed_frame is None:confirmed_frame=f["game_frame"]
        if combat_active(f):label="combat"
        elif pickup and consumed_frame is not None:label="confirmation"
        elif phase in {"combat","settling_or_handoff"}:label="settling_or_handoff"
        elif phase in {"navigation","confirmation"}:label=phase
        else:label="unknown"
        classified.append((f,label))
    counts=Counter(p for _,p in classified);seconds=Counter();path=net=0.0
    segments=[];segment=[];discontinuities=0
    for i,(f,label) in enumerate(classified):
        previous,previous_phase=classified[i-1] if i else (None,None)
        dt=timestamp(f)-timestamp(previous) if previous else None
        adjacent=bool(previous and f["run_id"]==previous["run_id"] and
            f["room"]["id"]==previous["room"]["id"] and
            0<f["game_frame"]-previous["game_frame"]<=3 and 0<dt<=0.25 and label==previous_phase)
        if adjacent:seconds[label]+=dt
        elif previous:discontinuities+=1
        if not adjacent or label!="navigation":
            if segment:segments.append(segment);segment=[]
        if label=="navigation":
            position=f["player"]["position"];point=(position["x"],position["y"])
            if segment:
                dx,dy=point[0]-segment[-1][0],point[1]-segment[-1][1]
                path+=math.hypot(dx,dy)
            segment.append(point)
    if segment:segments.append(segment)
    net=sum(math.dist(s[0],s[-1]) for s in segments)

    def reversals(vectors,threshold):
        old=[0,0];result=[0,0]
        for v in vectors:
            for axis,value in enumerate(v):
                if abs(value)<=threshold:continue
                sign=1 if value>0 else -1
                if old[axis] and sign!=old[axis]:result[axis]+=1
                old[axis]=sign
        return {"x":result[0],"y":result[1]}

    navigation_commands=[e for e in sent if e.get("controller_phase")=="navigation" and
        (e.get("navigation") or {}).get("stage") not in {"awaiting_pickup_confirmation","settling"} and
        (consumed_frame is None or e.get("observation_frame",0)<consumed_frame)]
    # Preserve phase/room boundaries: unrelated combat and coordinate teleports
    # are never compared to navigation movement or direction changes.
    command_vectors=[];command_reversals=Counter()
    last_room=None
    for e in sent:
        if (e not in navigation_commands or e.get("room_id")!=last_room) and command_vectors:
            command_reversals.update(reversals(command_vectors,0));command_vectors=[]
        if e not in navigation_commands:continue
        last_room=e.get("room_id");command_vectors.append(MOVES[e["action_id"]//5])
    command_reversals.update(reversals(command_vectors,0))
    movement_reversals=Counter()
    for s in segments:
        movement_reversals.update(reversals([(b[0]-a[0],b[1]-a[1]) for a,b in zip(s,s[1:])],1.0))
    nav_keys={(f["run_id"],f["room"]["id"],f["game_frame"]) for f,p in classified if p=="navigation"}
    applied=set();nonzero=set();unknown_requests=set();has_applied=False
    for f,_ in classified:
        a=f.get("applied_controls")
        if a is None:continue
        has_applied=True;key=(a.get("run_id"),a.get("room_id"),a.get("game_frame"))
        if key not in nav_keys:continue
        for r in a.get("requests",[]):
            request_id=r.get("request_id")
            if request_id not in requests:unknown_requests.add(request_id);continue
            if requests[request_id]!="navigation":continue
            applied.add(key)
            if any(r.get("pressed",{}).values()):nonzero.add(key)
    return {"task_id":task_id,"target":target,"outcome":end["event"],"reason":end.get("reason"),
        "run_id":start["run_id"],"total_seconds":round(timestamp(end)-timestamp(start),3),
        "phase_frames":dict(counts),"phase_seconds":{k:round(v,3) for k,v in seconds.items()},
        "navigation_room_segments":[{"points":len(s),"net_px":round(math.dist(s[0],s[-1]),2)} for s in segments],
        "navigation_path_px":round(path,2),"navigation_net_px":round(net,2),
        "path_efficiency":round(net/path,4) if path else None,
        "navigation_speed_px_s":round(path/seconds["navigation"],2) if seconds["navigation"] else None,
        "navigation_commands":len(navigation_commands),"commands_by_phase":dict(Counter(e.get("controller_phase","unknown") for e in sent)),
        "navigation_command_reversals":dict(command_reversals),"navigation_displacement_reversals":dict(movement_reversals),
        "confirmation":{"consumed_frame":consumed_frame,"confirmed_frame":confirmed_frame,
            "delay_frames":confirmed_frame-consumed_frame if confirmed_frame is not None else None},
        "navigation_applied_frames":len(applied) if has_applied else None,
        "navigation_nonzero_frames":len(nonzero) if has_applied else None,
        "navigation_input_coverage":round(len(nonzero)/len(nav_keys),4) if nav_keys and has_applied and not unknown_requests else None,
        "unknown_input_requests":len(unknown_requests),"excluded_discontinuities":discontinuities,
        "legacy_command_attribution":any("task_id" not in e for e in sent)}


def audit(recording,events_path,run_id=None,task_id=None):
    state={};frames=[r for r in records(recording,state) if r.get("type")=="frame"]
    events_state={};events=list(records(events_path,events_state))
    starts={e["task_id"]:e for e in events if e.get("event")=="task_started"}
    results=[]
    for end in events:
        if end.get("event") not in {"task_completed","task_blocked","task_cancelled"}:continue
        start=starts.get(end.get("task_id"))
        if not start or (run_id and start.get("run_id")!=run_id) or (task_id and start["task_id"]!=task_id):continue
        if not any(in_task(f,start,end) for f in frames):continue
        results.append(analyze_task(start,end,frames,events))
    return {"recording":str(Path(recording).resolve()),"events":str(Path(events_path).resolve()),**state,"tasks":results,
        "measurement":"Seconds and distances use consecutive same-room, same-phase samples (max 3-frame/250ms gap). Missing boundary intervals are excluded. Combat includes hostile projectiles; pickup confirmation and handoff waits are separate. Old command attribution is inferred by enclosing task/run/time; missing input attribution is unknown. Command and displacement reversals are distinct; neither alone proves snake navigation."}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording",type=Path);parser.add_argument("--events",type=Path,required=True)
    parser.add_argument("--run-id");parser.add_argument("--task-id");parser.add_argument("--output",type=Path)
    args=parser.parse_args();path=args.recording/"frames.jsonl.gz" if args.recording.is_dir() else args.recording
    result=audit(path,args.events,args.run_id,args.task_id);text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+"\n",encoding="utf-8")
    print(text)


if __name__=="__main__":main()
