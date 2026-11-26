import copy
import json
import unittest
from unittest.mock import patch
from isaac_jev.features import policy_observation, contract_for
from isaac_jev.features_v005 import VERSION, compact_facts, compact_prompt
from isaac_jev.hazards import area_hazards
from isaac_jev.rooms import combat_active, combat_state
from isaac_jev.terrain import combat_terrain
from isaac_jev.weapons import weapon_state
from test_skill_runtime import frame

def observe(f):return policy_observation(f,include_active=False,include_controls=False,feature_profile=VERSION)

class V005Tests(unittest.TestCase):
    def test_compact_text_preserves_unknowns_geometry_and_record_fields(self):
        facts={'history':[{'age_frames':3,'available':True,'weapon_charge_ratio':None},
                          {'age_frames':9,'available':True,'weapon_charge_ratio':0}],
               'terrain':{'mask_rows_rle':[[['W',2],['.',3],['P',1]]],
                          'local_occupancy_7x7':[[0,1,0,1,0,1,0]],
                          'map_origin_rel':[-121.23456,43.98765]},
               'hazards':{'nearest':[['hostile_laser',12.34567,80.12345,8,True,None,[20.999,10.2345],'observed']]}}
        original=copy.deepcopy(facts);packed=compact_facts(facts)
        self.assertEqual(facts,original)
        self.assertEqual(packed['history']['fields'],['age_frames','available','weapon_charge_ratio'])
        self.assertIsNone(packed['history']['rows'][0][2])
        self.assertEqual(packed['history']['rows'][1][2],0)
        self.assertEqual(packed['terrain']['mask_rows'],['WW...P'])
        self.assertEqual(packed['terrain']['local_occupancy_7x7'],['0101010'])
        for old,new in zip(original['terrain']['map_origin_rel'],packed['terrain']['map_origin_rel']):
            self.assertLessEqual(abs(old-new),0.005)
        self.assertEqual(packed['hazards']['nearest'][0][0],'hostile_laser')
        self.assertEqual(packed,compact_facts(packed))

    def test_cached_prompt_conversion_matches_live_generation(self):
        f=frame();f['player']['weapons']=[{'slot':1,'type':2,'charge':0,'max_charge':6}]
        prompt,numeric,names=observe(f)
        self.assertEqual(prompt,compact_prompt(prompt))
        with patch('isaac_jev.features_v005.compact_facts',side_effect=lambda facts:facts):
            raw_prompt,raw_numeric,raw_names=observe(f)
        self.assertEqual(prompt,compact_prompt(raw_prompt))
        self.assertEqual((numeric,names),(raw_numeric,raw_names))
        self.assertEqual(contract_for({'feature_version':VERSION})['text_serialization'],'column-tables-round2-v1')

    def test_old_enemy_telemetry_without_spawn_threat(self):
        f=frame()
        for spawn in ({}, {'spawn_threat':None}, {'spawn_threat':False}):
            with self.subTest(spawn=spawn):
                f['entities']=[{'kind':'enemy','active_enemy':False,**spawn}]
                self.assertEqual(combat_state(f,extended=True)['hostile_enemy_count'],0)
                self.assertFalse(combat_active(f,extended=True))

    def test_spawning_hostile_enemies_trigger_combat_without_double_counting(self):
        f=frame();f['entities']=[
            {'kind':'enemy','active_enemy':False,'spawn_threat':True},
            {'kind':'enemy','active_enemy':True,'spawn_threat':True},
            {'kind':'enemy','active_enemy':False,'spawn_threat':True,'friendly':True},
            {'kind':'enemy','active_enemy':False,'spawn_threat':True,'dead':True},
        ]
        self.assertEqual(combat_state(f,extended=True)['hostile_enemy_count'],2)
        self.assertTrue(combat_active(f,extended=True))
        self.assertEqual(combat_state(f)['hostile_enemy_count'],1)

    def test_relative_input_is_translation_invariant(self):
        f=frame();f['entities']=[{'kind':'enemy','type':10,'position':{'x':200,'y':180},'velocity':{'x':1,'y':-1},'size':10}]
        first=observe(f);g=copy.deepcopy(f)
        for value in [g['player']['position'],g['room']['top_left'],g['room']['bottom_right'],*[e['position'] for e in g['entities']]]:
            value['x']+=100;value['y']+=200
        for c in g['room']['navigation_cells']:c[1]+=100;c[2]+=200
        other=observe(g);self.assertEqual(first[0],other[0]);self.assertEqual(first[2],other[2])
        for a,b in zip(first[1],other[1]):self.assertAlmostEqual(a,b,places=7)

    def test_rock_and_l_room_void_are_blocked(self):
        f=frame();f['room']['grid']=[{'index':10,'kind':'rock','collision':3,'position':{'x':160,'y':120}}]
        d={x['move']:x for x in combat_terrain(f)['directions']}
        self.assertTrue(d['right']['blocked_within_12px'])
        f['room']['grid']=[]
        f['room']['navigation_cells']=[[i,x,y,0,not (x>=160 and y>=120)] for i,x,y in f['room']['navigation_cells']]
        self.assertTrue({x['move']:x for x in combat_terrain(f)['directions']}['right']['blocked_within_12px'])

    def test_unknown_charge_is_not_observed_zero(self):
        f=frame();f['player']['inventory']['collectibles']=[{'id':118}]
        self.assertIsNone(weapon_state(f)['charge_ratio']);self.assertFalse(weapon_state(f)['charge_known'])
        f['player']['weapons']=[{'slot':1,'type':2,'charge':0,'max_charge':6}]
        self.assertEqual(weapon_state(f)['charge_ratio'],0);self.assertTrue(weapon_state(f)['charge_known'])

    def test_creep_flight_and_owner_classification(self):
        f=frame();f['entities']=[{'kind':'effect','variant':22,'position':{'x':125,'y':120},'size':24,'owner_type':10}]
        self.assertTrue(combat_active(f,extended=True));self.assertFalse(combat_active(f))
        f['player']['can_fly']=True;self.assertFalse(area_hazards(f)[0]['harmful'])
        f['player']['can_fly']=False;f['entities'][0]['owner_type']=1;self.assertFalse(combat_active(f,extended=True))

    def test_new_telemetry_keeps_v004_prompt_compatible(self):
        f=frame();old=policy_observation(f,include_active=False,include_controls=False)
        f['player']['weapons']=[{'slot':1,'type':1,'charge':0,'max_charge':0}]
        f['player']['stats'].update(tear_range=200,luck=0)
        self.assertEqual(old,policy_observation(f,include_active=False,include_controls=False))

    def test_contract_and_numeric_fields_are_stable(self):
        f=frame();text,values,names=observe(f)
        self.assertEqual(len(values),len(names));self.assertEqual(len(names),len(set(names)))
        self.assertIn('terrain_3_3',names);self.assertIn('weapon_charge_known',names)
        c=contract_for({'feature_version':VERSION,'control_context':False,'observation_to_action_frame_lag':4})
        self.assertEqual(c['feature_version'],VERSION);self.assertIn('knowledge_sha256',c)
        state=json.loads(text.split('Current state:\n')[1].split('\nDesired outcome')[0])
        self.assertNotIn('previous_controls',state);self.assertIn('build',state)

if __name__=='__main__':unittest.main()
