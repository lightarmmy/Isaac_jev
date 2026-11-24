import json
from pathlib import Path
import tempfile
import unittest
from isaac_jev.mock import sample_frame
from isaac_jev.recording import SessionRecorder, iter_messages
from isaac_jev.rooms import RoomTimeline, export_room_timeline, TRAINING_PHASE, combat_state
from isaac_jev.training import prepare_dataset
from isaac_jev.outcomes import damage_evidence, event_key, label_outcome


class RoomPhaseTests(unittest.TestCase):
    def test_room_entry_and_clear_timestamps_are_observations_without_duplicate_clear(self):
        timeline = RoomTimeline()
        frame = sample_frame(10);frame["game_time_ms"] = 330
        first = timeline.observe(frame)[0]
        self.assertEqual(first["event"], "room_observed")
        frame = sample_frame(11)
        self.assertEqual(timeline.observe(frame), [])
        cleared = sample_frame(12);cleared["room"]["clear"] = True;cleared["game_time_ms"] = 396
        event = timeline.observe(cleared)[0]
        self.assertEqual(event["event"], "room_cleared")
        self.assertEqual(event["game_frame"], 12)
        self.assertEqual(event["game_time_ms"], 396)
        self.assertTrue(event["consecutive_frames"])
        revisit = sample_frame(13);revisit["room"].update(id="cleared-room",clear=True)
        events = timeline.observe(revisit)
        self.assertEqual([e["event"] for e in events], ["room_entered"])
        self.assertTrue(events[0]["room_clear"])
        again = sample_frame(14);again["room"].update(id="cleared-room",clear=True)
        self.assertEqual(timeline.observe(again), [])

    def test_residual_projectiles_remain_combat_and_button_planning_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);rec = SessionRecorder(root/"records")
            for n in range(6):
                frame = sample_frame(n);frame["room"]["clear"] = n>=3
                if n>=3:frame["entities"]=[]
                if n<5:frame["entities"].append({"kind":"projectile","position":{"x":250,"y":200},"velocity":{"x":-2,"y":0}})
                if n>=5:frame["room"]["grid"]=[{"type":20,"state":0,"position":{"x":400,"y":200}}]
                rec.append(frame)
            rec.close()
            prepare_dataset(rec.path,root/"dataset")
            rows = [json.loads(line) for line in (root/"dataset/samples.jsonl").read_text().splitlines()]
            self.assertEqual([row["action_frame"] for row in rows], [1,2,3,4])
            self.assertEqual(sum(m.get("type")=="frame" for m in iter_messages(rec.path)),6)
            self.assertEqual(json.loads((root/"dataset/dataset.json").read_text())["training_phase"],TRAINING_PHASE)

    def test_post_clear_damage_does_not_label_combat_as_negative(self):
        frames = [sample_frame(n) for n in range(40)]
        for frame in frames[10:]:frame["room"]["clear"] = True;frame["entities"]=[]
        for frame in frames[20:]:frame["player"]["health"]["red"] = 5
        events = {event_key(frames[20]):[{"flags":0,"amount":1}]}
        result = label_outcome(frames,0,damage_evidence(frames,events))
        self.assertEqual(result["label"],"positive")
        self.assertEqual(result["reason"],"no_observed_damage_through_combat_end")

    def test_clear_marker_and_actual_combat_end_have_separate_times(self):
        timeline=RoomTimeline()
        first=sample_frame(0);timeline.observe(first)
        clear=sample_frame(1);clear["room"]["clear"]=True
        clear["entities"]=[{"kind":"projectile","position":{"x":250,"y":200},"velocity":{"x":-2,"y":0}}]
        events=timeline.observe(clear)
        self.assertEqual([e["event"] for e in events],["room_cleared"])
        self.assertEqual(events[0]["phase"],"combat")
        safe=sample_frame(2);safe["room"].update(clear=True);safe["entities"]=[]
        safe["room"]["grid"]=[{"type":20,"state":0,"position":{"x":400,"y":200}}]
        ended=timeline.observe(safe)[0]
        self.assertEqual(ended["event"],"combat_ended")
        self.assertEqual(ended["game_frame"],2)
        self.assertEqual(ended["next_controller"],"planner")

    def test_friendly_or_dead_bullets_and_inactive_npcs_do_not_block_planner(self):
        frame=sample_frame()
        frame["entities"]=[{"kind":"projectile","friendly":True},{"kind":"projectile","dead":True},
                           {"kind":"enemy","active_enemy":False}]
        self.assertEqual(combat_state(frame)["phase"],"planning")

    def test_old_recordings_can_export_timeline_without_reading_an_open_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);closed = SessionRecorder(root/"records")
            first = sample_frame(0);last = sample_frame(1);last["room"]["clear"] = True
            closed.append(first);closed.append(last);closed.close()
            opened = SessionRecorder(root/"records")
            try:
                # The writer opens its gzip asynchronously. This fixture needs an
                # existing open stream, rather than racing file enumeration.
                (opened.path/"frames.jsonl.gz").touch(exist_ok=True)
                report = export_room_timeline(root/"records",root/"timeline.jsonl")
                events = [json.loads(line) for line in (root/"timeline.jsonl").read_text().splitlines()]
                self.assertEqual([event["event"] for event in events],["room_observed","room_cleared"])
                self.assertEqual(report["open_sessions_skipped"],1)
            finally:opened.close()


if __name__ == "__main__":unittest.main()
