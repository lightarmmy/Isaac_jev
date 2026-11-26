import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'src'))
from isaac_jev.actions import MOVES
from isaac_jev.bridge import BridgeServer
from isaac_jev.combat_phases import contact_enabled, contiguous_frames, phase_evidence
from isaac_jev.features import policy_observation, contract, contract_for
from isaac_jev.features_v009 import observation as old_observation
from isaac_jev.features_v010 import PREFIX, observation, history_summary, observed_shoot_direction
from isaac_jev.mock import sample_frame


def route_frame(n):
    f = sample_frame(n)
    f['room'].update(top_left={'x':0,'y':0}, bottom_right={'x':360,'y':360}, grid_width=9,
        navigation_cells=[[r*9+c,20+40*c,20+40*r,0,True] for r in range(9) for c in range(9)])
    f['room']['grid'] = [{'index':30,'position':{'x':140,'y':140},'collision':2,'kind':'rock'}]
    f['player']['position']={'x':60,'y':140}; f['player']['velocity']={'x':0,'y':0}
    f['player']['stats']['tear_range']=260
    f['entities'][0].update(position={'x':260,'y':140}, velocity={'x':0,'y':0}, collision_class=4, animation='Walk', animation_frame=n)
    return f


class TacticalFactsTests(unittest.TestCase):
    def test_collision_enum_and_unknown_do_not_depend_on_vulnerability(self):
        self.assertEqual([contact_enabled({'collision_class':i}) for i in range(5)], [False,True,True,False,True])
        self.assertIsNone(contact_enabled({})); self.assertIsNone(contact_enabled({'collision_class':False}))
        self.assertTrue(contact_enabled({'collision_class':4,'vulnerable':False}))

    def test_all_45_actions_and_original_numeric_prefix_remain_available(self):
        f=route_frame(30); past=[route_frame(i) for i in range(30)]
        old=old_observation(f,past); new=policy_observation(f,past,condition='safe_progress',feature_profile=PREFIX+'s01')
        self.assertEqual(new[1][:965],old[1]);self.assertEqual(new[2][:965],old[2])
        self.assertEqual(len(new[1]),1018)
        cfg=contract(4,False,PREFIX+'s01');self.assertEqual(contract_for(cfg),cfg)

    def test_ai_actual_shooting_beats_manual_zero_and_ack(self):
        f=route_frame(30); f['mode']='ai'
        f['player']['shoot_direction']={'x':0,'y':0}
        f['execution']={'action_id':1};f['applied_controls']={'game_frame':29,'run_id':f['run_id'],'room_id':f['room']['id'],
            'requests':[{'queried':{'5':True},'pressed':{'5':True}}]}
        d=observed_shoot_direction(f,[f]);self.assertEqual(d['direction'],'right');self.assertEqual(d['source'],'ai_callback')
        state=json.loads(observation(f)[0].split('\nCurrent feedback state:\n')[1])
        self.assertNotEqual(state['feedback']['current_attack_geometry'],'direction_unobserved')
        f['applied_controls']['game_frame']=30
        self.assertIsNone(observed_shoot_direction(f,[f])['direction'])

    def test_unqueried_and_conflicting_callbacks_stay_unknown(self):
        f=route_frame(30);f['mode']='ai'
        f['applied_controls']={'game_frame':29,'run_id':f['run_id'],'room_id':f['room']['id'],
            'requests':[{'queried':{'5':True},'pressed':{}}]}
        self.assertIsNone(observed_shoot_direction(f,[f])['direction'])
        f['applied_controls']['requests']=[{'queried':{str(i):True for i in range(4,8)},'pressed':{}}]
        self.assertEqual(observed_shoot_direction(f,[f])['direction'],'none')
        f['applied_controls']['requests'][0]['pressed']={'4':True,'5':True}
        self.assertIsNone(observed_shoot_direction(f,[f])['direction'])

    def test_current_manual_label_future_and_foreign_history_are_excluded(self):
        f=route_frame(30);past=[route_frame(i) for i in range(30)]
        changed=copy.deepcopy(f);changed['human_input']['shoot']={'x':1,'y':0}
        foreign=route_frame(29);foreign['room']['id']='foreign'
        self.assertEqual(observation(f,past),observation(changed,[*past,route_frame(31),foreign]))
        summary=history_summary(f,past);summary['tactical']['identity'][2]=29
        with self.assertRaisesRegex(ValueError,'identity'):observation(f,history_facts=summary)

    def test_airborne_collision_zero_has_landing_warning_without_fabricated_timer(self):
        f=route_frame(30);f['entities'][0].update(animation='JumpDown',animation_frame=12,collision_class=0,vulnerable=False,
            position={'x':60,'y':140},npc_target_position={'x':999,'y':999})
        text,values,names=observation(f)
        state=json.loads(text.split('\nCurrent feedback state:\n')[1]);row=state['npc_phases']['rows'][0]
        self.assertEqual(row[11],'descending_or_landing');self.assertFalse(row[14]);self.assertIsNone(row[16])
        self.assertEqual(state['candidate_consequences']['rows'][0][-2],320)
        self.assertLess(state['candidate_consequences']['rows'][0][-1],0)
        self.assertGreater(state['candidate_consequences']['rows'][4][-1],state['candidate_consequences']['rows'][0][-1])
        self.assertEqual(values[names.index('tactical_landing_eta_known')],0)

    def test_empirical_timer_uses_prior_restore_not_animation_end(self):
        frames=[route_frame(i) for i in range(20)]
        for i,f in enumerate(frames):
            f['entities'][0].update(animation='JumpDown', animation_frame=i if i<10 else i-10,
                                  collision_class=0 if i<6 or i>=10 else 4)
        result=phase_evidence(frames[-1],frames)['enemy-1']
        # Already beyond the previous observed restoration: do not promise a negative timer.
        self.assertIsNone(result['eta_frames'])
        result=phase_evidence(frames[14],frames[:15])['enemy-1']
        self.assertEqual(result['eta_frames'],[2,2]);self.assertEqual(result['timing_samples'],1)
        fresh=copy.deepcopy(frames[14]);fresh['entities'][0]['uid']='replacement'
        self.assertIsNone(phase_evidence(fresh,contiguous_frames(fresh,frames[:14]))['replacement']['eta_frames'])

    def test_history_gap_limits_calibration_and_route_commitment(self):
        frames=[route_frame(i) for i in range(80)]
        result=contiguous_frames(frames[-1],frames[:30]+frames[35:-1])
        self.assertEqual(result[0]['game_frame'],35)
        self.assertEqual(len(contiguous_frames(frames[-1],frames[:-1],20)),21)

    def test_committed_route_retains_attack_position_across_enemy_motion(self):
        frames=[route_frame(i) for i in range(40)]
        frames[31]['entities'][0]['position']['y']=150
        a=history_summary(frames[30],frames[:30])['tactical']['route'][0]
        b=history_summary(frames[31],frames[:31])['tactical']['route'][0]
        self.assertEqual(a['position_delta'],b['position_delta']);self.assertEqual(b['status'],'committed')
        self.assertNotEqual(b['next_waypoint'],[0,0])
        frames[32]['player']['position']['y']=180
        c=history_summary(frames[32],frames[:32])['tactical']['route'][0]
        self.assertGreater(c['progress_px'],0)
        frames[33]['entities'][0]['uid']='replacement'
        d=history_summary(frames[33],frames[:33])['tactical']['route'][0]
        self.assertEqual(d['status'],'new_target');self.assertIsNone(d['progress_px'])

    def test_pursuit_finds_far_target_beyond_the_old_96_cell_search(self):
        from isaac_jev.features_v009 import attack_routes
        f=route_frame(0); f['room'].update(bottom_right={'x':1320,'y':360},grid_width=33,grid=[],
            navigation_cells=[[r*33+c,20+40*c,20+40*r,0,True] for r in range(9) for c in range(33)])
        f['entities'][0]['position']={'x':1220,'y':140}
        old,_=attack_routes(f,[('enemy-1','target')]);self.assertFalse(old)
        new=history_summary(f,[])['tactical']['route'][0]
        self.assertIsNotNone(new['path_px']);self.assertGreater(new['path_px'],700)
        self.assertGreater(new['next_waypoint'][0],0)


class TacticalRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_combat_loop_requests_tactical_history_for_new_checkpoint(self):
        from isaac_jev.skill_runtime import GameRuntime
        from isaac_jev.actions import Action
        with tempfile.TemporaryDirectory() as folder:
            runtime=GameRuntime(folder);f=route_frame(30);f['mode']='ai'
            summary=history_summary(f,[])
            runtime.enabled=True;runtime.phase='combat';runtime.policy_config={'feature_version':PREFIX+'s01'}
            runtime.policy=types.SimpleNamespace(writer=True);runtime.memory.frame=f
            runtime.memory.reply={'tactical_history':summary,'status':{'last_frame_age_ms':0}}
            runtime.frame_received_at=time.monotonic();seen=[]
            async def score(frame,history,generation):
                seen.append(history);runtime.enabled=False
                return Action(0,False,3),frame,generation,0,0
            runtime.score=score;runtime.discard_reason=lambda *args:'session_changed'
            await runtime.combat_loop(runtime.generation)
            self.assertEqual(seen,[summary])

    async def test_bridge_offline_and_scoring_use_the_same_tactical_facts(self):
        from isaac_jev.skill_runtime import GameRuntime
        frames=[route_frame(i) for i in range(31)];bridge=BridgeServer(recording_enabled=False)
        bridge.latest=frames[-1];bridge._recent_frames.extend(frames)
        result=await bridge.control({'command':'get_frame','include_history':True,'include_tactical_history':True})
        expected=history_summary(frames[-1],frames[:-1]);self.assertEqual(result['tactical_history'],expected)
        prompt,numeric,names=observation(frames[-1],history_facts=expected)
        cfg={**contract(4,False,PREFIX+'s01'),'numeric_names':names,'numeric_size':len(names),'hold_frames':3}
        seen=[]
        class Policy:
            async def request(self,payload):
                seen.append(payload)
                return {'ok':True,'action_id':4,'move_id':0,'shoot_id':4,'use_active':False,'model_ms':1,'observation':payload['observation']}
        with tempfile.TemporaryDirectory() as folder:
            runtime=GameRuntime(folder);runtime.policy_config=cfg;runtime.policy=Policy()
            await runtime.score(frames[-1],expected,0)
            self.assertEqual(seen[0]['prompt'],prompt)
            state=json.loads(prompt.split('\nCurrent feedback state:\n')[1])
            self.assertEqual(runtime.last_combat_feedback['pursuit'],state['pursuit'])


class TacticalSupervisionTests(unittest.TestCase):
    def weights(self,row,summary,frames):
        spec=importlib.util.spec_from_file_location('tactical_dataset',ROOT/'scripts/prepare_tactical_dataset.py')
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        return m.phase_training_weights(row,summary,frames)

    def test_phase_contact_is_masked_and_holdout_weights_remain_unchanged(self):
        frames=[route_frame(i) for i in range(13)]
        for f in frames:f['entities'][0].update(position={'x':60,'y':140},collision_class=0,animation='JumpDown',vulnerable=False)
        summary=history_summary(frames[0],[])
        row={'split':'train','action_label':4,'quality':{'grades':[4,-1,3,3,3,3,1], 'confidence':[1,0,.6,.35,.5,.35,.5],
            'move_weight':.3,'shoot_weight':1,'displacement_px':0,'rapid_move_changes':0,'observed_charge_or_wait':False}}
        q=self.weights(row,summary,frames);self.assertEqual(q['grades'][2],-1);self.assertEqual(q['confidence'][2],0)
        self.assertEqual(q['grades'][6],4);self.assertAlmostEqual(q['move_weight'],1)
        row['split']='validation';self.assertEqual(self.weights(row,summary,frames),row['quality'])

    def test_route_progress_with_attack_progress_is_rewarded_without_relabeling(self):
        frames=[route_frame(i) for i in range(13)]
        frames[-1]['player']['position']['y']=180
        row={'split':'train','action_label':14,'quality':{'grades':[4,-1,-1,3,3,3,4],
            'move_weight':1.,'shoot_weight':1.,'displacement_px':40.,'rapid_move_changes':0,'observed_charge_or_wait':False}}
        q=self.weights(row,history_summary(frames[0],[]),frames)
        self.assertIn('safe_route_progress_with_attack_progress',q['tactical_alignment'])
        self.assertGreater(q['move_weight'],1);self.assertEqual(row['action_label'],14)


if __name__=='__main__':unittest.main()
