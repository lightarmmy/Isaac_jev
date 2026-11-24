import asyncio
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from isaac_jev.actions import Action,label_from_input
from isaac_jev.bridge import BridgeServer,request_control
from isaac_jev.features import policy_observation
from isaac_jev.mock import sample_frame
from isaac_jev.protocol import encode,decode,validate_frame
from isaac_jev.recording import SessionRecorder,iter_messages
from isaac_jev.training import prepare_dataset,split_for_run,active_head_eligible,imitation_baselines

class CoreTests(unittest.TestCase):
    def test_movement_shooting_and_active_are_concurrent(self):
        label,active=label_from_input({"move":{"x":1,"y":1},"shoot":{"x":-1,"y":0},"active_trigger":True})
        command=Action(label,active).as_command(sample_frame(10))
        self.assertEqual(command["move"],{"x":1,"y":1})
        self.assertEqual(command["shoot"],{"x":-1,"y":0})
        self.assertTrue(command["use_active"])
        self.assertEqual(command["valid_until_frame"],16)
        self.assertEqual(command["hold_frames"],3)
        with self.assertRaises(ValueError):Action(45)
        with self.assertRaises(ValueError):Action(0,1)

    def test_json_rejects_nonfinite_and_wrong_versions(self):
        for line in [b'{"protocol_version":1,"n":NaN}\n',b'{"protocol_version":2}\n',b'[]\n']:
            with self.assertRaises(ValueError):decode(line)
        frame=sample_frame();validate_frame(frame)
        frame["game_frame"]=True
        with self.assertRaises(ValueError):validate_frame(frame)

    def test_policy_inputs_do_not_contain_run_seed_or_future_metadata(self):
        frame=sample_frame();before=policy_observation(frame)
        frame["run_id"]="secret-seed-run";frame["privileged"]={"hidden_rooms":[12],"next_drop":105}
        frame["entities"][0]["uid"]="another-internal-seed"
        after=policy_observation(frame)
        self.assertEqual(before,after)
        self.assertEqual(len(after[1]),len(after[2]))
        self.assertGreater(len(after[1]),40)

    def test_recorder_drains_valid_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder=SessionRecorder(Path(tmp))
            for i in range(61):self.assertTrue(recorder.append(sample_frame(i)))
            recorder.close()
            self.assertEqual(recorder.written,61);self.assertIsNone(recorder.error)
            self.assertEqual(len(list(iter_messages(recorder.path))),61)
            self.assertEqual(json.loads((recorder.path/"session.json").read_text())["dropped"],0)

    def test_dataset_splits_whole_runs_and_keeps_active_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rec=SessionRecorder(root/"records")
            runids=[]
            for wanted in ["train","validation","test"]:
                runids.append(next(f"run-{i}" for i in range(1000) if split_for_run(f"run-{i}")==wanted))
            for run in runids:
                for n in range(5):
                    frame=sample_frame(n);frame["run_id"]=run
                    frame["human_input"]["active_trigger"]=n==1
                    if n==1:rec.append({"type":"event","event":"item_used","run_id":run,"game_frame":n,"item_id":45,"active_slot":0})
                    rec.append(frame)
            invalid=sample_frame(100);invalid["mode"]="ai";rec.append(invalid)
            rec.close();prepare_dataset(root/"records",root/"dataset",stride=3)
            rows=[json.loads(l) for l in (root/"dataset"/"samples.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows),6)
            self.assertEqual(sum(r["active_label"] for r in rows),3)
            for run in runids:self.assertEqual(len({r["split"] for r in rows if r["run_id"]==run}),1)
            self.assertTrue(all(r["action_frame"]==r["game_frame"]+1 for r in rows))

    def test_active_use_trains_on_charge_before_consumption_and_prior_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rec=SessionRecorder(root/"records")
            before=sample_frame(0)
            before["human_input"]={"move":{"x":-1,"y":0},"shoot":{"x":0,"y":0},"active_trigger":False}
            after=copy.deepcopy(before);after["game_frame"]=1
            after["player"]["position"]["x"]=240
            after["player"]["active"].update(charge=0,ready=False)
            after["human_input"]["move"]["x"]=1;after["human_input"]["active_trigger"]=True
            rec.append(before)
            rec.append({"type":"event","event":"item_used","run_id":before["run_id"],"game_frame":1,"item_id":45,"active_slot":0})
            rec.append(after);rec.close()
            prepare_dataset(root/"records",root/"dataset")
            row=json.loads((root/"dataset/samples.jsonl").read_text())
            values=dict(zip(row["numeric_names"],row["numeric"]))
            self.assertTrue(row["active_label"])
            self.assertEqual(values["active_charge"],4/12)
            self.assertEqual(values["active_ready"],1)
            self.assertEqual(values["previous_move_3"],1)
            self.assertEqual(values["previous_shoot_0"],1)
            self.assertEqual(row["action_label"],20)

    def test_explicit_validation_selection_keeps_all_frames_of_a_run_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rec=SessionRecorder(root/"records")
            runids=[f"run-{i}" for i in range(1000) if split_for_run(f"run-{i}")=="train"][:2]
            for run in runids:
                for n in range(4):
                    frame=sample_frame(n);frame["run_id"]=run;rec.append(frame)
            rec.close()
            prepare_dataset(root/"records",root/"dataset",split_overrides={runids[1]:"validation"})
            rows=[json.loads(l) for l in (root/"dataset/samples.jsonl").read_text().splitlines()]
            self.assertEqual({r["split"] for r in rows if r["run_id"]==runids[0]},{"train"})
            self.assertEqual({r["split"] for r in rows if r["run_id"]==runids[1]},{"validation"})
            with self.assertRaisesRegex(ValueError,"unrecorded"):
                prepare_dataset(root/"records",root/"invalid",split_overrides={"missing-run":"validation"})

    def test_one_active_example_does_not_enable_automatic_item_use(self):
        rows=[{"run_id":"one","active_label":True}]+[{"run_id":"one","active_label":False}]*20
        self.assertFalse(active_head_eligible(rows,list(range(len(rows)))))
        rows += [{"run_id":"two","active_label":True}]*4
        self.assertTrue(active_head_eligible(rows,list(range(len(rows)))))

    def test_imitation_baseline_uses_preceding_controls_and_training_majority(self):
        rows=[{"action_label":20,"numeric_names":["previous_action"],"numeric":[20/44]},
              {"action_label":15,"numeric_names":["previous_action"],"numeric":[15/44]},
              {"action_label":15,"numeric_names":["previous_action"],"numeric":[20/44]}]
        metrics=imitation_baselines(rows,[0],[1,2])
        self.assertEqual(metrics["repeat_previous_action_accuracy"],0.5)
        self.assertEqual(metrics["training_majority_action_accuracy"],0)

class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub=BridgeServer(port=0);await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.close()

    async def test_live_history_matches_dataset_history_and_resets_on_room_change(self):
        reader,writer=await asyncio.open_connection("127.0.0.1",self.hub.port)
        writer.write(encode({"protocol_version":1,"type":"hello","client":"isaac_mod"}));await writer.drain();await reader.readline()
        frames=[sample_frame(i) for i in range(21)]
        for frame in frames:writer.write(encode(frame))
        await writer.drain();await asyncio.sleep(0.05)
        reply=await request_control({"command":"get_frame","include_history":True},port=self.hub.port)
        self.assertEqual(policy_observation(frames[-1],frames[:-1]),
                         policy_observation(reply["frame"],history_facts=reply["policy_history"]))
        changed=sample_frame(21);changed["room"]["id"]="next-room"
        writer.write(encode(changed));await writer.drain();await asyncio.sleep(0.03)
        reply=await request_control({"command":"get_frame","include_history":True},port=self.hub.port)
        self.assertTrue(all(not h["available"] for h in reply["policy_history"]))
        writer.close();await writer.wait_closed()

    async def test_fragmented_packets_and_bad_frames_do_not_break_connection(self):
        reader,writer=await asyncio.open_connection("127.0.0.1",self.hub.port)
        hello=encode({"protocol_version":1,"type":"hello","client":"isaac_mod"})
        writer.write(hello[:7]);await writer.drain();writer.write(hello[7:]);await writer.drain()
        self.assertEqual(decode(await reader.readline())["mode"],"manual")
        writer.write(encode(sample_frame(1))+b'{"protocol_version":1,"type":"frame"}\n'+encode(sample_frame(3)))
        await writer.drain();await asyncio.sleep(0.05)
        response=await request_control({"command":"get_status"},port=self.hub.port)
        self.assertEqual(response["status"]["frames"],2)
        self.assertEqual(response["status"]["invalid_messages"],1)
        self.assertEqual(response["status"]["frame_gaps"],1)
        self.assertTrue(response["status"]["connected"])
        writer.close();await writer.wait_closed()

    async def test_state_age_room_and_expiry_are_enforced(self):
        reader,writer=await asyncio.open_connection("127.0.0.1",self.hub.port)
        writer.write(encode({"protocol_version":1,"type":"hello","client":"isaac_mod"}));await writer.drain();await reader.readline()
        writer.write(encode(sample_frame(10)));await writer.drain();await asyncio.sleep(0.03)
        command=Action(20,False,2).as_command(sample_frame(4))
        # These are validator checks. Keep wall-clock freshness explicit rather
        # than allowing slow socket setup on a loaded host to select another guard.
        self.hub.last_received=time.monotonic()
        result=await self.hub.control(command)
        self.assertFalse(result["ok"]);self.assertIn("expired",result["error"])
        command=Action(20).as_command(sample_frame(10));command["room_id"]="wrong-room"
        self.hub.last_received=time.monotonic()
        result=await self.hub.control(command)
        self.assertFalse(result["ok"]);self.assertIn("mismatch",result["error"])
        self.hub.last_received=time.monotonic()-1
        result=await self.hub.control(Action(20).as_command(sample_frame(10)))
        self.assertFalse(result["ok"]);self.assertIn("no fresh",result["error"])
        writer.close();await writer.wait_closed()

    async def test_record_rotation_drains_gzip_and_excludes_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.hub.record_root=Path(tmp)
            self.hub.recorder=SessionRecorder(Path(tmp),{"purpose":"diagnostic"})
            self.hub.recorder.append(sample_frame(0))
            rotated=await request_control({"command":"rotate_recording","purpose":"demonstration"},port=self.hub.port)
            self.assertTrue(rotated["ok"]);self.assertIsNone(rotated["completed_error"])
            self.hub.recorder.append(sample_frame(1))
            stopped=await request_control({"command":"stop_recording"},port=self.hub.port)
            self.assertTrue(stopped["ok"]);self.assertIsNone(stopped["completed_error"])
            self.assertEqual([m["game_frame"] for m in iter_messages(Path(tmp))],[0,1])
            self.assertEqual([m["game_frame"] for m in iter_messages(Path(tmp),include_diagnostics=False)],[1])

    async def test_start_without_recording_can_enable_capture_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=BridgeServer(port=0,record_root=Path(tmp),recording_enabled=False)
            await hub.start()
            try:
                self.assertIsNone(hub.recorder)
                self.assertEqual(list(Path(tmp).iterdir()),[])
                reply=await request_control({"command":"rotate_recording","purpose":"demonstration"},port=hub.port)
                self.assertTrue(reply["ok"])
                self.assertIsNotNone(hub.recorder)
            finally:await hub.close()

    async def test_recording_survives_game_restart_and_same_seed_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=BridgeServer(port=0,record_root=Path(tmp))
            await hub.start()
            async def connect_mod():
                reader,writer=await asyncio.open_connection("127.0.0.1",hub.port)
                writer.write(encode({"protocol_version":1,"type":"hello","client":"isaac_mod"}))
                await writer.drain()
                self.assertTrue(decode(await reader.readline())["recording"])
                return writer
            try:
                writer=await connect_mod()
                writer.write(encode(sample_frame(10)));await writer.drain();await asyncio.sleep(0.03)
                first=hub.recorder.path
                # A new run can restart the frame counter while reusing a seed.
                writer.write(encode({"protocol_version":1,"type":"event","event":"run_started","run_id":"mock-run","game_frame":0}))
                writer.write(encode(sample_frame(0)));await writer.drain();await asyncio.sleep(0.08)
                second=hub.recorder.path
                self.assertNotEqual(first,second)
                self.assertEqual(hub.invalid,0)
                self.assertTrue(json.loads((first/"session.json").read_text())["closed_at_unix"])
                writer.close();await writer.wait_closed();await asyncio.sleep(0.08)
                self.assertIsNone(hub.recorder)
                self.assertTrue(hub.recording_enabled)
                self.assertTrue(json.loads((second/"session.json").read_text())["closed_at_unix"])
                writer=await connect_mod()
                writer.write(encode(sample_frame(1)));await writer.drain();await asyncio.sleep(0.03)
                third=hub.recorder.path
                self.assertNotEqual(second,third)
                writer.close();await writer.wait_closed();await asyncio.sleep(0.08)
                self.assertEqual(sum(m.get("type")=="frame" for m in iter_messages(Path(tmp))),3)
                self.assertEqual(sum(m.get("event")=="room_observed" for m in iter_messages(Path(tmp))),3)
            finally:await hub.close()

    async def test_explicit_record_stop_is_preserved_across_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub=BridgeServer(port=0,record_root=Path(tmp));await hub.start()
            try:
                await hub.control({"command":"stop_recording"})
                reader,writer=await asyncio.open_connection("127.0.0.1",hub.port)
                writer.write(encode({"protocol_version":1,"type":"hello","client":"isaac_mod"}));await writer.drain()
                self.assertFalse(decode(await reader.readline())["recording"])
                self.assertIsNone(hub.recorder)
                writer.close();await writer.wait_closed()
            finally:await hub.close()

if __name__=="__main__":unittest.main()
