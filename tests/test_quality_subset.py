import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.mock import sample_frame
spec=importlib.util.spec_from_file_location('prepare_quality_subset',ROOT/'scripts/prepare_quality_subset.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def frame(n,room,clear=False,hp=6):
    f=sample_frame(n);f['room'].update(id=room,clear=clear);f['player']['health']['red']=hp
    f['_recording_session']='session'
    return f


class CompleteRoomFilterTests(unittest.TestCase):
    def test_complete_contiguous_damage_free_room_is_accepted(self):
        rooms={('mock-run','battle'):module.new_room()}
        module.audit_rooms([frame(0,'previous',True),frame(1,'battle'),frame(2,'battle'),frame(3,'battle',True)],rooms)
        self.assertTrue(module.accepted_room(rooms[('mock-run','battle')])[0])

    def test_recording_file_path_maps_to_source_session_directory(self):
        frames=[frame(0,'previous',True),frame(1,'battle'),frame(2,'battle',True)]
        for f in frames:f['_recording_session']='/recordings/source-session/frames.jsonl.gz'
        rooms={('mock-run','battle'):module.new_room()};module.audit_rooms(frames,rooms)
        self.assertEqual(rooms[('mock-run','battle')]['visits'][0]['session'],'source-session')

    def test_damage_without_room_id_then_healing_still_rejects_room(self):
        rooms={('mock-run','battle'):module.new_room()}
        event={'type':'event','event':'damage','run_id':'mock-run','game_frame':1,'entity_type':1,'amount':1}
        module.audit_rooms([frame(0,'previous',True),frame(1,'battle'),event,frame(2,'battle',hp=5),frame(3,'battle',True)],rooms)
        okay,reasons=module.accepted_room(rooms[('mock-run','battle')])
        self.assertFalse(okay);self.assertIn('damage_events',reasons);self.assertIn('health_losses',reasons)

    def test_mid_room_start_and_frame_gap_are_not_complete_evidence(self):
        for frames in ([frame(1,'battle'),frame(2,'battle',True)],
                       [frame(0,'previous',True),frame(1,'battle'),frame(3,'battle',True)]):
            rooms={('mock-run','battle'):module.new_room()};module.audit_rooms(frames,rooms)
            self.assertFalse(module.accepted_room(rooms[('mock-run','battle')])[0])

    def test_damage_on_later_visit_rejects_physical_room(self):
        rooms={('mock-run','battle'):module.new_room()}
        module.audit_rooms([frame(0,'previous',True),frame(1,'battle'),frame(2,'battle',True),
                            frame(3,'other',True),frame(4,'battle',True),frame(5,'battle',True,hp=5)],rooms)
        self.assertFalse(module.accepted_room(rooms[('mock-run','battle')])[0])

    def test_health_conversion_is_unknown_and_not_assumed_damage_free(self):
        changed=frame(2,'battle',True);changed['player']['health']['max_red']=8
        rooms={('mock-run','battle'):module.new_room()}
        module.audit_rooms([frame(0,'previous',True),frame(1,'battle'),changed],rooms)
        self.assertIn('unknown_health_transitions',module.accepted_room(rooms[('mock-run','battle')])[1])


if __name__=='__main__':unittest.main()
