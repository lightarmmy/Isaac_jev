"""Local game client for an SSH-forwarded policy service; no Torch or weights required."""
import asyncio
import json
import time
from .actions import Action
from .bridge import request_control
from .features import contract_for, policy_observation,extended_profile
from .protocol import MAX_MESSAGE_BYTES
from .rooms import combat_active


class PolicyClient:
    def __init__(self, port=19528, timeout=2):
        self.port=port; self.timeout=timeout
        self.reader=None; self.writer=None
        self.lock=asyncio.Lock()

    async def connect(self):
        if self.timeout<=0:raise ValueError("request_timeout must be positive")
        self.reader,self.writer=await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1",self.port,limit=MAX_MESSAGE_BYTES+1),self.timeout)

    async def request(self, payload):
        async with self.lock:
            if self.writer is None:raise RuntimeError("Policy service is disconnected")
            message=(json.dumps(payload,allow_nan=False,separators=(",",":"))+"\n").encode()
            if len(message)>MAX_MESSAGE_BYTES:raise ValueError("Policy request too large")
            try:
                async def exchange():
                    self.writer.write(message);await self.writer.drain()
                    return await self.reader.readline()
                line=await asyncio.wait_for(exchange(),self.timeout)
                if not line:raise ConnectionError("Policy service disconnected")
                reply=json.loads(line)
                if not isinstance(reply,dict) or not reply.get("ok"):raise RuntimeError(str(reply))
                return reply
            except BaseException:
                # Never consume a timed-out prediction as the response to a newer observation.
                await self.close()
                raise

    async def close(self):
        if self.writer:
            self.writer.close()
            try:await self.writer.wait_closed()
            except ConnectionError:pass
        self.reader=None;self.writer=None


def validate_description(reply):
    cfg=reply.get("config")
    if not isinstance(cfg,dict) or any(cfg.get(k)!=v for k,v in contract_for(cfg).items()):
        raise ValueError("Server/local rules or feature contract mismatch; update both sides")
    if cfg.get("active_head_present") or cfg.get("active_context") or cfg.get("active_head_trained"):
        raise ValueError("This remote client supports the movement/shooting policy with active items disabled")
    if not isinstance(cfg.get("numeric_names"),list) or len(cfg["numeric_names"])!=cfg.get("numeric_size"):
        raise ValueError("Invalid numeric feature metadata")
    Action(0,False,cfg.get("hold_frames"))
    return cfg


def prediction_action(reply, frame, cfg):
    identity={"run_id":frame["run_id"],"room_id":frame["room"]["id"],"game_frame":frame["game_frame"]}
    if reply.get("observation")!=identity:raise ValueError("Prediction belongs to another observation")
    m,s=reply.get("move_id"),reply.get("shoot_id")
    if type(m) is not int or not 0<=m<9 or type(s) is not int or not 0<=s<5:
        raise ValueError("Invalid policy branch IDs")
    if reply.get("action_id")!=m*5+s or reply.get("use_active") is not False:
        raise ValueError("Invalid policy action")
    return Action(m*5+s,False,cfg["hold_frames"])


async def remote_infer(args):
    if args.startup_timeout<=0 or (args.max_seconds is not None and args.max_seconds<=0):
        raise ValueError("Timeouts must be positive")
    client=PolicyClient(args.policy_port,args.request_timeout)
    mode_requested=False;running_since=None;previous=None;waiting_since=time.monotonic()
    try:
        print("Waiting for L40S service readiness through the SSH tunnel...",flush=True)
        while True:
            try:
                await client.connect()
                description=await client.request({"command":"describe"})
                break
            except (ConnectionError,OSError,asyncio.TimeoutError):
                await client.close()
                if time.monotonic()-waiting_since>args.startup_timeout:
                    raise RuntimeError("Policy unavailable; open Start_Server_Tunnel.cmd and check the service log")
                await asyncio.sleep(0.5)
        cfg=validate_description(description)
        print(json.dumps({"policy_ready":True,"backend":description["backend"],"mode":args.mode,
                          "condition":cfg["deployment_condition"],"control_context":cfg.get("control_context",True),
                          "observation_lag_frames":cfg["observation_to_action_frame_lag"],"local_model_loaded":False}),flush=True)
        while True:
            if running_since is not None and args.max_seconds is not None and time.monotonic()-running_since>=args.max_seconds:
                print("Timed inference completed.",flush=True);break
            request={"command":"get_frame","include_history":True}
            if cfg.get('feature_version', '').startswith('combat-semantic-v7-'):request['include_semantic_history']=True
            if cfg.get('feature_version','').startswith('combat-feedback-v9-'):request['include_feedback_history']=True
            if cfg.get('feature_version','').startswith('combat-tactical-v10-'):request['include_tactical_history']=True
            reply=await request_control(request,port=args.port)
            status=reply["status"];frame=reply.get("frame");age=status.get("last_frame_age_ms")
            if not mode_requested:
                if status.get("connected") and frame and age is not None and age<100:
                    result=await request_control({"command":"set_mode","mode":args.mode},port=args.port)
                    if not result.get("ok"):raise RuntimeError("Cannot start inference: "+str(result))
                    mode_requested=True
                elif time.monotonic()-waiting_since>args.startup_timeout:
                    raise RuntimeError("No fresh game frame; connect and unpause the experiment game")
                else:await asyncio.sleep(0.05)
                continue
            if not status.get("connected"):raise ConnectionError("Isaac disconnected")
            if not frame or frame["mode"]!=args.mode:
                if running_since is not None:
                    print(json.dumps({"inference_stopped":True,"reason":"human_takeover_or_mode_changed",
                        "observed_mode":frame.get("mode") if frame else None,
                        "mode_reason":frame.get("mode_reason","unknown") if frame else None,
                        "room_id":frame.get("room",{}).get("id") if frame else None,
                        "game_frame":frame.get("game_frame") if frame else None}),flush=True);break
                if time.monotonic()-waiting_since>args.startup_timeout:raise RuntimeError("Requested mode did not start")
                await asyncio.sleep(0.05);continue
            if running_since is None:running_since=time.monotonic()
            key=(frame["run_id"],frame["room"]["id"],frame["game_frame"])
            if key==previous or age is None or age>=100:
                await asyncio.sleep(0.01);continue
            previous=key
            # Room traversal/buttons belong to the future planner, even in shadow mode.
            if frame["player"].get("dead") or not combat_active(frame,extended_profile(cfg.get('feature_version'))):
                await asyncio.sleep(0.02);continue
            if "policy_history" not in reply:raise RuntimeError("Bridge needs v3 history support")
            started=time.perf_counter()
            prompt,numeric,names=policy_observation(frame,condition=cfg["deployment_condition"],
                history_facts=reply['tactical_history'] if cfg.get('feature_version','').startswith('combat-tactical-v10-') else reply['feedback_history'] if cfg.get('feature_version','').startswith('combat-feedback-v9-') else reply['semantic_history'] if cfg.get('feature_version', '').startswith('combat-semantic-v7-') else reply['policy_history'],include_active=False,include_controls=cfg.get("control_context",True),feature_profile=cfg.get('feature_version') if extended_profile(cfg.get('feature_version')) else None)
            if names!=cfg["numeric_names"]:raise ValueError("Numeric feature order mismatch")
            observation={"run_id":frame["run_id"],"room_id":frame["room"]["id"],"game_frame":frame["game_frame"]}
            prediction=await client.request({"command":"predict","prompt":prompt,"numeric":numeric,
                "feature_version":cfg["feature_version"],"head_architecture":cfg["head_architecture"],
                "rules_sha256":cfg["rules_sha256"],"observation":observation})
            action=prediction_action(prediction,frame,cfg)
            response_ms=(time.perf_counter()-started)*1000
            # Keep the original frame identity; bridge + Lua independently reject stale/changed-room actions.
            if response_ms+age>=200:
                result={"ok":False,"error":"prediction exceeded 200ms observation deadline"}
            else:result=await request_control(action.as_command(frame),port=args.port)
            print(json.dumps({"frame":key,"action":action.action_id,"mode":args.mode,
                "move_id":prediction["move_id"],"shoot_id":prediction["shoot_id"],
                "tokens":prediction.get("tokens"),"model_ms":prediction["model_ms"],
                "response_ms":round(response_ms,2),"command_accepted":result.get("ok"),
                "control_injected":args.mode=="ai" and bool(result.get("ok")),
                "error":result.get("error") or (result.get("ack",{}).get("reason") if not result.get("ok") else None)}),flush=True)
    finally:
        await client.close()
        if mode_requested:
            try:await request_control({"command":"set_mode","mode":"manual"},port=args.port)
            except (ConnectionError,asyncio.TimeoutError):pass
