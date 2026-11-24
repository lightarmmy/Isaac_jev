"""Portable CLI and stdio MCP adapter for the skill's local game tools."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

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


def setup():
    cfg=configuration();root=Path(cfg["project_root"]).resolve()
    try:
        result=request("/health")
        if result["service"]!="isaac-play-tools-v1" or Path(result["project_root"]).resolve()!=root:
            raise RuntimeError("Port is occupied by a different project/service")
        return {"already_running":True,**result}
    except (OSError,urllib.error.URLError):pass
    if not root.exists():raise ValueError("Project path missing; edit connection.json or ISAAC_JEV_PROJECT")
    source=root/"src"
    if not (source/"isaac_jev/skill_service.py").exists():source=SKILL/"scripts/vendor"
    if not (source/"isaac_jev/skill_service.py").exists():raise ValueError("Missing packaged runtime; run the project installer")
    env=dict(os.environ);env["PYTHONPATH"]=str(source)+os.pathsep+env.get("PYTHONPATH","")
    local=root/".local/isaac-skill";local.mkdir(parents=True,exist_ok=True)
    args=[sys.executable,"-u","-m","isaac_jev.skill_service","--project-root",str(root),"--port",str(cfg.get("port",9530)),
          "--bridge-port",str(cfg.get("bridge_port",9527)),"--policy-port",str(cfg.get("policy_port",19528))]
    with (local/"service.log").open("ab",buffering=0) as log:
        proc=subprocess.Popen(args,cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,start_new_session=os.name!="nt")
    (local/"service.pid").write_text(str(proc.pid))
    for _ in range(40):
        if proc.poll() is not None:raise RuntimeError("Tool service failed; inspect "+str(local/"service.log"))
        try:return {"started":True,**request("/health")}
        except (OSError,urllib.error.URLError):time.sleep(0.25)
    raise RuntimeError("Tool service not ready; inspect "+str(local/"service.log"))


def call(name,args):return request("/call",{"tool":name,"arguments":args}).get("result")


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


def main():
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True)
    for name in ("setup","list","mcp","shutdown"):sub.add_parser(name)
    p=sub.add_parser("call");p.add_argument("tool");p.add_argument("--args-file",type=Path);p.add_argument("--json",default="{}")
    args=parser.parse_args()
    try:
        if args.command=="mcp":mcp();return
        if args.command=="setup":result=setup()
        elif args.command=="list":result=request("/tools")["tools"]
        elif args.command=="shutdown":result=request("/call",{"tool":"shutdown_service","arguments":{}})
        else:
            payload=json.loads(args.args_file.read_text(encoding="utf-8-sig") if args.args_file else args.json)
            result=call(args.tool,payload)
        print(json.dumps({"ok":True,"result":result},ensure_ascii=False,allow_nan=False,indent=2))
    except Exception as exc:
        print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False));raise SystemExit(1)


if __name__=="__main__":main()
