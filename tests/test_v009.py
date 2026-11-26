import asyncio
import copy
import json
import math
import random
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.features import contract,contract_for,policy_observation
from isaac_jev.features_v009 import PREFIX,history_summary,feedback_summary,attack_routes,observation
from isaac_jev.mock import sample_frame
from isaac_jev.bridge import BridgeServer
from isaac_jev.actions import Action


class FeedbackFactsTests(unittest.TestCase):
    def test_spatial_index_preserves_collision_results(self):
        from isaac_jev.features_v009 import box_index,nearby_boxes
        rng=random.Random(3)
        boxes=tuple(((rng.uniform(0,600),rng.uniform(0,600)),(20.,20.),'rock','box') for _ in range(40))
        obstacles,bins=box_index(boxes)
        for _ in range(80):
            a=(rng.uniform(0,600),rng.uniform(0,600));b=(rng.uniform(0,600),rng.uniform(0,600))
            self.assertEqual(any(o.blocks(a,b) for o in obstacles),any(o.blocks(a,b) for o in nearby_boxes(a,b,obstacles,bins)))

    def test_new_contract_and_old_numeric_prefix_are_consistent(self):
        frame=sample_frame(60);past=[sample_frame(i) for i in range(60)]
        old=policy_observation(frame,past,condition='safe_progress',feature_profile='combat-semantic-v7-s01')
        new=policy_observation(frame,past,condition='safe_progress',feature_profile=PREFIX+'s01')
        self.assertEqual(new[1][:930],old[1]);self.assertEqual(new[2][:930],old[2])
        cfg=contract(4,False,PREFIX+'s01');self.assertEqual(contract_for(cfg),cfg)
        self.assertEqual(len(new[1]),965)

    def test_future_cross_room_and_current_human_labels_do_not_change_feedback(self):
        frame=sample_frame(60);past=[sample_frame(i) for i in range(60)]
        altered=copy.deepcopy(frame);altered['human_input']={'move':{'x':-1,'y':-1},'shoot':{'x':1,'y':0}}
        foreign=sample_frame(59);foreign['room']['id']='another-room'
        self.assertEqual(observation(frame,past),observation(altered,[*past,sample_frame(61),foreign]))

    def test_manual_input_is_not_fabricated_as_applied_ai_input(self):
        frame=sample_frame(30);past=[sample_frame(i) for i in range(30)]
        summary=feedback_summary(frame,past)
        self.assertEqual(summary['manual_shoot_observed_fraction'],1)
        self.assertIsNone(summary['ai_shoot_applied_fraction'])
        self.assertEqual(summary['ai_shoot_applied_frames'],0)

    def test_ai_callback_frames_are_deduplicated_and_future_controls_ignored(self):
        frames=[sample_frame(i) for i in range(30,61)]
        for f in frames:
            f['mode']='ai';f['applied_controls']={'game_frame':f['game_frame']-1,'run_id':f['run_id'],'room_id':f['room']['id'],
                'requests':[{'queried':{'6':True},'pressed':{'6':True}}]}
        frames[-1]['applied_controls']['game_frame']=70
        result=feedback_summary(frames[-1],frames[:-1])
        self.assertEqual(result['ai_shoot_applied_frames'],29)
        self.assertEqual(result['ai_shoot_applied_fraction'],1)

    def test_healed_damage_is_retained_but_health_conversion_is_unknown(self):
        frames=[sample_frame(i) for i in range(31)]
        frames[12]['player']['health']['red']=5
        result=feedback_summary(frames[-1],frames[:-1]);self.assertEqual(result['observed_hp_loss'],1)
        frames[12]['player']['health']['max_red']=8
        self.assertIsNone(feedback_summary(frames[-1],frames[:-1])['observed_hp_loss'])

    def test_hp_identity_changes_and_history_gaps_reset_progress_evidence(self):
        frames=[sample_frame(i) for i in range(61)]
        frames[-1]['entities'][0]['max_hp']=20
        result=feedback_summary(frames[-1],frames[:-1])
        self.assertIsNone(result['comparable_enemy_hp_change']);self.assertEqual(result['no_net_hp_progress_frames'],0)
        result=feedback_summary(frames[-1],frames[:20]+frames[22:-1])
        self.assertEqual(result['available_past_frames'],38)

    def test_clear_line_route_goes_around_rock_and_first_leg_is_nonzero(self):
        frame=sample_frame(0);room=frame['room'];room.update(top_left={'x':0,'y':0},bottom_right={'x':360,'y':360},grid_width=9)
        room['navigation_cells']=[[r*9+c,20+40*c,20+40*r,0,True] for r in range(9) for c in range(9)]
        room['grid']=[{'index':30,'position':{'x':140,'y':140},'collision':2,'kind':'rock'}]
        frame['player']['position']={'x':60,'y':140};frame['player']['stats']['tear_range']=260
        frame['entities'][0]['position']={'x':260,'y':140}
        routes,costs=attack_routes(frame,[('enemy-1','entity_0')])
        self.assertTrue(routes);self.assertTrue(any(v is not None for v in costs))
        self.assertNotEqual(routes[0][4:6],[0,0])
        self.assertNotEqual(routes[0][5],0,'The first route leg should leave the blocked horizontal line')
        self.assertGreater(routes[0][3],math.hypot(routes[0][1],routes[0][2]))


class FeedbackRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoring_exposes_the_exact_feedback_sent_to_policy(self):
        from isaac_jev.skill_runtime import GameRuntime
        frame=sample_frame(30);past=[sample_frame(i) for i in range(30)];summary=history_summary(frame,past)
        prompt,numeric,names=observation(frame,history_facts=summary)
        cfg={**contract(4,False,PREFIX+'s01'),'numeric_names':names,'numeric_size':len(names),'hold_frames':3}
        seen=[]
        class Policy:
            async def request(self,payload):
                seen.append(payload)
                return {'ok':True,'action_id':4,'move_id':0,'shoot_id':4,'use_active':False,'model_ms':1,'observation':payload['observation']}
        with tempfile.TemporaryDirectory() as folder:
            runtime=GameRuntime(folder);runtime.policy_config=cfg;runtime.policy=Policy()
            await runtime.score(frame,summary,0)
            self.assertEqual(seen[0]['prompt'],prompt)
            self.assertEqual(runtime.last_combat_feedback['feedback'],json.loads(prompt.split('\nCurrent feedback state:\n',1)[1])['feedback'])

    async def test_bridge_provides_same_feedback_as_offline_generator(self):
        bridge=BridgeServer(recording_enabled=False);frames=[sample_frame(i) for i in range(61)]
        bridge.latest=frames[-1];bridge._recent_frames.extend(frames)
        reply=await bridge.control({'command':'get_frame','include_history':True,'include_feedback_history':True})
        self.assertEqual(reply['feedback_history'],history_summary(frames[-1],frames[:-1]))
        self.assertIn('policy_history',reply)

    async def test_combat_loop_forwards_feedback_history_to_scoring(self):
        from isaac_jev.skill_runtime import GameRuntime
        with tempfile.TemporaryDirectory() as folder:
            runtime=GameRuntime(folder);frame=sample_frame(30);frame['mode']='ai'
            expected={'legacy':[],'feedback':{}}
            runtime.enabled=True;runtime.phase='combat';runtime.policy_config={'feature_version':PREFIX+'s01'}
            runtime.policy=types.SimpleNamespace(writer=True);runtime.memory.frame=frame
            runtime.memory.reply={'feedback_history':expected,'status':{'last_frame_age_ms':0}}
            runtime.frame_received_at=time.monotonic();seen=[]
            async def score(frame,history,generation):
                seen.append(history);runtime.enabled=False
                return Action(0,False,6),frame,generation,0,0
            runtime.score=score;runtime.discard_reason=lambda *args:'session_changed'
            await runtime.combat_loop(runtime.generation)
            self.assertEqual(seen,[expected])


class NumericTransferTests(unittest.TestCase):
    def test_extended_numeric_head_preserves_original_actor_before_learning(self):
        import torch
        from isaac_jev.model import SemanticQualityHead,load_extended_numeric_state
        torch.manual_seed(1);old=SemanticQualityHead(16,10,16).eval();new=SemanticQualityHead(16,13,16).eval()
        load_extended_numeric_state(new,old.state_dict(),10)
        embedding=torch.randn(4,16);numeric=torch.randn(4,10)
        a=old(embedding,numeric);b=new(embedding,torch.cat((numeric,torch.randn(4,3)),1))
        self.assertTrue(torch.allclose(a[0],b[0]));self.assertTrue(torch.allclose(a[1],b[1]))


class FeedbackSupervisionTests(unittest.TestCase):
    def weights(self,row,feedback):
        import importlib.util
        spec=importlib.util.spec_from_file_location('feedback_dataset',ROOT/'scripts/prepare_feedback_dataset.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module.feedback_training_weights(row,feedback)

    def row(self):
        return {'split':'train','action_label':4,'quality':{'grades':[4,-1,-1,3,3,3,4],
                'move_weight':1.,'shoot_weight':1.,'displacement_px':0,'observed_charge_or_wait':False}}

    def test_effective_stationary_attack_does_not_require_movement(self):
        r=self.row();q=self.weights(r,{'no_net_hp_progress_frames':30})
        self.assertEqual(q['move_weight'],1);self.assertGreater(q['shoot_weight'],1)

    def test_mechanism_wait_is_protected(self):
        r=self.row();r['quality']['grades'][4]=1;r['quality']['observed_charge_or_wait']=True
        q=self.weights(r,{'no_net_hp_progress_frames':60})
        self.assertEqual(q['move_weight'],1);self.assertEqual(q['shoot_weight'],1)

    def test_heldout_quality_weights_are_not_redefined(self):
        r=self.row();r['split']='validation'
        self.assertEqual(self.weights(r,{'no_net_hp_progress_frames':60}),r['quality'])


if __name__=='__main__':unittest.main()
