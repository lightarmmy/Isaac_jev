import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from isaac_jev.features import contract, history_summary, policy_observation
from isaac_jev.mock import sample_frame
from isaac_jev.remote import PolicyClient, prediction_action, remote_infer, validate_description


def config():
    _,numeric,names=policy_observation(sample_frame(),include_active=False)
    return {**contract(),"numeric_names":names,"numeric_size":len(numeric),"hold_frames":3,
            "active_head_present":False,"active_context":False,"active_head_trained":False}


class RemoteContractTests(unittest.TestCase):
    def test_rejects_rule_mismatch_and_active_checkpoint(self):
        cfg=config();validate_description({"config":cfg})
        for key,value in (("rules_sha256","wrong"),("active_head_present",True),("numeric_size",1)):
            with self.assertRaises(ValueError):validate_description({"config":{**cfg,key:value}})

    def test_prediction_preserves_source_frame_and_rejects_wrong_identity(self):
        frame=sample_frame(40);cfg=config()
        observation={"run_id":frame["run_id"],"room_id":frame["room"]["id"],"game_frame":40}
        reply={"observation":observation,"move_id":4,"shoot_id":2,"action_id":22,"use_active":False}
        command=prediction_action(reply,frame,cfg).as_command(frame)
        self.assertEqual(command["observation_frame"],40)
        self.assertEqual(command["valid_until_frame"],46)
        for bad in ({**reply,"observation":{**observation,"game_frame":41}},
                    {**reply,"use_active":True},{**reply,"move_id":9}):
            with self.assertRaises(ValueError):prediction_action(bad,frame,cfg)

    def test_server_validates_contract_and_finite_numeric_inputs_without_torch(self):
        path=Path(__file__).resolve().parents[1]/"scripts/policy_rpc.py"
        spec=importlib.util.spec_from_file_location("policy_rpc",path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cfg=config();request={**contract(),"prompt":"test","numeric":[0]*cfg["numeric_size"]}
        module.prediction_fields(request,cfg)
        for bad in ({**request,"rules_sha256":"wrong"},{**request,"numeric":[float("nan")]*cfg["numeric_size"]}):
            with self.assertRaises(ValueError):module.prediction_fields(bad,cfg)


class RemoteClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_closes_stream_so_old_response_cannot_be_reused(self):
        handlers=set()
        async def slow(reader,writer):
            handlers.add(asyncio.current_task())
            try:
                await reader.readline();await asyncio.sleep(0.1)
                writer.write(b'{"ok":true}\n');await writer.drain()
            except ConnectionError:pass
            finally:
                writer.close();await writer.wait_closed()
        server=await asyncio.start_server(slow,"127.0.0.1",0)
        client=PolicyClient(server.sockets[0].getsockname()[1],timeout=2)
        try:
            await client.connect()
            client.timeout=0.02
            with self.assertRaises(asyncio.TimeoutError):await client.request({"command":"predict"})
            self.assertIsNone(client.writer)
            with self.assertRaises(RuntimeError):await client.request({"command":"predict"})
        finally:
            await client.close();server.close();await server.wait_closed()
            await asyncio.gather(*handlers)

    async def run_inference_case(self,elapsed):
        controls=[];calls=[];frame_count=0;cfg=config()
        class FakeClient:
            def __init__(self,*args):pass
            async def connect(self):pass
            async def close(self):pass
            async def request(self,payload):
                calls.append(payload)
                if payload["command"]=="describe":return {"ok":True,"config":cfg,"backend":{}}
                return {"ok":True,"move_id":4,"shoot_id":2,"action_id":22,"use_active":False,
                        "model_ms":50,"observation":payload["observation"]}
        async def control(payload,port):
            nonlocal frame_count
            controls.append(payload)
            if payload["command"]!="get_frame":return {"ok":True}
            frame_count+=1;frame=sample_frame(frame_count)
            frame["mode"]="shadow" if frame_count==2 else "manual"
            return {"frame":frame,"policy_history":history_summary(frame,[]),
                    "status":{"connected":True,"last_frame_age_ms":0}}
        args=SimpleNamespace(port=0,policy_port=0,request_timeout=1,startup_timeout=1,max_seconds=1,mode="shadow")
        with patch("isaac_jev.remote.PolicyClient",FakeClient),patch("isaac_jev.remote.request_control",control),\
                patch("isaac_jev.remote.time.perf_counter",side_effect=[0,elapsed]):
            await remote_infer(args)
        self.assertEqual(controls[-1],{"command":"set_mode","mode":"manual"})
        self.assertTrue(all(p.get("mode")!="ai" for p in controls))
        prediction=[p for p in calls if p["command"]=="predict"][0]
        self.assertIn("Desired outcome condition: positive.",prediction["prompt"])
        return [p for p in controls if p["command"]=="action"]

    async def test_remote_shadow_wires_history_and_original_frame_then_honors_takeover(self):
        actions=await self.run_inference_case(0.06)
        self.assertEqual(len(actions),1);self.assertEqual(actions[0]["observation_frame"],2)
        self.assertFalse(actions[0]["use_active"])

    async def test_slow_prediction_is_discarded(self):
        self.assertEqual(await self.run_inference_case(0.25),[])

    async def test_ai_waits_in_clear_room_then_continues_after_entering_combat_room(self):
        cfg=config();controls=[];predictions=[];frame_count=0
        class FakeClient:
            def __init__(self,*args):pass
            async def connect(self):pass
            async def close(self):pass
            async def request(self,payload):
                if payload["command"]=="describe":return {"ok":True,"config":cfg,"backend":{}}
                predictions.append(payload)
                return {"ok":True,"move_id":4,"shoot_id":2,"action_id":22,"use_active":False,
                        "model_ms":50,"observation":payload["observation"]}
        async def control(payload,port):
            nonlocal frame_count
            controls.append(payload)
            if payload["command"]!="get_frame":return {"ok":True}
            frame_count+=1;frame=sample_frame(frame_count)
            frame["mode"]="ai" if frame_count in (2,3) else "manual"
            frame["mode_reason"]="f8" if frame_count==4 else "requested_ai"
            if frame_count<3:frame["entities"]=[];frame["room"]["clear"]=True
            else:frame["room"]["id"]="next-combat-room"
            return {"frame":frame,"policy_history":history_summary(frame,[]),
                    "status":{"connected":True,"last_frame_age_ms":0}}
        args=SimpleNamespace(port=0,policy_port=0,request_timeout=1,startup_timeout=1,max_seconds=1,mode="ai")
        with patch("isaac_jev.remote.PolicyClient",FakeClient),patch("isaac_jev.remote.request_control",control):
            await remote_infer(args)
        self.assertEqual(len(predictions),1)
        action=[p for p in controls if p["command"]=="action"][0]
        self.assertEqual(action["room_id"],"next-combat-room")
        self.assertEqual(action["observation_frame"],3)
        self.assertEqual(controls[-1],{"command":"set_mode","mode":"manual"})


if __name__=="__main__":unittest.main()
