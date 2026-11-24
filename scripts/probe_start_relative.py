"""One bounded right-input probe in a clear lab room; always release its own lease."""
import asyncio
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from isaac_jev.actions import Action,EXECUTION_TIMING
from isaac_jev.bridge import request_control
from isaac_jev.rooms import combat_active


async def main():
    lease=None;deadline=time.monotonic()+45;previous=None;source=None
    try:
        while time.monotonic()<deadline:
            r=await request_control({"command":"get_frame"});f=r.get("frame");s=r["status"]
            if f and s.get("last_frame_age_ms",10000)<80 and f["mode"]=="manual" and not s.get("control_lease"):
                p=f["player"];room=f["room"];pos=p["position"];v=p["velocity"]
                obstacles=room.get("grid",[])+f.get("entities",[])
                safe=room["clear"] and not combat_active(f) and not p.get("dead") and p.get("controls_enabled")
                safe=safe and abs(v["x"])+abs(v["y"])<.1 and room["top_left"]["x"]+80<pos["x"]<room["bottom_right"]["x"]-80
                safe=safe and not any(math.hypot(e.get("position",{}).get("x",0)-(pos["x"]+12),e.get("position",{}).get("y",0)-pos["y"])<40 for e in obstacles)
                if safe and (s.get("hello") or {}).get("input_timing")==EXECUTION_TIMING:
                    if previous and previous["run_id"]==f["run_id"] and f["game_frame"]>=previous["game_frame"]+3:source=f;break
                    if previous is None:previous=f
            await asyncio.sleep(.1)
        if source is None:raise RuntimeError("No fresh, advancing, stationary player in a clear unobstructed lab room; probe not sent")
        r=await request_control({"command":"acquire_control","owner":"bounded-timing-probe"})
        if not r.get("ok"):raise RuntimeError(r.get("error"))
        lease=r["lease"]
        r=await request_control({"command":"set_mode","mode":"ai","lease_id":lease["id"]})
        if not r.get("ok"):raise RuntimeError(r.get("error"))
        source=(await request_control({"command":"get_frame"}))["frame"]
        # Deliberately consume about three logic frames of the admission budget.
        # Keep the original observation identity; do not renew it on arrival.
        await asyncio.sleep(.095)
        ack=await request_control({**Action(20,False,6).as_command(source),"lease_id":lease["id"]})
        request_id=(ack.get("ack") or {}).get("request_id");samples=[]
        end=time.monotonic()+.45
        while time.monotonic()<end:
            r=await request_control({"command":"get_frame"});f=r.get("frame")
            if f:samples.append({"frame":f["game_frame"],"position":f["player"]["position"],"execution":f.get("execution"),"applied":f.get("applied_controls")})
            await asyncio.sleep(.01)
        applied={a["applied"]["game_frame"] for a in samples if a.get("applied") and any(p["request_id"]==request_id for p in a["applied"].get("requests",[]))}
        result={"source_frame":source["game_frame"],"ack":ack,"actual_query_frames":sorted(applied),
            "before":source["player"]["position"],"after":samples[-1]["position"] if samples else None,"samples":samples}
        (ROOT/".local/isaac-skill/timing-input-probe.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps({k:v for k,v in result.items() if k!="samples"},indent=2))
    finally:
        if lease:
            status=(await request_control({"command":"get_status"}))["status"]
            if status.get("control_lease")==lease:
                await request_control({"command":"set_mode","mode":"manual","lease_id":lease["id"]})
                await request_control({"command":"release_control","lease_id":lease["id"]})


if __name__=="__main__":asyncio.run(main())
