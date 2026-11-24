"""Portable CLI and stdio MCP adapter for the skill's local game tools."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

SKILL=Path(__file__).resolve().parents[1]


def configuration():
    path=SKILL/"connection.json"
    if path.exists():cfg=json.loads(path.read_text(encoding="utf-8-sig"))
    else:cfg={"project_root":str(SKILL.parents[1]),"port":9530,"bridge_port":9527,"policy_port":19528}
    if os.environ.get("ISAAC_JEV_PROJECT"):cfg["project_root"]=os.environ["ISAAC_JEV_PROJECT"]
    return cfg


def request(path,payload=None):
    cfg=configuration();url="http://127.0.0.1:"+str(cfg.get("port",9530))+path
    data=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode() if payload is not None else None
    req=urllib.request.Request(url,data=data,headers={"Content-Type":"application/json"})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=40) as response:reply=json.load(response)
    except urllib.error.HTTPError as exc:
        reply=json.loads(exc.read());raise RuntimeError(reply.get("error",str(exc)))
    if not reply.get("ok"):raise RuntimeError(reply.get("error","Tool service error"))
    return reply


def service_health(root):
    try:
        result=request("/health")
        if result["service"]!="isaac-play-tools-v1" or Path(result["project_root"]).resolve()!=root:
            raise RuntimeError("Port is occupied by a different project/service")
        return result
    except (OSError,urllib.error.URLError):return None


def prepare_launch(cfg):
    root=Path(cfg["project_root"]).resolve()
    if not root.exists():raise ValueError("Project path missing; edit connection.json or ISAAC_JEV_PROJECT")
    source=root/"src"
    if not (source/"isaac_jev/skill_service.py").exists():source=SKILL/"scripts/vendor"
    if not (source/"isaac_jev/skill_service.py").exists():raise ValueError("Missing packaged runtime; run the project installer")
    local=root/".local/isaac-skill";warnings=[]
    try:
        local.mkdir(parents=True,exist_ok=True)
        # Check all files the controller needs before shutting down a live service.
        for name in ("events.jsonl","service.pid"):
            with (local/name).open("ab"):pass
    except OSError as exc:
        raise PermissionError(f"Cannot write service state at {local}: {exc}. Run the project launcher with workspace write access; a read-only sandbox cannot restart this service.") from exc
    log_path=local/"service.log"
    try:
        with log_path.open("ab"):pass
    except OSError as exc:
        alternate=local/("service-"+uuid.uuid4().hex[:8]+".log")
        try:
            with alternate.open("ab"):pass
        except OSError as alternate_error:
            raise PermissionError(f"Cannot open service logs at {local}: {alternate_error}. Check this process's write access before changing ACLs.") from alternate_error
        warnings.append(f"Cannot append {log_path}: {exc}; using {alternate}.");log_path=alternate
    return {"cfg":cfg,"root":root,"source":source,"local":local,"log_path":log_path,"warnings":warnings}


def preflight():
    cfg=configuration();root=Path(cfg["project_root"]).resolve();health=service_health(root);plan=prepare_launch(cfg)
    return {"writable":True,"service_running":health is not None,"log_path":str(plan["log_path"]),"warnings":plan["warnings"]}


def setup(plan=None):
    cfg=configuration();root=Path(cfg["project_root"]).resolve();health=service_health(root)
    if health:return {"already_running":True,**health}
    plan=plan or prepare_launch(cfg);local=plan["local"];log_path=plan["log_path"]
    env=dict(os.environ);env["PYTHONPATH"]=str(plan["source"])+os.pathsep+env.get("PYTHONPATH","")
    args=[sys.executable,"-u","-m","isaac_jev.skill_service","--project-root",str(root),"--port",str(cfg.get("port",9530)),
          "--bridge-port",str(cfg.get("bridge_port",9527)),"--policy-port",str(cfg.get("policy_port",19528))]
    with log_path.open("ab",buffering=0) as log:
        proc=subprocess.Popen(args,cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,start_new_session=os.name!="nt")
    (local/"service.pid").write_text(str(proc.pid))
    for _ in range(40):
        if proc.poll() is not None:raise RuntimeError("Tool service failed; inspect "+str(log_path))
        health=service_health(root)
        if health:return {"started":True,"log_path":str(log_path),"warnings":plan["warnings"],**health}
        time.sleep(0.25)
    raise RuntimeError("Tool service not ready; inspect "+str(log_path))


def restart():
    cfg=configuration();root=Path(cfg["project_root"]).resolve();health=service_health(root)
    plan=prepare_launch(cfg) # A denied preflight must leave the current service running.
    if health:
        if call("observe_game",{}).get("autoplay"):raise RuntimeError("Stop the automatic session before restarting the tool service")
        request("/call",{"tool":"shutdown_service","arguments":{}})
        for _ in range(40):
            if service_health(root) is None:break
            time.sleep(0.25)
        else:raise RuntimeError("Previous tool service has not stopped; no replacement was launched")
    return setup(plan)


def call(name,args):return request("/call",{"tool":name,"arguments":args}).get("result")


def arguments_for(args):
    if args.args_file is not None:payload=json.loads(args.args_file.read_text(encoding="utf-8-sig"))
    elif args.json is not None:payload=json.loads(args.json)
    elif args.arg:
        tool=next((t for t in request("/tools")["tools"] if t["name"]==args.tool),None)
        if tool is None:raise ValueError("Unknown tool: "+args.tool)
        properties=tool["inputSchema"].get("properties",{});payload={}
        for key,raw in args.arg:
            if key not in properties:raise ValueError("Unknown argument: "+key)
            kind=properties[key].get("type")
            if kind=="string":value=raw
            elif kind=="integer":
                try:value=int(raw)
                except ValueError:raise ValueError(key+" must be an integer") from None
            elif kind=="number":
                try:value=float(raw)
                except ValueError:raise ValueError(key+" must be a number") from None
                if not math.isfinite(value):raise ValueError(key+" must be finite")
            elif kind=="boolean":
                if raw not in {"true","false"}:raise ValueError(key+" must be true or false")
                value=raw=="true"
            elif kind=="array" and properties[key].get("items",{}).get("type")=="string":
                payload.setdefault(key,[]).append(raw);continue
            else:raise ValueError("Use --args-file for nested argument: "+key)
            if key in payload:raise ValueError("Repeated argument: "+key)
            payload[key]=value
    else:payload={}
    if not isinstance(payload,dict):raise ValueError("Arguments must be a JSON object")
    return payload


def check_game(interval):
    if not math.isfinite(interval) or not .25<=interval<=5:raise ValueError("Check interval must be 0.25..5 seconds")
    first=call("observe_game",{"detail":"full"});time.sleep(interval)
    second=call("observe_game",{"detail":"full"})
    frame=(second.get("raw_frame") or {});player=second.get("player") or {};age=second.get("last_frame_age_ms")
    before,after=first.get("game_frame"),second.get("game_frame")
    same=bool(first.get("run_id") and first.get("run_id")==second.get("run_id") and first.get("run_instance")==second.get("run_instance"))
    delta=after-before if same and type(before) is int and type(after) is int else None
    fresh=age is not None and age<100;advancing=delta is not None and delta>0;alive=bool(player) and player.get("dead") is False
    ready=bool(second.get("connected") and fresh and advancing and alive and frame.get("mode")=="manual" and not second.get("autoplay"))
    if ready:next_step="Game checks passed. Confirm doctor reports L40S ready, then call start_autoplay while keeping the game in the foreground."
    elif not second.get("connected"):next_step="Reconnect the experimental game bridge before starting."
    elif not player:next_step="Enter a playable experimental run and check again."
    elif player.get("dead"):next_step="The character is dead. Prepare a new run manually and check again."
    elif second.get("autoplay") or frame.get("mode")!="manual":next_step="Inspect the existing controller/session before requesting another start."
    elif not same:next_step="The run changed between samples. Check the new run again."
    else:next_step="Return to the experimental game, check its pause menu, unpause if needed and keep it in the foreground. Recheck; do not inject Esc blindly."
    return {"ready_for_start":ready,"connected":second.get("connected"),"mode":frame.get("mode"),
        "run_id":second.get("run_id"),"run_instance":second.get("run_instance"),"room_id":second.get("room_id"),
        "frame_before":before,"frame_after":after,"frame_delta":delta,"sample_interval_s":interval,
        "frames_advancing":advancing,"game_fresh":fresh,"last_frame_age_ms":age,"alive":alive,"autoplay":second.get("autoplay"),
        "next_step":next_step}


def mcp():
    setup()
    for line in sys.stdin:
        message=None
        try:
            message=json.loads(line)
            if "id" not in message:continue
            method=message.get("method");params=message.get("params",{})
            if method=="initialize":
                requested=params.get("protocolVersion")
                supported={"2024-11-05","2025-03-26","2025-06-18","2025-11-25"}
                result={"protocolVersion":requested if requested in supported else "2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"isaac-play","version":"1.0.0"}}
            elif method=="ping":result={}
            elif method=="tools/list":result={"tools":request("/tools")["tools"]}
            elif method=="tools/call":
                try:
                    value=call(params["name"],params.get("arguments",{}))
                    result={"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False,allow_nan=False)}],"isError":False}
                except Exception as exc:result={"content":[{"type":"text","text":str(exc)}],"isError":True}
            else:raise ValueError("Unsupported MCP method")
            response={"jsonrpc":"2.0","id":message["id"],"result":result}
        except Exception as exc:response={"jsonrpc":"2.0","id":message.get("id") if isinstance(message,dict) else None,"error":{"code":-32603,"message":str(exc)}}
        print(json.dumps(response,ensure_ascii=False,allow_nan=False),flush=True)


def main(argv=None):
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    for name in ("setup","list","mcp","shutdown","preflight","restart"):sub.add_parser(name)
    p=sub.add_parser("call");p.add_argument("tool");source=p.add_mutually_exclusive_group()
    source.add_argument("--args-file",type=Path);source.add_argument("--json")
    source.add_argument("--arg",nargs=2,action="append",metavar=("NAME","VALUE"),help="Typed primitive parameter; repeat for other fields or string-array entries")
    p=sub.add_parser("check");p.add_argument("--interval",type=float,default=2)
    args=parser.parse_args(argv)
    try:
        if args.command=="mcp":mcp();return
        if args.command=="setup":result=setup()
        elif args.command=="preflight":result=preflight()
        elif args.command=="restart":result=restart()
        elif args.command=="list":result=request("/tools")["tools"]
        elif args.command=="shutdown":result=request("/call",{"tool":"shutdown_service","arguments":{}})
        elif args.command=="check":result=check_game(args.interval)
        else:
            payload=arguments_for(args)
            result=call(args.tool,payload)
        print(json.dumps({"ok":True,"result":result},ensure_ascii=False,allow_nan=False,indent=2))
    except Exception as exc:
        print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=="__main__":main()
