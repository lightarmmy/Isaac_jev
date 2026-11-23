import json
from pathlib import Path
import tempfile
import unittest
from isaac_jev.dataset_filters import find_combat_bomb_rooms,UNSUPPORTED_ACTION_FILTER
from isaac_jev.mock import sample_frame
from isaac_jev.recording import SessionRecorder,iter_messages
from isaac_jev.training import prepare_dataset


class BombFilterTests(unittest.TestCase):
    def test_entire_room_is_removed_before_stride_and_across_visits_and_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=SessionRecorder(root/"records")
            for room in ("bomb-room","safe-room","bomb-room"):
                for n in range(6):
                    frame=sample_frame(n);frame["room"]["id"]=room
                    frame["human_input"]["bomb_trigger"]=room=="bomb-room" and n==3
                    first.append(frame)
            first.close()
            resumed=SessionRecorder(root/"records")
            for n in range(6):
                frame=sample_frame(n+20);frame["room"]["id"]="bomb-room";resumed.append(frame)
            resumed.close()
            prepare_dataset(root/"records",root/"dataset",stride=2)
            rows=[json.loads(line) for line in (root/"dataset/samples.jsonl").read_text().splitlines()]
            self.assertTrue(rows)
            self.assertEqual({row["room_id"] for row in rows},{"safe-room"})
            summary=json.loads((root/"dataset/dataset.json").read_text())
            self.assertEqual(summary["unsupported_action_filter"],UNSUPPORTED_ACTION_FILTER)
            self.assertEqual(summary["combat_bomb_pairs_excluded_before_stride"],15)
            self.assertEqual(sum(m.get("type")=="frame" for m in iter_messages(root/"records")),24)

    def test_last_enemy_killed_before_snapshot_still_excludes_room(self):
        before=sample_frame(0);after=sample_frame(1)
        after["room"]["clear"]=True;after["entities"]=[]
        after["human_input"]["bomb_trigger"]=True
        rooms,triggers=find_combat_bomb_rooms([before,after])
        self.assertEqual(rooms,{("mock-run","mock-room")})
        self.assertEqual(triggers,1)

    def test_planning_bomb_and_enemy_bombs_do_not_exclude_combat(self):
        battle=sample_frame(0);battle["entities"].append({"kind":"bomb"})
        safe=sample_frame(1);safe["room"]["clear"]=True;safe["entities"]=[]
        exploration=sample_frame(2);exploration["room"]["clear"]=True;exploration["entities"]=[{"kind":"bomb"}]
        exploration["human_input"]["bomb_trigger"]=True
        rooms,triggers=find_combat_bomb_rooms([battle,safe,exploration])
        self.assertEqual(rooms,set());self.assertEqual(triggers,0)

    def test_held_input_is_supported_and_other_runs_are_independent(self):
        pressed=sample_frame(0);pressed["human_input"]["bomb_pressed"]=True
        other=sample_frame(1);other["run_id"]="another-run"
        rooms,_=find_combat_bomb_rooms([pressed,other])
        self.assertEqual(rooms,{("mock-run","mock-room")})


if __name__=="__main__":unittest.main()
