import copy
import json
import unittest

from isaac_jev.features import contract,contract_for,policy_observation,extended_profile
from isaac_jev.features_v007 import PREFIX,CANDIDATE_FIELDS,option_texts,semantic_history_summary,observation
from isaac_jev.mock import sample_frame
from isaac_jev.quality_v007 import fit_weights,refine_feedback


class SemanticContractTests(unittest.TestCase):
    def test_both_heads_receive_identical_facts_under_one_fixed_objective(self):
        frame=sample_frame(30)
        a=policy_observation(frame,condition='safe_progress',feature_profile=PREFIX+'s01')
        b=policy_observation(frame,condition='safe_progress',feature_profile=PREFIX+'s02')
        self.assertEqual(a,b)
        self.assertEqual(len(a[1]),930)
        self.assertFalse(any(name.startswith('condition_') for name in a[2]))
        for key in ('s01','s02'):
            cfg=contract(4,False,PREFIX+key)
            self.assertEqual(contract_for(cfg),cfg)
            self.assertTrue(extended_profile(cfg['feature_version']))
        with self.assertRaises(ValueError):observation(frame,condition='positive')

    def test_online_summary_matches_offline_history_and_rejects_future_frames(self):
        past=[sample_frame(i) for i in range(12,30)]
        current=sample_frame(30);future=sample_frame(31)
        past[9]['entities'][0]['hp']=13
        summaries=semantic_history_summary(current,[*past,future])
        self.assertEqual(observation(current,past),observation(current,history_facts=summaries))
        text=observation(current,[*past,future])[0]
        self.assertNotIn(current['run_id'],text)
        self.assertNotIn(current['entities'][0]['uid'],text)
        self.assertNotIn('previous_controls',text)
        self.assertNotIn('Desired outcome condition',text)
        self.assertNotIn('human_input',text)

    def test_outcome_and_control_changes_do_not_change_model_input(self):
        frame=sample_frame(30);changed=copy.deepcopy(frame)
        changed['outcome']={'label':'negative'}
        changed['human_input']={'move':{'x':-1,'y':1},'shoot':{'x':1,'y':0}}
        self.assertEqual(observation(frame),observation(changed))

    def test_all_joint_options_have_distinct_text_and_no_safety_filter(self):
        texts=option_texts()
        self.assertEqual(len(texts),45);self.assertEqual(len(set(texts)),45)
        _,values,names=observation(sample_frame())
        for action in range(45):
            for field in CANDIDATE_FIELDS:self.assertIn(f'option_{action}_{field}',names)
        self.assertEqual(len(values),len(names))

    def test_efficiency_reference_cannot_be_fitted_from_test_outcomes(self):
        def row(split,dps):
            return {'run_id':'train-run' if split=='train' else 'test-run','split':split,'episode_id':'episode',
                    'action_frame':10,'action_label':1,'quality':{'observed_dps':dps,'efficiency_group':[1,3.5,1],
                    'grades':[-1]*7,'confidence':[0.]*7,'move_weight':1.,'shoot_weight':1.}}
        rows=[row('train',float(i)) for i in range(30)]+[row('test',1000000.)]
        reference=fit_weights(rows)
        self.assertEqual(reference['dps_reference']['1/3.5/1']['quantiles'][-1],29.)
        self.assertEqual(rows[-1]['quality']['grades'][5],4)

    def test_unknown_charge_wait_is_not_low_efficiency_and_refinement_is_idempotent(self):
        def row(trigger):
            return {'prompt':'rules\nCurrent semantic state:\n'+json.dumps({'weapon':{'trigger':trigger,'holding':None}}),
                    'quality':{'grades':[4,-1,-1,1,1,0,4],'confidence':[1,0,0,.35,.35,.35,.5],
                               'move_weight':1.,'shoot_weight':.45}}
        charging,continuous=row('charge_release'),row('continuous')
        counts=refine_feedback([charging,continuous])
        self.assertEqual(counts,{'charge_wait_protected':1,'idle_move_downweighted':1})
        self.assertEqual(charging['quality']['grades'][4:6],[-1,-1])
        self.assertEqual(charging['quality']['move_weight'],1.)
        self.assertEqual(continuous['quality']['move_weight'],.5)
        snapshot=copy.deepcopy([charging,continuous]);refine_feedback([charging,continuous])
        self.assertEqual([charging,continuous],snapshot)


try:
    import torch
except ImportError:
    torch=None


@unittest.skipIf(torch is None,'Torch is installed on the training server')
class TextOptionHeadTests(unittest.TestCase):
    def test_shared_scoring_is_equivariant_to_candidate_order_and_supports_subsets(self):
        from isaac_jev.model import TextOptionCombatHead,decode_actions
        model=TextOptionCombatHead(16,540,candidate_indices=[[12*a+j for j in range(12)] for a in range(45)]).eval()
        model.option_embeddings.normal_()
        e=torch.randn(2,16);n=torch.randn(2,540)
        scores=model.score_candidates(e,n);permutation=torch.randperm(45)
        permuted=model.score_candidates(e,n,permutation)
        self.assertTrue(torch.allclose(permuted,scores[:,permutation],atol=1e-6))
        self.assertTrue(torch.allclose(model.score_candidates(e,n,[3,7]),scores[:,[3,7]],atol=1e-6))
        first,second,_=model(e,n);m,s=decode_actions(model,first,second)
        self.assertTrue(torch.equal(m*5+s,scores.argmax(-1)))

    def test_quality_auxiliary_updates_shared_policy_parameters(self):
        from isaac_jev.model import SemanticQualityHead
        model=SemanticQualityHead(16,20)
        q=model.quality_logits(torch.randn(2,16),torch.randn(2,20),torch.tensor([1,5]))
        q.sum().backward()
        self.assertEqual(tuple(q.shape),(2,7,5))
        self.assertIsNotNone(model.move_tower['text'][1].weight.grad)


if __name__=='__main__':unittest.main()
