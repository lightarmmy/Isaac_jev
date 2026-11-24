"""Core bridge commands run without Torch; training/inference dependencies are lazy."""
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import time
from .actions import Action, action_map, label_from_input
from .bridge import BridgeServer, request_control
from .recording import iter_messages
from .protocol import encode, decode, PROTOCOL_VERSION

def doctor(game: Path):
    print("Game directory:",game)
    print("Game executable:",(game/"isaac-ng.exe").exists())
    print("Bridge mod installed:",(game/"mods"/"isaac_jev_bridge"/"main.lua").exists())
    print("REPENTOGON folders:",[p.name for p in game.glob("*repentogon*")])
    if shutil.which("nvidia-smi"):
        subprocess.run(["nvidia-smi","--query-gpu=name,memory.total","--format=csv,noheader"],check=False)
    print("Bridge port: localhost:9527; F8 always returns manual control.")

async def serve(args):
    hub=BridgeServer(port=args.port,record_root=Path(args.record) if args.record else None,status_path=Path(args.status_file),recording_enabled=not args.no_record,record_purpose=args.record_purpose)
    await hub.start()
    print(f"Bridge listening on 127.0.0.1:{hub.port}; awaiting Isaac mod.",flush=True)
    if hub.recorder:print("Recording:",hub.recorder.path.resolve(),flush=True)
    publisher=asyncio.create_task(hub.publish_status())
    try:
        async with hub.server:await hub.server.serve_forever()
    finally:
        publisher.cancel()
        await hub.close()

def inspect_recording(path):
    frames=events=manual=active=0;actions={};runs=set();gaps=0;last={};phases=set()
    for msg in iter_messages(Path(path)):
        if msg.get("type")!="frame":events+=1;continue
        frames+=1;runs.add(msg.get("run_id"));phases.add(msg.get("capture_phase"))
        run=msg["run_id"];n=msg["game_frame"]
        if run in last:gaps+=max(0,n-last[run]-1)
        last[run]=n
        if msg.get("mode")=="manual":
            manual+=1;a,use=label_from_input(msg["human_input"]);active+=int(use);actions[a]=actions.get(a,0)+1
    print(json.dumps({"frames":frames,"manual_frames":manual,"events":events,"runs":len(runs),
        "frame_gaps":gaps,"capture_phases":sorted(str(p) for p in phases),"active_triggers":active,
        "observed_joint_actions":len(actions),"action_counts":actions},ensure_ascii=False,indent=2))

async def probe(args):
    response=await request_control({"command":"get_frame"},port=args.port)
    frame=response.get("frame")
    if not frame:raise RuntimeError("No game frame; connect the mod and enter a run first")
    if not frame["room"].get("clear") or any(e.get("kind") in {"projectile","laser","bomb"} or
            (e.get("kind")=="enemy" and not e.get("friendly") and not e.get("dead")) for e in frame["entities"]):
        raise RuntimeError("Probe requires a clear room without enemies, projectiles, lasers, or bombs")
    print("Probe: 2-frame right movement, no shooting/item use; manual control restored immediately afterwards.")
    try:
        print(await request_control({"command":"set_mode","mode":"ai"},port=args.port))
        frame=(await request_control({"command":"get_frame"},port=args.port))["frame"]
        result=await request_control(Action(20,False,2).as_command(frame),port=args.port)
        print(result)
        if not result.get("ok"):raise RuntimeError("Probe action was rejected: "+str(result))
        await asyncio.sleep(0.12)
    finally:
        print(await request_control({"command":"set_mode","mode":"manual"},port=args.port))

async def mock_client(args):
    from .mock import sample_frame
    reader,writer=await asyncio.open_connection("127.0.0.1",args.port)
    writer.write(encode({"protocol_version":1,"type":"hello","client":"isaac_mod","mock":True}));await writer.drain()
    await reader.readline()
    async def commands():
        while line:=await reader.readline():
            msg=decode(line)
            writer.write(encode({"protocol_version":1,"type":"command_ack","request_id":msg.get("request_id"),"ok":True,"reason":"mock only"}))
            await writer.drain()
    task=asyncio.create_task(commands())
    try:
        for n in range(args.frames):
            frame=sample_frame(n);writer.write(encode(frame));await writer.drain();await asyncio.sleep(1/30)
    finally:
        task.cancel();writer.close();await writer.wait_closed()

def main():
    parser=argparse.ArgumentParser(description="Isaac Jev project CLI")
    sub=parser.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("doctor");p.add_argument("--game",type=Path,default=Path(".local/isaac-lab/game"))
    p=sub.add_parser("bridge");p.add_argument("--port",type=int,default=9527);p.add_argument("--record",default="recordings");p.add_argument("--status-file",default=".local/bridge_status.json")
    p.add_argument("--record-purpose",choices=["demonstration","diagnostic"],default="demonstration")
    p.add_argument("--no-record",action="store_true",help="Start without recording; retain record rotate capability")
    p=sub.add_parser("record");p.add_argument("operation",choices=["rotate","stop"]);p.add_argument("--purpose",choices=["demonstration","diagnostic"],default="demonstration");p.add_argument("--completed-purpose",choices=["demonstration","diagnostic"]);p.add_argument("--port",type=int,default=9527)
    for name in ["status","frame","probe"]:
        p=sub.add_parser(name);p.add_argument("--port",type=int,default=9527)
    p=sub.add_parser("mode");p.add_argument("mode",choices=["manual","shadow","ai"]);p.add_argument("--port",type=int,default=9527)
    sub.add_parser("actions")
    p=sub.add_parser("inspect");p.add_argument("path")
    p=sub.add_parser("room-timeline");p.add_argument("recordings");p.add_argument("--output",required=True)
    p=sub.add_parser("mock-client");p.add_argument("--frames",type=int,default=90);p.add_argument("--port",type=int,default=9527)
    p=sub.add_parser("prepare-dataset");p.add_argument("recordings");p.add_argument("--output",required=True);p.add_argument("--stride",type=int,default=1)
    p.add_argument("--validation-run",action="append",default=[],help="Reserve this complete run for validation; repeat for multiple runs")
    p.add_argument("--test-run",action="append",default=[],help="Reserve this complete run for final held-out testing; repeat for multiple runs")
    p.add_argument("--outcome-window-frames",type=int,default=30,help="Future outcome window in 30 Hz logic frames, 1..150; labels only, never observations")
    p.add_argument("--exclude-active-items",action="store_true",help="Exclude rooms with active item use/input and disable active policy context/output")
    p.add_argument("--action-lag-frames",type=int,default=1,help="Align actions this many logic frames after observation (1..6)")
    p.add_argument("--without-control-context",action="store_true",help="Keep physical history, omit explicit previous key labels from policy input")
    p=sub.add_parser("train");p.add_argument("dataset");p.add_argument("--output",required=True);p.add_argument("--model",default="Qwen/Qwen3.5-2B");p.add_argument("--epochs",type=int,default=10);p.add_argument("--max-length",type=int,default=3072);p.add_argument("--device",default="cuda");p.add_argument("--revision",default="main")
    p.add_argument("--feature-batch-size",type=int,default=16,help="Batch size for frozen feature extraction")
    p.add_argument("--feature-pad-multiple",type=int,default=0,help="Pad frozen-feature batches to fixed token buckets (0 disables)")
    p.add_argument("--patience",type=int,default=8,help="Stop after this many epochs without a meaningful validation NLL improvement")
    p.add_argument("--min-delta",type=float,default=0.005,help="Minimum NLL improvement to reset early stopping")
    p.add_argument("--cpu-threads",type=int,default=1)
    p.add_argument("--move-transition-weight",type=float,default=1.0)
    p.add_argument("--shoot-transition-weight",type=float,default=1.0)
    p=sub.add_parser("infer");p.add_argument("checkpoint");p.add_argument("--port",type=int,default=9527);p.add_argument("--mode",choices=["shadow","ai"],default="shadow");p.add_argument("--device",default="cuda")
    p.add_argument("--max-seconds",type=float,help="Stop after this many seconds of a running game; restore manual mode")
    p.add_argument("--startup-timeout",type=float,default=600,help="Seconds to wait for an unpaused game before starting")
    p=sub.add_parser("remote-infer",help="Use an SSH-forwarded L40S policy; no local model/checkpoint required")
    p.add_argument("--port",type=int,default=9527);p.add_argument("--policy-port",type=int,default=19528)
    p.add_argument("--mode",choices=["shadow","ai"],default="shadow")
    p.add_argument("--max-seconds",type=float);p.add_argument("--startup-timeout",type=float,default=600)
    p.add_argument("--request-timeout",type=float,default=2)
    args=parser.parse_args()
    if args.cmd=="doctor":doctor(args.game)
    elif args.cmd=="bridge":asyncio.run(serve(args))
    elif args.cmd=="record":
        print(json.dumps(asyncio.run(request_control({"command":"rotate_recording" if args.operation=="rotate" else "stop_recording","purpose":args.purpose,"completed_purpose":args.completed_purpose},port=args.port)),ensure_ascii=False,indent=2))
    elif args.cmd in {"status","frame"}:print(json.dumps(asyncio.run(request_control({"command":"get_"+args.cmd},port=args.port)),ensure_ascii=False,indent=2))
    elif args.cmd=="mode":print(json.dumps(asyncio.run(request_control({"command":"set_mode","mode":args.mode},port=args.port)),ensure_ascii=False,indent=2))
    elif args.cmd=="probe":asyncio.run(probe(args))
    elif args.cmd=="actions":print(json.dumps(action_map(),indent=2))
    elif args.cmd=="inspect":inspect_recording(args.path)
    elif args.cmd=="room-timeline":
        from .rooms import export_room_timeline
        print(json.dumps(export_room_timeline(args.recordings,args.output),ensure_ascii=False,indent=2))
    elif args.cmd=="mock-client":asyncio.run(mock_client(args))
    elif args.cmd=="prepare-dataset":
        from .training import prepare_dataset
        if set(args.validation_run)&set(args.test_run):parser.error("A run cannot be both validation and test")
        overrides={**{run:"validation" for run in args.validation_run},**{run:"test" for run in args.test_run}}
        prepare_dataset(Path(args.recordings),Path(args.output),args.stride,split_overrides=overrides,outcome_window_frames=args.outcome_window_frames,exclude_active_items=args.exclude_active_items,action_lag_frames=args.action_lag_frames,control_context=not args.without_control_context)
    elif args.cmd=="train":
        from .training import train
        train(args)
    elif args.cmd=="infer":
        from .training import infer
        asyncio.run(infer(args))
    elif args.cmd=="remote-infer":
        from .remote import remote_infer
        asyncio.run(remote_infer(args))
