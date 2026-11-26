import json
import math
import tempfile
import unittest
from unittest.mock import patch

from isaac_jev.experiments import EXPERIMENTS, PREFIX, transform_prompt
from isaac_jev.features import contract, contract_for, policy_observation
from isaac_jev.mock import sample_frame
from isaac_jev.quality_labels import annotate_quality, branch_weights, confirmed_enemy_progress


class ExperimentContractTests(unittest.TestCase):
    def test_all_variants_keep_observed_numeric_features_and_runtime_text_matches_conversion(self):
        frame=sample_frame(10)
        original,numeric,names=policy_observation(frame,feature_profile='combat-build-terrain-weapons-v5')
        for key,spec in EXPERIMENTS.items():
            with self.subTest(key=key):
                profile=PREFIX+key
                text,values,fields=policy_observation(frame,feature_profile=profile)
                self.assertEqual(values,numeric);self.assertEqual(fields,names)
                self.assertEqual(text,transform_prompt(original,numeric,names,spec['prompt_scheme']))
                cfg=contract(4,False,profile)
                self.assertEqual(contract_for(cfg),cfg)
                self.assertNotIn(frame['run_id'],text)
                self.assertNotIn('previous_controls',text)
        self.assertLess(len(transform_prompt(original,numeric,names,'focused')),len(original))

    def test_unknown_variants_are_rejected(self):
        with self.assertRaises(ValueError):contract(4,False,PREFIX+'e99')

    def test_experiment_runtime_retains_ground_hazard_combat_handoff(self):
        from isaac_jev.skill_runtime import GameRuntime
        frame=sample_frame(10)
        frame['entities']=[{'kind':'effect','variant':22,'position':frame['player']['position'],'size':24,'owner_type':10}]
        frame['player']['can_fly']=False
        with tempfile.TemporaryDirectory() as root:
            runtime=GameRuntime(root)
            runtime.policy_config={'feature_version':PREFIX+'e07'}
            self.assertTrue(runtime.fighting(frame))
            runtime.policy_config={'feature_version':'combat-state-history-no-controls-v4'}
            self.assertFalse(runtime.fighting(frame))


class QualityTests(unittest.TestCase):
    def test_hp_drop_requires_stable_identity_and_capacity(self):
        a=sample_frame(1);b=sample_frame(2)
        a['entities'][0].update(uid='enemy:1',hp=10,max_hp=10)
        b['entities'][0].update(uid='enemy:1',hp=7,max_hp=10)
        self.assertEqual(confirmed_enemy_progress(a,b)[0],3)
        b['entities'][0]['uid']='other'
        self.assertEqual(confirmed_enemy_progress(a,b)[0],0)
        b['entities'][0].update(uid='enemy:1',max_hp=7)
        self.assertEqual(confirmed_enemy_progress(a,b)[0],0)

    def test_truncated_future_is_not_claimed_complete_or_low_quality(self):
        frames=[sample_frame(i) for i in range(20)]
        result=annotate_quality(frames,0,4,{'label':'positive'},[0]*20,[0]*20,list(range(100,80,-1)),[20]*20)
        self.assertFalse(result['quality_window_complete'])
        self.assertFalse(result['unproductive_still'])

    def test_injury_does_not_discard_room_or_equally_blame_good_firing(self):
        a={'enemy_progress':True,'threat_separation_improved':False,'unproductive_still':False,'quality_window_complete':True}
        move,shoot=branch_weights(a,{'label':'negative','weight':.8,'damage_after_action_frames':5},'dual_quality')
        self.assertGreater(move,0);self.assertGreater(shoot,move)


try:
    import torch
except ImportError:
    torch=None


@unittest.skipIf(torch is None,'Optional Torch dependency missing')
class HeadTests(unittest.TestCase):
    def test_feature_rpc_preserves_sample_order_and_checks_dtype(self):
        import base64
        import numpy as np
        from isaac_jev.rpc_features import PolicyFeatureEncoder
        encoder=PolicyFeatureEncoder.__new__(PolicyFeatureEncoder)
        encoder.revision='revision';encoder.embedding_size=4
        def reply(payload):
            values=np.full(4,1 if payload['texts'][0]=='first' else 2,dtype=np.float16)
            return {'model_revision':'revision','embedding_size':4,'dtype':'float16','features_base64':base64.b64encode(values.tobytes()).decode()}
        encoder.request=reply
        values=encoder.encode(['first','second'])
        self.assertEqual(values[:,0].tolist(),[1,2])
        encoder.request=lambda p:{**reply(p),'dtype':'bfloat16'}
        with self.assertRaises(ValueError):encoder.encode(['first'])

    def test_encoder_rpc_rejects_multiple_texts_and_wrong_revision(self):
        import importlib.util
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/'scripts/policy_rpc.py'
        spec=importlib.util.spec_from_file_location('tested_policy_rpc',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        config={'revision':'r'}
        self.assertEqual(module.feature_texts({'model_revision':'r','texts':['sample']},config),['sample'])
        with self.assertRaises(ValueError):module.feature_texts({'model_revision':'other','texts':['sample']},config)
        with self.assertRaises(ValueError):module.feature_texts({'model_revision':'r','texts':['a','b']},config)

    def test_hierarchical_decoding_uses_total_motion_probability(self):
        from isaac_jev.model import HierarchicalCombatHead,decode_actions
        head=HierarchicalCombatHead(16,8)
        logits=torch.tensor([[math.log(.3)]+[math.log(.7/8)]*8])
        self.assertEqual(logits.argmax(-1).item(),0)
        move,_=decode_actions(head,logits,torch.zeros(1,5))
        self.assertNotEqual(move.item(),0)

    def test_both_new_heads_train_and_reload_with_the_same_decoding_contract(self):
        from isaac_jev.model import head_from_config,decode_actions
        for architecture in ('two-tower-move-gate-v1','separate-move-shoot-towers-v1'):
            cfg={'embedding_size':16,'numeric_size':8,'head_architecture':architecture,'active_head_present':False}
            head=head_from_config(cfg)
            move,shoot,_=head(torch.randn(3,16),torch.randn(3,8))
            loss=torch.nn.functional.cross_entropy(move,torch.tensor([0,2,7]))+torch.nn.functional.cross_entropy(shoot,torch.tensor([1,0,4]))
            loss.backward();self.assertTrue(torch.isfinite(loss))
            restored=head_from_config(cfg);restored.load_state_dict(head.state_dict())
            self.assertEqual(tuple(decode_actions(restored,move,shoot)[0].shape),(3,))
