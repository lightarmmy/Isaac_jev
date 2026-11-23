import copy
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from isaac_jev.features import policy_observation, history_summary, contract
from isaac_jev.mock import sample_frame
from isaac_jev.outcomes import label_outcome, damage_evidence, event_key, segment_frames
from isaac_jev.recording import SessionRecorder
from isaac_jev.training import prepare_dataset


def facts(prompt):
    return json.loads(prompt.split("\nCurrent state:\n", 1)[1].split("\nDesired outcome condition:", 1)[0])


class ConditionedPolicyTests(unittest.TestCase):
    def test_nearest_projectiles_are_explicit_and_bounded_without_identifiers(self):
        frame = sample_frame()
        for i in range(12):
            frame["entities"].append({"uid": "secret-seed-"+str(i), "kind": "projectile",
                "position": {"x": 205+i*5, "y": 190}, "velocity": {"x": -2, "y": 1},
                "size": 3, "height": -10, "falling_speed": 0.5})
        prompt, numeric, names = policy_observation(frame)
        state = facts(prompt)
        self.assertEqual(len(state["projectiles"]), 8)
        self.assertEqual(state["projectiles_omitted"], 4)
        self.assertEqual(state["projectiles"][0]["dx"], 5)
        self.assertEqual(state["projectiles"][0]["vx"], -2)
        self.assertNotIn("secret-seed", prompt)
        self.assertEqual(len(numeric), len(names))

    def test_history_is_past_only_and_resets_at_gaps_modes_rooms(self):
        frame = sample_frame(20)
        past = [sample_frame(i) for i in range(2, 20)]
        full = history_summary(frame, past+[sample_frame(21)])
        self.assertTrue(all(h["available"] for h in full))
        self.assertEqual(full[0]["player_dx"], -3)
        missing = history_summary(frame, [p for p in past if p["game_frame"] != 15])
        self.assertTrue(missing[0]["available"])
        self.assertFalse(missing[1]["available"])
        self.assertFalse(missing[2]["available"])
        for p in past:p["mode"] = "ai"
        self.assertTrue(all(not h["available"] for h in history_summary(frame, past)))

    def test_damage_requires_actual_loss_and_ambiguous_callbacks_are_unknown(self):
        frames = [sample_frame(i) for i in range(50)]
        event = {"flags": 0, "amount": 1}
        callbacks = {event_key(frames[10]): [event]}
        evidence = damage_evidence(frames, callbacks)
        self.assertEqual(label_outcome(frames, 0, evidence)["label"], "unknown")
        for f in frames[10:]:f["player"]["health"]["red"] = 5
        evidence = damage_evidence(frames, callbacks)
        label = label_outcome(frames, 0, evidence)
        self.assertEqual(label["label"], "negative")
        self.assertEqual(label["damage_after_action_frames"], 9)
        self.assertFalse(label["causal_claim"])
        event["flags"] = 1
        self.assertEqual(label_outcome(frames, 0, damage_evidence(frames, callbacks))["label"], "unknown")

    def test_positive_requires_complete_window_and_threat(self):
        frames = [sample_frame(i) for i in range(40)]
        self.assertEqual(label_outcome(frames, 0, {})["label"], "positive")
        self.assertEqual(label_outcome(frames, 15, {})["label"], "unknown")
        frames[0]["entities"] = []
        self.assertEqual(label_outcome(frames, 0, {})["label"], "unknown")
        with self.assertRaises(ValueError):label_outcome(frames, 0, {}, 151)

    def test_session_boundary_prevents_future_damage_and_history_stitching(self):
        frames = [sample_frame(i) for i in range(10)]
        for f in frames:f["_recording_session"] = "a" if f["game_frame"] < 5 else "b"
        segments = list(segment_frames(frames))
        self.assertEqual([len(s) for s in segments], [5, 5])
        self.assertEqual(label_outcome(segments[0], 0, {})["label"], "unknown")

    def test_future_changes_only_training_condition_not_deployment_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = []
            for damaged in (False, True):
                rec = SessionRecorder(root/str(damaged))
                for i in range(40):
                    f = sample_frame(i)
                    if damaged and i >= 10:f["player"]["health"]["red"] = 5
                    if damaged and i == 10:rec.append({"type": "event", "event": "damage", "entity_type": 1,
                        "run_id": f["run_id"], "game_frame": i, "amount": 1, "flags": 0})
                    rec.append(f)
                rec.close()
                destination = root/("dataset-"+str(damaged))
                prepare_dataset(rec.path, destination)
                outputs.append(json.loads((destination/"samples.jsonl").read_text().splitlines()[0]))
            safe, hurt = outputs
            self.assertEqual(safe["outcome"]["label"], "positive")
            self.assertEqual(hurt["outcome"]["label"], "negative")
            self.assertEqual(safe["deployment_prompt"], hurt["deployment_prompt"])
            self.assertEqual(safe["deployment_numeric"], hurt["deployment_numeric"])
            self.assertEqual(facts(safe["prompt"]), facts(hurt["prompt"]))
            self.assertNotIn("damage_after_action_frames", hurt["prompt"])


try:
    import torch
    import numpy as np
    from safetensors.torch import load_file
except ImportError:
    torch = None


@unittest.skipIf(torch is None, "Optional train dependencies not installed")
class TrainingPipelineTests(unittest.TestCase):
    def test_factorized_training_exports_positive_deployment_contract(self):
        from isaac_jev.model import CombatHead
        from isaac_jev.training import train
        class FakeFrozenQwen:
            embedding_size = 16
            revision = "fixture-revision"
            def __init__(self, *args, **kwargs):pass
            def encode(self, prompts):
                return torch.tensor([[len(p)%17/17]*16 for p in prompts])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); rec = SessionRecorder(root/"records")
            for run in ("train-run", "val-run"):
                for i in range(75):
                    f = sample_frame(i); f["run_id"] = run
                    f["human_input"]["active_trigger"]=False
                    if i >= 50:f["player"]["health"]["red"] = 5
                    if i == 50:rec.append({"type": "event", "event": "damage", "entity_type": 1,
                        "run_id": run, "game_frame": i, "amount": 1, "flags": 0})
                    rec.append(f)
            rec.close()
            prepare_dataset(rec.path, root/"dataset", split_overrides={"train-run": "train", "val-run": "validation"},exclude_active_items=True)
            args = SimpleNamespace(dataset=str(root/"dataset"), output=str(root/"artifact"), model="fixture",
                revision="fixture-revision", device="cpu", max_length=3072, epochs=1)
            with patch("isaac_jev.model.FrozenQwen", FakeFrozenQwen):train(args)
            cfg = json.loads((root/"artifact/policy_config.json").read_text())
            self.assertEqual(cfg["deployment_condition"], "positive")
            self.assertFalse(cfg["active_head_present"])
            self.assertFalse(cfg["active_context"])
            self.assertEqual(cfg["head_architecture"], contract()["head_architecture"])
            norm = json.loads((root/"artifact/normalization.json").read_text())
            self.assertTrue(all(v == 1 for v in norm["std"]))
            head = CombatHead(16, cfg["numeric_size"],active_enabled=cfg["active_head_present"])
            head.load_state_dict(load_file(str(root/"artifact/head.safetensors")))
            with torch.no_grad():
                move, shoot, active = head(torch.zeros(2, 16), torch.zeros(2, cfg["numeric_size"]))
            self.assertEqual(tuple(move.shape), (2, 9)); self.assertEqual(tuple(shoot.shape), (2, 5))
            self.assertEqual(tuple(active.shape), (2,))
            self.assertTrue(torch.isneginf(active).all())
            self.assertFalse(any(name.startswith("active.") for name in head.state_dict()))
            metrics = json.loads((root/"artifact/evaluation.json").read_text())
            self.assertGreater(metrics["validation"]["fixed_positive_on_observed_positive"]["samples"], 0)
            self.assertEqual(set(metrics["validation"]["by_observed_outcome"]), {"positive", "negative", "unknown"})
            # Full inference wiring: wait through a paused/manual snapshot,
            # score a clear room in shadow mode, then honor human takeover.
            from isaac_jev.training import infer
            requests=[];frame_requests=0
            async def fake_control(payload,port):
                nonlocal frame_requests
                requests.append(payload)
                if payload["command"]!="get_frame":return {"ok":True}
                frame_requests+=1
                f=sample_frame(frame_requests);f["room"]["clear"]=True
                f["mode"]="shadow" if frame_requests==2 else "manual"
                return {"frame":f,"status":{"connected":True,"last_frame_age_ms":0},"policy_history":history_summary(f,[])}
            infer_args=SimpleNamespace(checkpoint=str(root/"artifact"),device="cpu",mode="shadow",port=0,max_seconds=1)
            with patch("isaac_jev.model.FrozenQwen",FakeFrozenQwen),patch("isaac_jev.training.request_control",fake_control):
                asyncio.run(infer(infer_args))
            self.assertEqual(sum(r["command"]=="action" for r in requests),1)
            self.assertEqual(requests[-1],{"command":"set_mode","mode":"manual"})
            self.assertFalse(any(r.get("mode")=="ai" for r in requests))


if __name__ == "__main__":unittest.main()
