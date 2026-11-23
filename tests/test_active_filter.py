import json
from pathlib import Path
import tempfile
import unittest
from isaac_jev.dataset_filters import find_active_item_rooms
from isaac_jev.mock import sample_frame
from isaac_jev.recording import SessionRecorder,iter_messages
from isaac_jev.training import prepare_dataset


class ActiveFilterTests(unittest.TestCase):
    def test_real_use_filters_teleport_source_and_destination(self):
        before=sample_frame(10);before["room"]["id"]="source"
        after=sample_frame(11);after["room"]["id"]="destination"
        rooms,_=find_active_item_rooms([before,after],{(None,"mock-run",11)})
        self.assertEqual(rooms,{("mock-run","source"),("mock-run","destination")})

    def test_used_room_is_removed_but_equipped_unused_item_and_raw_data_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rec=SessionRecorder(root/"records")
            for n in range(50):
                f=sample_frame(n);f["room"]["id"]="used" if n<10 else "clean"
                f["human_input"]["active_trigger"]=n==2
                if n==2:rec.append({"type":"event","event":"item_used","run_id":"mock-run",
                    "game_frame":n,"item_id":45,"active_slot":0})
                rec.append(f)
            rec.close()
            prepare_dataset(rec.path,root/"dataset",exclude_active_items=True)
            rows=[json.loads(line) for line in (root/"dataset/samples.jsonl").read_text().splitlines()]
            self.assertEqual({row["room_id"] for row in rows},{"clean"})
            self.assertTrue(all(not row["active_label"] for row in rows))
            self.assertTrue(all('"active_item"' not in row["prompt"] for row in rows))
            self.assertEqual(sum(m.get("type")=="frame" for m in iter_messages(rec.path)),50)
            meta=json.loads((root/"dataset/dataset.json").read_text())
            self.assertTrue(meta["exclude_active_items"])


if __name__=="__main__":unittest.main()
