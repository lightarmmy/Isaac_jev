"""Single input owner: execute navigation goals and hand combat to the L40S policy."""
import asyncio
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from .bridge import request_control
from .features import policy_observation
from .navigation import Navigator
from .remote import PolicyClient, validate_description, prediction_action
from .rooms import combat_active
from .skill_state import GameMemory


class GameRuntime:
    def __init__(self,root,bridge_port=9527,policy_port=19528):
        self.root=Path(root);self.bridge_port=bridge_port;self.policy_port=policy_port
        self.memory=GameMemory();self.policy=None;self.policy_config=None;self.backend=None
        self.enabled=False;self.lease=None;self.phase="manual";self.task=None;self.generation=0
        self.prediction=None;self.last_scored=None;self.last_action_frame=None;self.safe_since=None
        self.cursor=0;self.condition=asyncio.Condition();self.goal_handlers={};self.floor_started=None
        self.run_started=None;self.last_error=None;self.last_model_ms=None
        self.policy_lock=asyncio.Lock()
        self.last_agent_contact=time.monotonic()
        self.last_decision=None
        self.log_path=self.root/".local/isaac-skill/events.jsonl";self.log_path.parent.mkdir(parents=True,exist_ok=True)

    async def control(self,payload):return await request_control(payload,port=self.bridge_port)

    async def event(self,kind,**fields):
        self.cursor+=1
        record={"event":kind,"cursor":self.cursor,"time":datetime.now(timezone.utc).isoformat(),**fields}
        self.memory.events.append(record)
        with self.log_path.open("a",encoding="utf-8") as out:out.write(json.dumps(record,ensure_ascii=False,allow_nan=False)+"\n")
        async with self.condition:self.condition.notify_all()

    async def connect_policy(self):
        async with self.policy_lock:return await self._connect_policy()

    async def _connect_policy(self):
        if self.policy and self.policy.writer:return {"ready":True,"backend":self.backend,"config":self.policy_config}
        self.policy=PolicyClient(self.policy_port,2)
        await self.policy.connect();description=await self.policy.request({"command":"describe"})
        self.policy_config=validate_description(description);self.backend=description["backend"]
        return {"ready":True,"backend":self.backend,"config":self.policy_config,"local_model_loaded":False}

    def register_goal(self,kind,candidates,navigator,completed):
        if kind in {"enter_door","inspect_shop","collect_pickup","collect_treasure_item","visit_room","skip_pickup","finish_floor_demo"}:
            raise ValueError("Cannot replace built-in goal semantics")
        self.goal_handlers[kind]=(candidates,navigator,completed)

    def actions(self):
        if self.enabled and (self.task or self.phase!="planning"):return []
        actions=self.memory.actions()
        for provider,_,_ in self.goal_handlers.values():actions.extend(provider(self.memory))
        return actions

    def observe(self,detail="summary"):
        result=self.memory.snapshot(detail)
        result.update(autoplay=self.enabled,controller_phase=self.phase,event_cursor=self.cursor,
            execution={k:v for k,v in (self.task or {}).items() if k not in {"navigator","before"}},
            battle_backend=self.backend,last_model_ms=self.last_model_ms,last_error=self.last_error)
        result["actions"]=self.actions() if result.get("phase")=="planning" else []
        return result

    async def start(self,wait_seconds=20):
        if self.enabled:return self.observe()
        if not 0<=wait_seconds<=25:raise ValueError("Startup wait must be 0..25 seconds")
        await self.connect_policy();deadline=time.monotonic()+wait_seconds
        while True:
            reply=await self.control({"command":"get_frame","include_history":True});accepted=self.memory.update(reply)
            age=reply.get("status",{}).get("last_frame_age_ms")
            if accepted and reply["status"].get("connected") and age is not None and age<100:break
            if time.monotonic()>=deadline:break
            await asyncio.sleep(0.05)
        state=self.memory.snapshot();frame=reply.get("frame")
        if not state.get("connected") or state.get("last_frame_age_ms") is None or state["last_frame_age_ms"]>100:
            raise ValueError("Unpause the experimental game in manual mode before start_autoplay")
        cells=frame.get("room",{}).get("navigation_cells") if frame else None
        if not frame or frame.get("planner_schema")!=1 or not cells or not isinstance(cells[0],list):
            raise ValueError("Reload Isaac Jev Bridge 0.4.1 before using the skill")
        if not reply["status"].get("control_leases_supported"):raise ValueError("Restart the bridge with skill control support")
        result=await self.control({"command":"acquire_control","owner":"isaac-skill-"+uuid.uuid4().hex})
        if not result.get("ok"):raise ValueError(result.get("error","Control unavailable"))
        self.lease=result["lease"];self.generation+=1
        result=await self.control({"command":"set_mode","mode":"ai","lease_id":self.lease["id"]})
        if not result.get("ok"):
            await self.control({"command":"release_control","lease_id":self.lease["id"]});self.lease=None
            raise ValueError(result.get("error","Cannot enter ai mode"))
        self.enabled=True;self.phase="starting";self.floor_started=self.memory.floor_id;self.run_started=self.memory.run_id
        self.safe_since=None;self.last_action_frame=None
        await self.event("autoplay_started",backend=self.backend)
        return self.observe()

    async def stop(self,reason="stopped"):
        self.enabled=False;self.generation+=1;self.phase="manual"
        if self.prediction:
            self.prediction.cancel();await asyncio.gather(self.prediction,return_exceptions=True);self.prediction=None
        if self.task:
            self.task["status"]="cancelled";await self.event("task_cancelled",task_id=self.task["task_id"],reason=reason)
            self.task=None
        if self.lease:
            try:
                # Never reset a controller that acquired input after our lease was revoked.
                reply=await self.control({"command":"get_status"})
                if reply["status"].get("control_lease",{} )==self.lease:
                    await self.control({"command":"set_mode","mode":"manual","lease_id":self.lease["id"]})
                    await self.control({"command":"release_control","lease_id":self.lease["id"]})
            except (OSError,asyncio.TimeoutError):pass
            self.lease=None
        await self.event("autoplay_stopped",reason=reason)
        return self.observe()

    async def execute(self,decision_id,action_id,reason="",timeout_s=120):
        if not self.enabled:raise ValueError("Call start_autoplay first")
        if self.task or self.phase!="planning":raise ValueError("Wait for a planning decision; another task/combat is active")
        if decision_id!=self.memory.decision_id():raise ValueError("Decision expired; observe_game again")
        target=next((a for a in self.actions() if a["action_id"]==action_id),None)
        if not target:raise ValueError("Choose an action_id from get_available_actions")
        if not 1<=timeout_s<=600:raise ValueError("Task timeout must be 1..600 seconds")
        if target["kind"]=="skip_pickup":
            self.memory.ignored.add(target["target_id"]);await self.event("pickup_skipped",target=target,reason=reason);return self.observe()
        if target["kind"]=="finish_floor_demo":
            remaining=[n["index"] for n in self.memory.rooms.values() if not n.get("visited") or not n.get("clear")]
            await self.stop("floor_demo_finished")
            return {"finished":True,"all_known_rooms_complete":not remaining,"remaining_rooms":remaining,"state":self.observe()}
        frame=self.memory.frame
        self.task={"task_id":uuid.uuid4().hex,"target":copy.deepcopy(target),"status":"running","reason":reason[:1000],
            "origin_room":frame["room"]["id"],"started":time.monotonic(),"timeout_s":timeout_s,
            "before":copy.deepcopy(frame["player"]),"navigator":None,"arrived":False,"last_key":None}
        self.phase="navigation"
        await self.event("task_started",task_id=self.task["task_id"],target=target,reason=reason[:1000])
        return {"task_id":self.task["task_id"],"status":"running","event_cursor":self.cursor}

    async def wait(self,event_cursor,timeout_s=20):
        if type(event_cursor) is not int or not 0<=timeout_s<=25:raise ValueError("Invalid cursor/timeout (max 25 seconds)")
        async with self.condition:
            if self.cursor<=event_cursor:
                try:await asyncio.wait_for(self.condition.wait_for(lambda:self.cursor>event_cursor),timeout_s)
                except asyncio.TimeoutError:pass
        return self.observe()

    async def send(self,action,frame):
        if not self.enabled or not self.lease:return
        # Initial skill capabilities exclude all active, bomb and card use.
        if action.use_active:raise ValueError("Active item use is not enabled in this skill")
        result=await self.control({**action.as_command(frame),"lease_id":self.lease["id"]})
        if not result.get("ok"):self.last_error=result.get("error") or result.get("ack",{}).get("reason")
        self.last_action_frame=(frame["run_id"],frame["room"]["id"],frame["game_frame"])

    async def score(self,frame,history,generation):
        cfg=self.policy_config;started=time.perf_counter()
        prompt,numeric,names=policy_observation(frame,history_facts=history,include_active=False,include_controls=cfg.get("control_context",True))
        if names!=cfg["numeric_names"]:raise ValueError("Numeric feature order mismatch")
        observation={"run_id":frame["run_id"],"room_id":frame["room"]["id"],"game_frame":frame["game_frame"]}
        reply=await self.policy.request({"command":"predict","prompt":prompt,"numeric":numeric,"observation":observation,
            "feature_version":cfg["feature_version"],"head_architecture":cfg["head_architecture"],"rules_sha256":cfg["rules_sha256"]})
        return prediction_action(reply,frame,cfg),frame,generation,(time.perf_counter()-started)*1000,reply["model_ms"]

    async def finish_task(self,status,reason=""):
        task=self.task
        if not task:return
        await self.event("task_"+status,task_id=task["task_id"],target=task["target"],reason=reason,
            before=task["before"],after=copy.deepcopy(self.memory.frame["player"]))
        self.task=None;self.phase="planning"
        self.last_decision=self.memory.decision_id()
        await self.event("planning_required",decision_id=self.memory.decision_id())

    async def navigate(self,frame):
        task=self.task;target=task["target"];kind=target["kind"]
        if time.monotonic()-task["started"]>task["timeout_s"]:
            await self.finish_task("blocked","execution timeout");return
        if kind in {"enter_door","inspect_shop"}:
            if target["target_id"]=="current-room" or task["origin_room"]!=frame["room"]["id"]:
                if target.get("destination_index") is not None and frame["room"]["id"].split(":")[-1]!=str(target["destination_index"]):
                    await self.finish_task("blocked","entered unexpected room");return
                if kind=="inspect_shop":self.memory.shops_seen.add(frame["room"]["id"])
                await self.finish_task("completed");return
        elif kind=="visit_room":
            current=frame["room"]["id"].split(":")[-1]
            if current==target["target_id"]:await self.finish_task("completed");return
            route=self.memory.route(target["target_id"])
            if len(route)<2:await self.finish_task("blocked","no known route");return
            door=next((d for d in frame["room"]["doors"] if str(d.get("target_room_index"))==route[1]),None)
            if not door:await self.finish_task("blocked","next route door unavailable");return
            local_target={"kind":"enter_door","target_id":"door-"+str(door["slot"])}
            if not task["navigator"] or task["navigator"].room_id!=frame["room"]["id"]:task["navigator"]=Navigator(frame,local_target)
        elif kind in {"collect_pickup","collect_treasure_item"}:
            if task["origin_room"]!=frame["room"]["id"]:await self.finish_task("blocked","pickup room changed");return
            present=any(e.get("uid")==target["target_id"] for e in frame["entities"])
            if not present:
                before=task["before"];after=frame["player"]
                changed=any(before.get(k)!=after.get(k) for k in ("inventory","health","active"))
                await self.finish_task("completed" if changed else "blocked","" if changed else "target disappeared without confirmed inventory/resource change")
                return
        elif kind in self.goal_handlers:
            _,factory,complete=self.goal_handlers[kind]
            if complete(self.memory,task):await self.finish_task("completed");return
            if not task["navigator"]:task["navigator"]=factory(frame,target)
        else:await self.finish_task("blocked","unknown goal kind");return
        if not task["navigator"]:task["navigator"]=Navigator(frame,target)
        key=(frame["run_id"],frame["room"]["id"],frame["game_frame"])
        if key==task["last_key"]:return
        task["last_key"]=key
        try:
            action=task["navigator"].step(frame)
            if action:await self.send(action,frame)
        except ValueError as exc:await self.finish_task("blocked",str(exc))

    async def tick(self):
        reply=await self.control({"command":"get_frame","include_history":True})
        if self.enabled and self.lease and reply.get("status",{}).get("control_epoch",self.lease["epoch"])<self.lease["epoch"]:return
        accepted=self.memory.update(reply)
        if not self.enabled:return
        if not accepted:return
        if time.monotonic()-self.last_agent_contact>120:
            await self.stop("agent_inactive_timeout");return
        status=reply["status"];frame=reply.get("frame")
        if not status.get("connected") or status.get("control_lease")!=self.lease:
            await self.stop("human_takeover_or_disconnected");return
        if not frame or status.get("last_frame_age_ms",10000)>200:return
        if frame["mode"]!="ai":
            if self.phase!="starting":await self.stop(frame.get("mode_reason","mode_changed"))
            return
        if frame["player"].get("dead"):await self.stop("player_dead");return
        if self.memory.floor_id!=self.floor_started or self.memory.run_id!=self.run_started:
            await self.stop("floor_or_run_changed");return
        fighting=combat_active(frame)
        if fighting and self.task and time.monotonic()-self.task["started"]>self.task["timeout_s"]:
            await self.stop("combat_task_timeout");return
        if fighting:
            self.safe_since=None
            if self.phase!="combat":
                self.phase="combat";self.generation+=1
                await self.event("combat_started",room_id=frame["room"]["id"])
            if self.prediction and self.prediction.done():
                action,source,generation,elapsed,model_ms=self.prediction.result();self.prediction=None
                self.last_model_ms=model_ms
                if generation==self.generation and source["run_id"]==frame["run_id"] and source["room"]["id"]==frame["room"]["id"] and 0<=frame["game_frame"]-source["game_frame"]<6 and elapsed<200:
                    await self.send(action,source)
            key=(frame["run_id"],frame["room"]["id"],frame["game_frame"])
            if self.prediction is None and key!=self.last_scored:
                self.last_scored=key;self.prediction=asyncio.create_task(self.score(copy.deepcopy(frame),reply["policy_history"],self.generation))
            return
        if self.prediction:
            self.prediction.cancel();await asyncio.gather(self.prediction,return_exceptions=True);self.prediction=None;self.generation+=1
        if self.safe_since is None:self.safe_since=(frame["room"]["id"],frame["game_frame"])
        if self.safe_since[0]!=frame["room"]["id"]:self.safe_since=(frame["room"]["id"],frame["game_frame"])
        if frame["game_frame"]-self.safe_since[1]<6:return
        if self.task:
            self.phase="navigation";await self.navigate(frame)
        elif self.phase!="planning":
            self.phase="planning";self.last_decision=self.memory.decision_id()
            await self.event("planning_required",decision_id=self.last_decision)
        elif self.memory.decision_id()!=self.last_decision:
            self.last_decision=self.memory.decision_id()
            await self.event("planning_updated",decision_id=self.last_decision)

    async def run(self):
        try:
            while True:
                try:await self.tick()
                except (OSError,ValueError,RuntimeError,KeyError,TypeError,asyncio.TimeoutError) as exc:
                    self.last_error=str(exc)
                    if self.enabled:await self.stop("runtime_error: "+str(exc))
                await asyncio.sleep(0.02)
        finally:
            if self.enabled:await self.stop("service_stopped")
            if self.policy:await self.policy.close()
