import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from isaac_jev.features import contract, contract_for, policy_observation
from isaac_jev.mock import sample_frame
from isaac_jev.recording import SessionRecorder
from isaac_jev.training import prepare_dataset, imitation_baselines
from isaac_jev.remote import validate_description
from isaac_jev.outcomes import label_outcome


class StateHistoryTests(unittest.TestCase):
    def test_control_labels_do_not_affect_v4_input_but_physical_history_remains(self):
        frame=sample_frame(20);frame["mode"]="ai";history=[sample_frame(i) for i in range(2,20)]
        for old in history:old["mode"]="ai"
        first=policy_observation(frame,history,include_active=False,include_controls=False)
        frame["execution"]={"action_id":44,"use_active":True}
        for old in history:old["execution"]={"action_id":44,"use_active":True}
        second=policy_observation(frame,history,include_active=False,include_controls=False)
        self.assertEqual(first,second)
        facts=json.loads(first[0].split("Current state:\n")[1].split("\nDesired outcome")[0])
        self.assertNotIn("previous_controls",facts)
        self.assertTrue(facts["history"][0]["available"])
        self.assertIn("player_dx",facts["history"][0]);self.assertIn("vx",facts["history"][0])
        self.assertNotIn("move",facts["history"][0]);self.assertNotIn("shoot",facts["history"][0])
        self.assertTrue(all(v==0 for n,v in zip(first[2],first[1]) if n.startswith("previous_")))

    def test_delayed_action_labels_and_original_baseline_are_metadata_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rec=SessionRecorder(root/"records")
            for i in range(45):
                f=sample_frame(i);f["human_input"]["move"]={"x":0,"y":0} if i<4 else {"x":-1,"y":0}
                f["human_input"]["active_trigger"]=False;rec.append(f)
            rec.close()
            prepare_dataset(rec.path,root/"dataset",exclude_active_items=True,action_lag_frames=4,control_context=False)
            rows=[json.loads(l) for l in (root/"dataset/samples.jsonl").read_text().splitlines()]
            self.assertTrue(all(r["action_frame"]==r["game_frame"]+4 for r in rows))
            first=rows[0];self.assertEqual(first["previous_action_label"]//5,0);self.assertEqual(first["move_label"],3)
            self.assertNotIn("previous_action_label",first["prompt"])
            metrics=imitation_baselines(rows,[0],[0]);self.assertEqual(metrics["repeat_previous_move_accuracy"],0)
            meta=json.loads((root/"dataset/dataset.json").read_text());self.assertFalse(meta["control_context"])
            self.assertEqual(meta["feature_version"],"combat-state-history-no-controls-v4")

    def test_contract_versions_are_checked_for_old_and_new_models(self):
        self.assertEqual(contract_for(contract()),contract())
        cfg={**contract(4,False),"active_head_present":False,"active_context":False,
             "numeric_names":["x"],"numeric_size":1,"hold_frames":3}
        validate_description({"config":cfg})
        with self.assertRaises(ValueError):validate_description({"config":{**cfg,"control_context":True}})
        with self.assertRaises(ValueError):contract(7,False)

    def test_outcome_window_starts_after_aligned_action(self):
        frames=[sample_frame(i) for i in range(45)]
        label=label_outcome(frames,0,{10:"confirmed_player_damage"},30,action_lag_frames=4)
        self.assertEqual(label["damage_after_action_frames"],6)


try:
    import torch
    from safetensors.torch import load_file
except ImportError:
    torch=None


@unittest.skipIf(torch is None,"Optional training dependencies unavailable")
class StateHistoryTrainingTests(unittest.TestCase):
    def test_padded_training_exports_delayed_no_control_contract_and_transition_metrics(self):
        from isaac_jev.training import train
        calls=[]
        class FakeEncoder:
            embedding_size=16;revision="fixture"
            def __init__(self,*args,**kwargs):pass
            def encode(self,prompts,**kwargs):
                calls.append(kwargs)
                return torch.tensor([[len(p)%19/19]*16 for p in prompts])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rec=SessionRecorder(root/"records")
            for run in ("train","validation"):
                for i in range(75):
                    f=sample_frame(i);f["run_id"]=run;f["human_input"]["active_trigger"]=False
                    f["human_input"]["move"]={"x":1 if i<35 else -1,"y":0};rec.append(f)
            rec.close()
            prepare_dataset(rec.path,root/"dataset",exclude_active_items=True,action_lag_frames=4,
                control_context=False,split_overrides={"train":"train","validation":"validation"})
            args=SimpleNamespace(dataset=str(root/"dataset"),output=str(root/"artifact"),epochs=1,
                model="fixture",revision="fixture",device="cpu",max_length=3072,
                feature_pad_multiple=512,move_transition_weight=3,shoot_transition_weight=8)
            with patch("isaac_jev.model.FrozenQwen",FakeEncoder):train(args)
            cfg=json.loads((root/"artifact/policy_config.json").read_text())
            validate_description({"config":cfg})
            self.assertFalse(cfg["control_context"]);self.assertEqual(cfg["observation_to_action_frame_lag"],4)
            self.assertEqual(cfg["transition_loss_weights"],{"move":3,"shoot":8})
            self.assertTrue(calls);self.assertTrue(all(c=={"pad_to_multiple_of":512} for c in calls))
            evaluation=json.loads((root/"artifact/evaluation.json").read_text())
            self.assertGreater(evaluation["validation"]["fixed_positive_all"]["move_changes"]["samples"],0)
            self.assertIn("idle_prediction_fraction",evaluation["validation"]["fixed_positive_all"])


if __name__=="__main__":unittest.main()
