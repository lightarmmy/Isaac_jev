import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from isaac_jev.actions import Action
from isaac_jev.features import history_summary
from isaac_jev.knowledge import KnowledgeStore
from isaac_jev.navigation import Navigator
from isaac_jev.skill_runtime import GameRuntime
from isaac_jev.skill_service import create_registry
from isaac_jev.skill_state import GameMemory
from isaac_jev.mock import sample_frame
from isaac_jev.bridge import BridgeServer


def frame(n=0):
    f=sample_frame(n);f["planner_schema"]=1;f["entities"]=[];f["room"].update(id="1:0:17",type=1,clear=True,
        top_left={"x":40,"y":40},bottom_right={"x":400,"y":320},grid_width=9,
        navigation_cells=[[r*9+c,80+c*40,80+r*40] for r in range(6) for c in range(8)],
        doors=[{"slot":0,"position":{"x":60,"y":120},"open":True,"locked":False,"target_room_index":16,"target_dimension":0,"target_room_type":4}])
    f["player"]["position"]={"x":120,"y":120};f["player"]["inventory"]={"coins":0,"keys":1,"bombs":1,"collectibles":[],"card":0,"pill_color":0}
    f["floor"]={"id":"1:0:0","dimension":0,"rooms":[{"index":17,"type":1,"visited":1,"clear":True,"display_flags":1,"links":[]},
        {"index":16,"type":4,"visited":0,"clear":False,"display_flags":0,"links":[]},
        {"index":99,"type":7,"visited":0,"clear":False,"display_flags":0,"links":[]}]}
    return f


def pickup(uid="item",shop=False,variant=100):
    return {"uid":uid,"kind":"pickup","variant":variant,"subtype":1,"position":{"x":300,"y":120},"size":10,
            "pickup_shop":shop,"pickup_price":0,"pickup_name":"The Sad Onion","pickup_wait":0}


class StateAndNavigationTests(unittest.TestCase):
    def test_hidden_nodes_and_blind_item_identity_do_not_leak(self):
        f=frame();f["entities"]=[{**pickup(),"pickup_blind":True}]
        mem=GameMemory();mem.update({"frame":f,"status":{"connected":True,"last_frame_age_ms":0}})
        snapshot=mem.snapshot("full")
        self.assertNotIn(99,[n["index"] for n in snapshot["floor_map"]["rooms"]])
        self.assertIsNone(snapshot["pickups"][0]["item_id"])
        self.assertIsNone(snapshot["raw_frame"]["entities"][0]["subtype"])
        self.assertNotIn("floor",snapshot["raw_frame"])

    def test_decision_survives_new_frames_but_expires_with_resources(self):
        mem=GameMemory();f=frame();reply={"frame":f,"status":{"connected":True,"last_frame_age_ms":0}}
        mem.update(reply);old=mem.decision_id()
        f["game_frame"]+=1;f["player"]["position"]["x"]+=4;mem.update(reply)
        self.assertEqual(old,mem.decision_id())
        f["player"]["inventory"]["keys"]=0;mem.update(reply)
        self.assertNotEqual(old,mem.decision_id())

    def test_free_shop_goods_cannot_be_pickup_targets_or_crossed(self):
        f=frame();f["room"]["type"]=2;f["entities"]=[pickup(shop=True)]
        mem=GameMemory();mem.update({"frame":f,"status":{"last_frame_age_ms":0}})
        self.assertFalse(any(a["kind"].startswith("collect") for a in mem.actions()))
        nav=Navigator(f,{"kind":"collect_pickup","target_id":"item"})
        with self.assertRaisesRegex(ValueError,"view-only"):nav.step(f)
        nav=Navigator(f,{"kind":"enter_door","target_id":"door-0"})
        path,obstacles=nav.path(f,(60,120))
        self.assertTrue(path);self.assertTrue(obstacles)

    def test_navigation_does_not_cut_diagonal_corners(self):
        f=frame();f["room"]["grid"]=[{"position":{"x":120,"y":80},"collision":1,"kind":"wall"},
                                      {"position":{"x":80,"y":120},"collision":1,"kind":"wall"}]
        f["player"]["position"]={"x":80,"y":80}
        nav=Navigator(f,{"kind":"collect_pickup","target_id":"item"})
        with self.assertRaises(ValueError):nav.path(f,(200,200))

    def test_visible_spawn_cooldown_does_not_hide_a_pickup_goal(self):
        f=frame();f["room"]["type"]=4;f["entities"]=[{**pickup(),"pickup_wait":20}]
        mem=GameMemory();mem.update({"frame":f,"status":{"last_frame_age_ms":0}})
        self.assertTrue(any(a["kind"]=="collect_treasure_item" for a in mem.actions()))

    def test_older_parallel_read_does_not_reset_the_run_map(self):
        mem=GameMemory();f=frame(20);mem.update({"frame":f,"status":{"control_epoch":1}})
        self.assertFalse(mem.update({"frame":frame(19),"status":{"control_epoch":1}}))
        self.assertEqual(mem.frame["game_frame"],20);self.assertEqual(mem.instance,1)
        self.assertTrue(mem.update({"frame":frame(0),"status":{"control_epoch":2}}))
        self.assertEqual(mem.instance,2)


class KnowledgeTests(unittest.TestCase):
    def test_cache_kind_identity_revision_and_missing_facts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=KnowledgeStore(tmp)
            entry={"item_kind":"collectible","item_id":1,"summary":"Improves tears; exact value not yet checked.","status":"partial",
                "sources":[{"url":"https://isaac.huijiwiki.com/wiki/Test_item","excerpt":"Fixture excerpt for a collected item effect. This is test evidence only."}]}
            first=store.upsert(entry);self.assertTrue(store.get("collectible",1)["cache_hit"])
            self.assertEqual(store.get("trinket",1)["status"],"missing")
            self.assertTrue(store.upsert(entry)["unchanged"])
            entry["summary"]+=" Extra checked condition.";second=store.upsert(entry)
            self.assertNotEqual(first["knowledge_revision"],second["knowledge_revision"])
            self.assertEqual(len(list((Path(tmp)/"history").glob("*.json"))),1)
            entry["sources"][0]["excerpt"]="Enable JavaScript and Cookies to continue."
            with self.assertRaises(ValueError):store.upsert(entry)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.runtime=GameRuntime(self.temp.name);self.f=frame();self.lease=None;self.commands=[]
        async def control(payload):
            self.commands.append(copy.deepcopy(payload));cmd=payload["command"]
            if cmd in {"get_frame","get_status"}:
                status={"connected":True,"last_frame_age_ms":0,"control_leases_supported":True,"control_lease":self.lease}
                return {"ok":True,"status":status,"frame":copy.deepcopy(self.f),"policy_history":history_summary(self.f,[])}
            if cmd=="acquire_control":self.lease={"id":"lease","owner":"test","epoch":1};return {"ok":True,"lease":self.lease}
            if cmd=="set_mode":self.f["mode"]=payload["mode"];return {"ok":True}
            if cmd=="release_control":self.lease=None;return {"ok":True}
            return {"ok":True}
        self.runtime.control=control
        async def connect():self.runtime.policy_config={};self.runtime.backend={"fixture":True};return {"ready":True}
        self.runtime.connect_policy=connect

    async def asyncTearDown(self):
        if self.runtime.enabled:await self.runtime.stop()
        self.temp.cleanup()

    async def start_planning(self):
        await self.runtime.start()
        for _ in range(8):self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.phase,"planning")

    async def test_enter_room_waits_for_combat_and_settling(self):
        await self.start_planning()
        await self.runtime.execute(self.runtime.memory.decision_id(),"door-0")
        self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertTrue(any(c["command"]=="action" for c in self.commands))
        self.f["room"]["id"]="1:0:16";self.f["room"]["type"]=4
        self.f["entities"]=[sample_frame()["entities"][0]]
        async def score(source,history,generation):return Action(19),source,generation,50,49
        self.runtime.score=score
        self.f["game_frame"]+=1;await self.runtime.tick();await asyncio.sleep(0)
        self.assertEqual(self.runtime.phase,"combat");self.assertIsNotNone(self.runtime.task)
        self.f["entities"]=[]
        for _ in range(8):self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.phase,"planning");self.assertIsNone(self.runtime.task)
        self.assertTrue(any(e["event"]=="task_completed" for e in self.runtime.memory.events))

    async def test_pickup_requires_real_inventory_change(self):
        self.f["room"]["type"]=4;self.f["entities"]=[pickup()]
        await self.start_planning()
        await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"]=[];self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertTrue(any(e["event"]=="task_blocked" for e in self.runtime.memory.events))
        self.f["entities"]=[pickup()];self.f["game_frame"]+=1;await self.runtime.tick()
        await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"]=[];self.f["player"]["inventory"]["collectibles"]=[{"id":1,"count":1}]
        self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertEqual(list(self.runtime.memory.events)[-2]["event"],"task_completed")

    async def test_takeover_cancels_pending_inference_without_reclaiming_input(self):
        await self.start_planning();self.f["entities"]=[sample_frame()["entities"][0]]
        gate=asyncio.Event()
        async def score(*args):await gate.wait()
        self.runtime.score=score;self.f["game_frame"]+=1;await self.runtime.tick()
        self.lease=None;self.f["mode"]="manual";self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertFalse(self.runtime.enabled);self.assertIsNone(self.runtime.prediction)
        self.assertEqual(sum(c["command"]=="set_mode" and c["mode"]=="ai" for c in self.commands),1)

    async def test_stale_room_prediction_never_gets_relabelled(self):
        await self.start_planning();source=copy.deepcopy(self.f);source["entities"]=[sample_frame()["entities"][0]]
        self.f["entities"]=source["entities"];self.runtime.phase="combat";self.runtime.generation=3
        async def ready():return Action(19),source,3,50,49
        task=asyncio.create_task(ready());await task;self.runtime.prediction=task
        self.f["room"]["id"]="1:0:16";self.f["game_frame"]+=1
        await self.runtime.tick()
        self.assertFalse(any(c["command"]=="action" for c in self.commands))
        if self.runtime.prediction:self.runtime.prediction.cancel();await asyncio.gather(self.runtime.prediction,return_exceptions=True)

    async def test_extension_tools_are_discoverable_and_callable(self):
        store=KnowledgeStore(Path(self.temp.name)/"knowledge");registry=create_registry(self.runtime,store)
        async def extra():return {"extension":True}
        registry.register("extra_query","Fixture extension",{},[],extra)
        self.assertIn("extra_query",[t["name"] for t in registry.catalogue()])
        self.assertTrue((await registry.call("extra_query",{}))["extension"])

    async def test_feedback_navigation_crosses_door_then_collects_real_drop(self):
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"door-0")
        seen=0;move={"x":0,"y":0}
        async def step_world():
            nonlocal seen,move
            self.f["game_frame"]+=1;await self.runtime.tick()
            for command in self.commands[seen:]:
                if command["command"]=="action":move=command["move"]
            seen=len(self.commands)
            self.f["player"]["position"]["x"]+=move["x"]*4
            self.f["player"]["position"]["y"]+=move["y"]*4
            if self.f["room"]["id"]=="1:0:17" and self.f["player"]["position"]["x"]<55:
                self.f["room"].update(id="1:0:16",type=4,doors=[])
                self.f["player"]["position"]={"x":350,"y":120};self.f["entities"]=[pickup()];move={"x":0,"y":0}
            if self.f["room"]["id"]=="1:0:16" and self.f["entities"] and abs(self.f["player"]["position"]["x"]-300)<15:
                self.f["entities"]=[];self.f["player"]["inventory"]["collectibles"]=[{"id":1,"count":1}]
        for _ in range(50):
            await step_world()
            if self.runtime.task is None:break
        self.assertEqual(self.f["room"]["id"],"1:0:16");self.assertIsNone(self.runtime.task)
        await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        for _ in range(50):
            await step_world()
            if self.runtime.task is None:break
        self.assertEqual(self.f["player"]["inventory"]["collectibles"],[{"id":1,"count":1}])
        self.assertEqual(sum(e["event"]=="task_completed" for e in self.runtime.memory.events),2)


class LeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_exclusive_writer_and_revoked_tokens_stay_invalid(self):
        hub=BridgeServer(port=0);hub.latest=frame();hub.last_received=time.monotonic()
        class Writer:
            def is_closing(self):return False
            def write(self,data):
                msg=json.loads(data);hub._acks[msg["request_id"]].set_result({"ok":True})
            async def drain(self):pass
        hub.mod_writer=Writer()
        first=await hub.control({"command":"acquire_control","owner":"skill"});token=first["lease"]["id"]
        self.assertFalse((await hub.control({"command":"acquire_control","owner":"other"}))["ok"])
        action=Action(20).as_command(hub.latest)
        self.assertFalse((await hub.control(action))["ok"])
        self.assertTrue((await hub.control({**action,"lease_id":token}))["ok"])
        hub.revoke_control()
        self.assertFalse((await hub.control({**action,"lease_id":token}))["ok"])
        self.assertFalse((await hub.control({"command":"set_mode","mode":"ai","lease_id":token}))["ok"])


if __name__=="__main__":unittest.main()
