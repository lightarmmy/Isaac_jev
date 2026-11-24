import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from isaac_jev.mock import sample_frame

spec=importlib.util.spec_from_file_location("timing_audit",Path(__file__).resolve().parents[1]/"scripts/audit_control_timing.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class TimingAuditTests(unittest.TestCase):
    def test_overlapping_intervals_and_repeated_query_snapshots_are_deduplicated(self):
        rows=[]
        for n in range(8):
            f=sample_frame(n);f["mode"]="ai"
            if n>=2:
                # Two snapshots refer to the same actual input frame.
                f["applied_controls"]={"run_id":f["run_id"],"room_id":f["room"]["id"],"game_frame":n-n%2,
                    "requests":[{"request_id":"a","action_id":0,"pressed":{"1":False}}]}
            rows.append(f)
            if n in {0,2}:
                rid="a" if n==0 else "b"
                rows.append({"type":"command","command":"action","request_id":rid,"run_id":f["run_id"],"room_id":f["room"]["id"],"observation_frame":n})
                rows.append({"type":"event","event":"action_started","request_id":rid,"observation_frame":n,"game_frame":n+1,"stop_frame":n+5})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"frames.jsonl.gz";p.write_bytes(gzip.compress(b"".join((json.dumps(r)+"\n").encode() for r in rows)))
            group=module.audit(p)["groups"][0]
        self.assertEqual(group["event_interval_union_frames"],6)
        self.assertEqual(group["actual_query_frames"],3)
        self.assertEqual(group["actual_nonzero_frames"],0)

    def test_partial_gzip_is_explicitly_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"frames.jsonl.gz"
            p.write_bytes(gzip.compress((json.dumps(sample_frame())+"\n").encode())[:-8])
            result=module.audit(p)
        self.assertFalse(result["gzip_complete"]);self.assertFalse(result["trailing_partial_line"])

    def test_same_seed_restart_does_not_merge_input_intervals(self):
        rows=[]
        for n in (0,1,0,1):
            f=sample_frame(n);f["mode"]="ai";rows.append(f)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"frames.jsonl.gz";p.write_bytes(gzip.compress(b"".join((json.dumps(r)+"\n").encode() for r in rows)))
            result=module.audit(p)
        self.assertEqual(len([g for g in result["groups"] if g["phase"]=="all_ai"]),2)
