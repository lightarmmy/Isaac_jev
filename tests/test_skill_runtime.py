import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import threading
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
from isaac_jev.pickups import PICKUP_CONFIRMATION_FRAMES


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
    def test_bomb_chest_is_named_without_becoming_a_pickup_goal(self):
        f=frame();f['entities']=[{**pickup(variant=51),'pickup_name':None}]
        memory=GameMemory();memory.update({'frame':f,'status':{'last_frame_age_ms':0}})
        self.assertEqual(memory.pickups()[0]['name'],'Bomb Chest')
        self.assertEqual(memory.pickups()[0]['kind'],'unsupported')
        self.assertFalse(any(a['kind'].startswith('collect') for a in memory.actions()))

    def test_game_item_categories_distinguish_familiar_from_active(self):
        f=frame();f["entities"]=[{**pickup(),"subtype":270,"pickup_item_type":4}]
        mem=GameMemory();mem.update({"frame":f,"status":{"last_frame_age_ms":0}})
        self.assertEqual(mem.pickups()[0]["item_category"],"familiar")
        self.assertFalse(mem.pickups()[0]["is_active"])
        f["entities"][0]["pickup_item_type"]=3;mem.update({"frame":f,"status":{}})
        self.assertTrue(mem.pickups()[0]["is_active"])

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
                status={"connected":True,"last_frame_age_ms":0,"control_leases_supported":True,"control_lease":self.lease,
                    "hello":{"input_timing":"start-relative-v1"}}
                return {"ok":True,"status":status,"frame":copy.deepcopy(self.f),"policy_history":history_summary(self.f,[])}
            if cmd=="acquire_control":self.lease={"id":"lease","owner":"test","epoch":1};return {"ok":True,"lease":self.lease}
            if cmd=="set_mode":self.f["mode"]=payload["mode"];return {"ok":True}
            if cmd=="release_control":self.lease=None;return {"ok":True}
            return {"ok":True}
        self.runtime.control=control
        async def connect():
            self.runtime.policy_config={};self.runtime.backend={"fixture":True};self.runtime.policy=SimpleNamespace(writer=True)
            return {"ready":True}
        self.runtime.connect_policy=connect

    async def asyncTearDown(self):
        if self.runtime.enabled:await self.runtime.stop()
        self.temp.cleanup()

    async def start_planning(self):
        await self.runtime.start()
        for _ in range(8):self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.phase,"planning")

    async def test_death_reason_survives_mode_and_lease_revocation(self):
        await self.start_planning()
        self.f['game_frame']+=1;self.f['player']['dead']=True
        self.f['mode']='manual';self.f['mode_reason']='player_dead';self.lease=None
        await self.runtime.tick()
        self.assertFalse(self.runtime.enabled)
        records=[json.loads(line) for line in self.runtime.log_path.read_text().splitlines()]
        self.assertEqual([row for row in records if row['event']=='autoplay_stopped'][-1]['reason'],'player_dead')

    async def test_clear_room_fire_contact_escapes_between_planning_tasks(self):
        await self.start_planning()
        self.f['entities']=[{'type':33,'kind':'enemy','active_enemy':False,'vulnerable':False,
            'hp':5,'size':13,'collision_class':4,'position':{'x':100,'y':120}}]
        self.f['game_frame']+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.phase,'navigation');self.assertIsNone(self.runtime.prediction)
        self.assertTrue(any(e['event']=='navigation_hazard_detected' for e in self.runtime.memory.events))
        actions=[c for c in self.commands if c['command']=='action']
        self.assertTrue(actions);self.assertGreater(actions[-1]['move']['x'],0)
        self.assertNotEqual(actions[-1]['controller_phase'],'combat')
        self.f['player']['position']['x']=150
        self.commands.clear()
        self.f['game_frame']+=1;await self.runtime.tick()
        self.assertTrue(any(c['command']=='action' and c['move']=={'x':0,'y':0} for c in self.commands))
        for _ in range(8):self.f['game_frame']+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.phase,'planning');self.assertIsNone(self.runtime.fire_escape)
        self.assertTrue(any(e['event']=='navigation_hazard_escaped' for e in self.runtime.memory.events))

    async def test_fire_escape_preserves_pickup_task_and_replans_after_leaving(self):
        self.f['room']['type']=4;self.f['entities']=[pickup()]
        await self.start_planning()
        await self.runtime.execute(self.runtime.memory.decision_id(),'pickup-item')
        task_id=self.runtime.task['task_id']
        self.f['entities'].append({'type':33,'size':13,'position':{'x':100,'y':120},'active_enemy':False,'kind':'enemy'})
        self.f['game_frame']+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.task['task_id'],task_id)
        self.f['player']['position']={'x':150,'y':160}
        for _ in range(8):self.f['game_frame']+=1;await self.runtime.tick()
        self.assertIsNotNone(self.runtime.task['navigator']);self.assertEqual(self.runtime.task['task_id'],task_id)
        self.assertFalse(any(e['event']=='task_blocked' for e in self.runtime.memory.events))

    async def test_fire_safety_does_not_reacquire_manual_or_revoked_control(self):
        self.f['entities']=[{'type':33,'size':13,'position':{'x':100,'y':120}}]
        self.commands.clear();await self.runtime.tick()
        self.assertFalse(any(c['command']=='action' for c in self.commands))
        self.f['entities']=[];await self.start_planning()
        self.f['entities']=[{'type':33,'size':13,'position':{'x':100,'y':120}}]
        self.lease=None;self.f['game_frame']+=1;self.commands.clear();await self.runtime.tick()
        self.assertFalse(any(c['command']=='action' for c in self.commands));self.assertFalse(self.runtime.enabled)

    async def test_fire_safety_overrides_pickup_confirmation_wait(self):
        self.f['room']['type']=4;self.f['entities']=[pickup()]
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),'pickup-item')
        self.f['entities']=[];self.f['game_frame']+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.task['stage'],'awaiting_pickup_confirmation')
        self.f['entities']=[{'type':33,'size':13,'kind':'enemy','active_enemy':False,'position':{'x':100,'y':120}}]
        self.f['game_frame']+=1;await self.runtime.tick()
        self.assertEqual(self.runtime.observe()['execution']['navigation']['stage'],'escape_fire')
        actions=[c for c in self.commands if c['command']=='action']
        self.assertGreater(actions[-1]['move']['x'],0)
        self.assertEqual(self.runtime.task['stage'],'awaiting_pickup_confirmation')

    async def test_feature_build_allows_frame_updates_and_keeps_original_identity(self):
        await self.start_planning()
        source=copy.deepcopy(self.f);entered=threading.Event();release=threading.Event()
        def build(*args,**kwargs):
            entered.set();release.wait(1);return 'fixture prompt',[],[]
        async def request(payload):
            return {'observation':payload['observation'],'move_id':1,'shoot_id':1,'action_id':6,'use_active':False,'model_ms':1}
        self.runtime.policy_config={'numeric_names':[],'hold_frames':3,'feature_version':'fixture','head_architecture':'fixture','rules_sha256':'fixture'}
        self.runtime.policy=SimpleNamespace(writer=True,request=request)
        with patch('isaac_jev.skill_runtime.policy_observation',side_effect=build):
            scoring=asyncio.create_task(self.runtime.score(source,[],self.runtime.generation))
            try:
                for _ in range(20):
                    if entered.is_set():break
                    await asyncio.sleep(.005)
                self.assertTrue(entered.is_set());self.assertFalse(scoring.done())
                self.f['game_frame']+=1;await self.runtime.tick()
                self.assertEqual(self.runtime.memory.frame['game_frame'],source['game_frame']+1)
            finally:release.set()
            _,returned,_,_,_=await scoring
        self.assertEqual(returned['game_frame'],source['game_frame'])

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
        for _ in range(PICKUP_CONFIRMATION_FRAMES):self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertTrue(any(e["event"]=="task_blocked" for e in self.runtime.memory.events))
        self.f["entities"]=[pickup()];self.f["game_frame"]+=1;await self.runtime.tick()
        await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"]=[];self.f["player"]["inventory"]["collectibles"]=[{"id":1,"count":1}]
        self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertEqual(list(self.runtime.memory.events)[-2]["event"],"task_completed")

    async def test_empty_pedestal_same_uid_completes_before_timeout_and_stops_input(self):
        self.f["room"]["type"]=4;self.f["entities"]=[pickup()]
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item",timeout_s=60)
        self.runtime.task["started"]-=self.runtime.task["timeout_s"]+1
        self.f["entities"][0]["subtype"]=0
        self.f["player"]["inventory"]["collectibles"]=[{"id":1,"count":1}]
        self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertIsNone(self.runtime.task);self.assertEqual(self.runtime.phase,"planning")
        complete=next(e for e in self.runtime.memory.events if e["event"]=="task_completed")
        self.assertEqual(complete["run_id"],self.f["run_id"]);self.assertEqual(complete["room_id"],self.f["room"]["id"])
        self.assertFalse(any(e["event"]=="task_blocked" for e in self.runtime.memory.events))
        self.assertEqual([c for c in self.commands if c["command"]=="action"][-1]["action_id"],0)
        self.assertFalse(any(a.get("target_id")=="item" for a in self.runtime.actions()))

    async def test_consumed_pedestal_waits_for_inventory_cache_without_more_approach(self):
        self.f["room"]["type"]=4;self.f["entities"]=[pickup()]
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"][0]["subtype"]=0
        for _ in range(3):self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertIsNotNone(self.runtime.task)
        self.assertTrue(all(c["action_id"]==0 for c in self.commands if c["command"]=="action"))
        self.f["player"]["inventory"]["collectibles"]=[{"id":1}]
        self.f["game_frame"]+=1;await self.runtime.tick();self.assertIsNone(self.runtime.task)

    async def test_unrelated_item_gain_does_not_confirm_selected_collectible(self):
        self.f["room"]["type"]=4;self.f["entities"]=[pickup()]
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"][0]["subtype"]=0;self.f["player"]["inventory"]["collectibles"]=[{"id":2}]
        for _ in range(PICKUP_CONFIRMATION_FRAMES+1):self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertFalse(any(e["event"]=="task_completed" for e in self.runtime.memory.events))
        self.assertTrue(any(e["event"]=="task_blocked" for e in self.runtime.memory.events))

    async def test_active_swap_with_persistent_uid_confirms_new_active(self):
        self.f["room"]["type"]=4;self.f["entities"]=[{**pickup(),"subtype":58}]
        self.f["player"]["active"]={"id":45,"charge":4}
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"][0]["subtype"]=45;self.f["player"]["active"]={"id":58,"charge":3}
        self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertIsNone(self.runtime.task);self.assertTrue(any(e["event"]=="task_completed" for e in self.runtime.memory.events))

    async def test_precharge_uses_fresh_commands_without_moving_during_charge(self):
        self.f['player']['weapons']=[{'slot':1,'type':2,'charge':3,'max_charge':6}]
        await self.start_planning();self.runtime.policy_config={'feature_version':'combat-build-terrain-weapons-v5'}
        await self.runtime.execute(self.runtime.memory.decision_id(),'door-0',prepare_for_combat=True)
        self.f['game_frame']+=1;await self.runtime.tick()
        command=[c for c in self.commands if c['command']=='action'][-1]
        self.assertEqual(command['move'],{'x':0,'y':0});self.assertEqual(command['shoot'],{'x':-1,'y':0})
        self.assertTrue(command['charge_hold']);self.assertEqual(command['handoff_room_id'],'1:0:16')
        self.assertEqual(self.runtime.task['stage'],'precharging')

    async def test_leech_and_trinket_delayed_confirmation(self):
        self.f["room"]["type"]=4
        for variant,item_id,delay in ((100,270,37),(350,6,36)):
            with self.subTest(variant=variant):
                self.f["entities"]=[{**pickup(variant=variant),"subtype":item_id}]
                await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
                self.f["entities"]=[];self.f["game_frame"]+=1;await self.runtime.tick()
                consumed_frame=self.f["game_frame"];command_count=len(self.commands)
                for _ in range(delay-1):
                    self.f["game_frame"]+=1;await self.runtime.tick()
                    self.assertIsNotNone(self.runtime.task)
                    self.assertEqual(self.runtime.observe()["execution"]["navigation"]["stage"],"awaiting_pickup_confirmation")
                self.assertFalse(any(c["command"]=="action" for c in self.commands[command_count:]))
                if variant==100:self.f["player"]["inventory"]["collectibles"]=[{"id":item_id,"count":1}]
                else:self.f["player"]["inventory"]["trinkets"]=[item_id,0]
                self.f["game_frame"]+=1;await self.runtime.tick()
                completed=[e for e in self.runtime.memory.events if e["event"]=="task_completed"][-1]
                self.assertEqual(completed["reason"],"late confirmation")
                self.assertEqual(completed["confirmation"]["delay_frames"],delay)
                self.assertEqual(completed["confirmation"]["consumed_frame"],consumed_frame)
                await self.runtime.stop()

    async def test_confirmation_deadline_and_real_timeout(self):
        self.f["room"]["type"]=4
        self.f["entities"]=[pickup()]
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.f["entities"]=[];self.f["game_frame"]+=1;await self.runtime.tick()
        self.f["game_frame"]+=PICKUP_CONFIRMATION_FRAMES-1;await self.runtime.tick()
        self.assertIsNotNone(self.runtime.task)
        self.f["game_frame"]+=1;await self.runtime.tick()
        blocked=[e for e in self.runtime.memory.events if e["event"]=="task_blocked"][-1]
        self.assertEqual(blocked["failure"]["reason_code"],"pickup_confirmation_timeout")
        self.f["entities"]=[pickup()];self.f["game_frame"]+=1;await self.runtime.tick()
        await self.runtime.execute(self.runtime.memory.decision_id(),"pickup-item")
        self.runtime.task["started"]-=self.runtime.task["timeout_s"]+1
        self.f["entities"]=[];self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertIsNone(self.runtime.task)

    async def test_navigation_momentum_recovery_keeps_task_running(self):
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"door-0")
        self.f["player"].update(position={"x":385,"y":300},velocity={"x":8,"y":0})
        self.f["game_frame"]+=1;await self.runtime.tick()
        self.assertIsNotNone(self.runtime.task);self.assertEqual(self.runtime.observe()["execution"]["navigation"]["stage"],"settling")
        self.assertFalse(any(e["event"]=="task_blocked" for e in self.runtime.memory.events))
        self.f["game_frame"]+=4;self.f["player"].update(position={"x":380,"y":290},velocity={"x":0,"y":0})
        await self.runtime.tick();self.assertIsNotNone(self.runtime.task)
        self.assertNotEqual([c for c in self.commands if c["command"]=="action"][-1]["action_id"],0)

    async def test_navigation_blocked_result_exposes_recoverability(self):
        await self.start_planning();await self.runtime.execute(self.runtime.memory.decision_id(),"door-0")
        self.f["player"].update(position={"x":385,"y":300},velocity={"x":8,"y":0})
        for _ in range(11):self.f["game_frame"]+=1;await self.runtime.tick()
        event=next(e for e in self.runtime.memory.events if e["event"]=="task_blocked")
        self.assertEqual(event["failure"]["reason_code"],"momentum_recovery_exhausted")
        self.assertTrue(event["failure"]["recoverable"])

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
        gate=asyncio.Event();started=asyncio.Event()
        async def score(*args):started.set();await gate.wait();return Action(19),source,3,50,49
        self.runtime.score=score
        await self.runtime.tick();await started.wait()
        self.f["room"]["id"]="1:0:16";self.f["game_frame"]+=1
        await self.runtime.tick();gate.set();await asyncio.sleep(0.01)
        self.assertFalse(any(c["command"]=="action" for c in self.commands))
        self.assertGreater(self.runtime.combat_stats["discarded_run_room_changed"],0)
        if self.runtime.prediction:self.runtime.prediction.cancel();await asyncio.gather(self.runtime.prediction,return_exceptions=True)

    async def test_inference_continues_while_previous_ack_is_pending(self):
        await self.start_planning();self.f["entities"]=[sample_frame()["entities"][0]]
        base=self.runtime.control;ack_gate=asyncio.Event();sent=asyncio.Event();second_score=asyncio.Event();calls=0
        async def slow_ack(payload):
            if payload["command"]=="action":sent.set();await ack_gate.wait()
            return await base(payload)
        async def score(source,history,generation):
            nonlocal calls
            calls+=1
            if calls==2:second_score.set();await asyncio.Event().wait()
            await asyncio.sleep(0.01)
            return Action(19),source,generation,10,9
        self.runtime.control=slow_ack;self.runtime.score=score
        self.f["game_frame"]+=1;await self.runtime.tick();await asyncio.wait_for(sent.wait(),1)
        self.f["game_frame"]+=1;await self.runtime.tick()
        await asyncio.wait_for(second_score.wait(),1)
        self.assertFalse(ack_gate.is_set());self.assertEqual(self.runtime.combat_stats["sent"],1)
        ack_gate.set();await asyncio.sleep(0.01)
        command=next(c for c in self.commands if c["command"]=="action")
        self.assertEqual(command["hold_frames"],6);self.assertEqual(command["execution_timing"],"start-relative-v1")

    async def test_expired_predictions_are_logged_and_never_refreshed(self):
        await self.start_planning();self.f["entities"]=[sample_frame()["entities"][0]]
        gate=asyncio.Event();begun=asyncio.Event()
        async def score(source,history,generation):
            begun.set();await gate.wait();await asyncio.sleep(0)
            return Action(19),source,generation,50,49
        self.runtime.score=score;self.f["game_frame"]+=1;await self.runtime.tick();await begun.wait()
        self.f["game_frame"]+=6;await self.runtime.tick();gate.set();await asyncio.sleep(0.01)
        self.assertGreater(self.runtime.combat_stats["discarded_observation_frame_expired"],0)
        logs=[json.loads(l) for l in self.runtime.log_path.read_text().splitlines()]
        self.assertTrue(any(r.get("discard_reason")=="observation_frame_expired" for r in logs))
        # A newly scored observation may send; the expired observation must never do so.
        self.assertFalse(any(c["command"]=="action" and c["observation_frame"]==9 for c in self.commands))

    async def test_realistic_delays_keep_combat_input_covered(self):
        await self.start_planning();self.f["entities"]=[sample_frame()["entities"][0]]
        base=self.runtime.control;applied_until=-1;coverage=[];predictions=[];stop=asyncio.Event()
        async def delayed(payload):
            nonlocal applied_until
            if payload["command"]=="get_frame":await asyncio.sleep(0.01)
            if payload["command"]=="action":
                await asyncio.sleep(0.015) # transport and first game query
                now=self.f["game_frame"]
                if now>=payload["valid_until_frame"]:return {"ok":False,"error":"expired"}
                applied_until=now+payload["hold_frames"]
                await asyncio.sleep(0.045) # ACK latency overlaps the next prediction
            return await base(payload)
        async def score(source,history,generation):
            predictions.append(self.f["game_frame"]);await asyncio.sleep(0.055)
            return Action(19),source,generation,55,52
        async def world():
            for i in range(48):
                self.f["game_frame"]+=1
                if i>=10:coverage.append(self.f["game_frame"]<applied_until)
                await asyncio.sleep(1/30)
            stop.set()
        async def polling():
            while not stop.is_set():await self.runtime.tick();await asyncio.sleep(0.005)
        self.runtime.control=delayed;self.runtime.score=score
        await asyncio.gather(world(),polling())
        self.assertGreater(sum(coverage)/len(coverage),0.9)
        self.assertEqual(self.runtime.combat_stats["rejected"],0)
        gaps=[b-a for a,b in zip(predictions,predictions[1:])]
        # Compare to the advancing game clock: loaded Windows hosts can delay
        # both coroutine wakeups. Coverage and ACK overlap are the invariant.
        self.assertLess(sum(gaps)/len(gaps),3)

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


class PolicyConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_blank_connection_timeout_has_explicit_tunnel_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=GameRuntime(tmp)
            class Client:
                writer=None;closed=False
                async def connect(self):raise asyncio.TimeoutError()
                async def close(self):self.closed=True
            client=Client()
            with patch("isaac_jev.skill_runtime.PolicyClient",return_value=client):
                with self.assertRaisesRegex(RuntimeError,"19528.*Start_Server_Tunnel"):
                    await runtime.connect_policy()
            self.assertTrue(client.closed);self.assertIsNone(runtime.policy);self.assertIsNone(runtime.lease)

    async def test_cached_connection_is_probed_and_cleared_after_network_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=GameRuntime(tmp)
            class Client:
                writer=True;closed=False
                async def request(self,payload):raise ConnectionResetError("network changed")
                async def close(self):self.closed=True;self.writer=None
            client=Client();runtime.policy=client
            with self.assertRaisesRegex(RuntimeError,"network changed"):
                await runtime.connect_policy()
            self.assertTrue(client.closed);self.assertIsNone(runtime.policy)

    async def test_invalid_description_is_never_cached_as_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime=GameRuntime(tmp)
            class Client:
                writer=None;closed=False
                async def connect(self):self.writer=True
                async def request(self,payload):return {"ok":True,"config":{}}
                async def close(self):self.closed=True;self.writer=None
            client=Client()
            with patch("isaac_jev.skill_runtime.PolicyClient",return_value=client):
                with self.assertRaises(ValueError):await runtime.connect_policy()
            self.assertTrue(client.closed);self.assertIsNone(runtime.policy)


if __name__=="__main__":unittest.main()
