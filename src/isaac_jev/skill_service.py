"""Local tool registry/HTTP service usable from any terminal or MCP-capable harness."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
from .knowledge import KnowledgeStore
from .skill_runtime import GameRuntime

SERVICE_ID="isaac-play-tools-v1"


class ToolRegistry:
    def __init__(self):self.tools={}
    def register(self,name,description,properties,required,handler):
        if name in self.tools:raise ValueError("Duplicate tool: "+name)
        self.tools[name]=({"name":name,"description":description,"inputSchema":{"type":"object","properties":properties,"required":required,"additionalProperties":False}},handler)
    def catalogue(self):return [s for s,_ in self.tools.values()]
    async def call(self,name,args):
        if name not in self.tools:raise ValueError("Unknown tool: "+name)
        if not isinstance(args,dict):raise ValueError("Arguments must be an object")
        schema,handler=self.tools[name]
        if set(args)-set(schema["inputSchema"]["properties"]):raise ValueError("Unknown argument")
        if any(k not in args for k in schema["inputSchema"]["required"]):raise ValueError("Missing required argument")
        types={"string":str,"integer":int,"number":(int,float),"array":list,"object":dict}
        for key,value in args.items():
            prop=schema["inputSchema"]["properties"][key];kind=prop.get("type")
            if kind in types and (not isinstance(value,types[kind]) or (kind in {"integer","number"} and type(value) is bool)):
                raise ValueError("Invalid type for "+key)
            if "enum" in prop and value not in prop["enum"]:raise ValueError("Invalid value for "+key)
        return await handler(**args)


def create_registry(runtime,store):
    registry=ToolRegistry()
    def add(name,description,props,required,fn):registry.register(name,description,props,required,fn)
    async def observe_game(detail="summary"):return runtime.observe(detail)
    async def map_tool(format="both"):
        result=runtime.memory.map();return result["text"] if format=="text" else result
    async def room_tool(room_index=None):
        if room_index is None:return runtime.observe("full")
        room=runtime.memory.rooms.get(str(room_index))
        if not room:return {"status":"unknown"}
        result={k:v for k,v in room.items() if k!="last_state"}
        if "last_state" in room:result["last_state"]=runtime.memory.known_frame(room["last_state"])
        return result
    async def item_tool(items):return store.lookup(items)
    async def search_tool(item_kind,item_id,names):return store.search(item_kind,item_id,names)
    async def read_tool(page_url):return store.read_request(page_url)
    async def upsert_tool(entry):return store.upsert(entry)
    async def actions_tool():return {"decision_id":runtime.memory.decision_id(),"actions":runtime.actions()}
    async def status_tool():return runtime.observe()
    async def rules_tool(topics=None):
        rules={"scope":"Explore this floor via normal doors; collect ordinary drops and treasure-room items; inspect shops only. No shopping, chest/button/machine use, bombs, active/card/pill use or automatic stairs.",
            "controls":"One local input owner. F8/pause/disconnect cancel the automatic session. Start again only when explicitly requested.",
            "pickups":"Only choose generated IDs. Shop items must be avoided, including free shop goods. Treasure choices may be mutually exclusive. Full red-health pickups may be unavailable. Use resource/inventory changes to confirm collection.",
            "knowledge":"Game state supplies actual stats/prices. Huiji Wiki supplies item effect evidence. Unknown effects remain unknown; consult the harness browser then import a supported summary.",
            "combat":"The L40S v004 policy controls movement/shooting only. Retain hostile residual projectiles as combat. Weapon transformations may not be supported by the trained head."}
        return {k:v for k,v in rules.items() if topics is None or k in topics}
    async def doctor():
        snapshot=runtime.observe();policy=None
        try:policy=await runtime.connect_policy()
        except (OSError,ValueError,RuntimeError,asyncio.TimeoutError) as exc:policy={"ready":False,"error":str(exc),"next_step":"Open Start_Server_Tunnel.cmd"}
        policy.pop("config",None)
        game_status=(runtime.memory.reply or {}).get("status",{})
        return {"service":SERVICE_ID,"game_connected":game_status.get("connected",False),"frame_available":runtime.memory.frame is not None,
            "mod_version":(game_status.get("hello") or {}).get("mod_version"),"input_timing":(game_status.get("hello") or {}).get("input_timing"),"planner_schema":snapshot.get("planner_schema"),
            "bridge_control_leases":bool((runtime.memory.reply or {}).get("status",{}).get("control_leases_supported")),
            "game_fresh":snapshot.get("last_frame_age_ms") is not None and snapshot["last_frame_age_ms"]<200,
            "battle_ai":policy,"scope":"first demo","local_model_loaded":False}
    string={"type":"string"};integer={"type":"integer"};number={"type":"number"}
    add("doctor","Check game telemetry and the L40S policy without injecting inputs.",{},[],doctor)
    add("observe_game","Read player, inventory, map, pickups, available goals and execution state.",{"detail":{"type":"string","enum":["summary","full"]}},[],observe_game)
    add("get_floor_map","Read the complete known room graph and text map.",{"format":{"type":"string","enum":["both","text","graph"]}},[],map_tool)
    add("get_room_details","Read current full telemetry or a previously seen room.",{"room_index":integer},[],room_tool)
    add("get_item_details","Read cached item facts, missing status and source evidence.",{"items":{"type":"array","items":{"type":"object"}}},["items"],item_tool)
    add("wiki_search","Generate a Huiji Wiki search for visible names, kind and ID; use the harness web tool.",{"item_kind":string,"item_id":integer,"names":{"type":"array","items":string}},["item_kind","item_id","names"],search_tool)
    add("wiki_read","Validate a Wiki URL and hand off article reading to the harness browser.",{"page_url":string},["page_url"],read_tool)
    add("knowledge_upsert","Save a source-backed item summary and preserve its previous revision.",{"entry":{"type":"object"}},["entry"],upsert_tool)
    add("get_rules","Read maintained rules for scope, controls, pickups, knowledge or combat.",{"topics":{"type":"array","items":string}},[],rules_tool)
    add("get_available_actions","List legal goal IDs for the current stable decision.",{},[],actions_tool)
    add("start_autoplay","Wait up to 20 seconds for an unpaused manual game, then acquire input and connect battle AI.",{"wait_seconds":number},[],runtime.start)
    add("execute_plan","Submit one generated goal; navigation and combat execute asynchronously.",{"decision_id":string,"action_id":string,"reason":string,"timeout_s":number},["decision_id","action_id"],runtime.execute)
    add("wait_for_decision","Wait for a new execution/combat/planning event, at most 25 seconds.",{"event_cursor":integer,"timeout_s":number},["event_cursor"],runtime.wait)
    add("get_execution_status","Read task status, controller phase and latest event cursor.",{},[],status_tool)
    add("stop_autoplay","Cancel all automatic input/tasks and release the current lease.",{"reason":string},[],runtime.stop)
    return registry


def load_extensions(registry,runtime,folder):
    folder=Path(folder)
    if not folder.exists():return
    for file in sorted(folder.glob("*.py")):
        spec=importlib.util.spec_from_file_location("isaac_ext_"+file.stem,file)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.register(registry,runtime)


async def serve(root,port=9530,bridge_port=9527,policy_port=19528):
    root=Path(root).resolve();runtime=GameRuntime(root,bridge_port,policy_port)
    store=KnowledgeStore(root/".local/knowledge_cache");registry=create_registry(runtime,store)
    load_extensions(registry,runtime,root/"skill_extensions")
    loop=asyncio.get_running_loop();stop=asyncio.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def response(self,status,value):
            data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.path=="/health":self.response(200,{"ok":True,"service":SERVICE_ID,"project_root":str(root),"tools":len(registry.tools)})
            elif self.path=="/tools":self.response(200,{"ok":True,"tools":registry.catalogue()})
            else:self.response(404,{"ok":False,"error":"unknown path"})
        def do_POST(self):
            try:
                if self.headers.get("Origin") or self.headers.get("Content-Type","").split(";")[0]!="application/json":
                    raise ValueError("Use the local JSON tool client")
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=2*1024*1024:raise ValueError("Invalid body size")
                payload=json.loads(self.rfile.read(length))
                if self.path!="/call":raise ValueError("Unknown path")
                if payload.get("tool")=="shutdown_service":
                    future=asyncio.run_coroutine_threadsafe(runtime.stop("service_shutdown"),loop);future.result(5)
                    loop.call_soon_threadsafe(stop.set);self.response(200,{"ok":True});return
                loop.call_soon_threadsafe(setattr,runtime,"last_agent_contact",time.monotonic())
                future=asyncio.run_coroutine_threadsafe(registry.call(payload["tool"],payload.get("arguments",{})),loop)
                try:result=future.result(35)
                except TimeoutError:future.cancel();raise
                self.response(200,{"ok":True,"result":result})
            except Exception as exc:self.response(400,{"ok":False,"error":str(exc) or type(exc).__name__})
    server=ThreadingHTTPServer(("127.0.0.1",port),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    task=asyncio.create_task(runtime.run())
    def runtime_ended(done):
        if not done.cancelled() and done.exception():print("Runtime stopped: "+str(done.exception()),file=sys.stderr,flush=True)
        stop.set()
    task.add_done_callback(runtime_ended)
    print(json.dumps({"ready":True,"service":SERVICE_ID,"port":server.server_port}),flush=True)
    try:await stop.wait()
    finally:
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        await asyncio.to_thread(server.shutdown);server.server_close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--project-root",type=Path,required=True)
    parser.add_argument("--port",type=int,default=9530);parser.add_argument("--bridge-port",type=int,default=9527)
    parser.add_argument("--policy-port",type=int,default=19528)
    args=parser.parse_args()
    try:asyncio.run(serve(args.project_root,args.port,args.bridge_port,args.policy_port))
    except KeyboardInterrupt:pass


if __name__=="__main__":main()
