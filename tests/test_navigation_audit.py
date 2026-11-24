import copy
from datetime import datetime, timezone
import unittest

from isaac_jev.navigation_audit import analyze_task


def event(kind,t,**fields):
    return {"event":kind,"time":datetime.fromtimestamp(t,timezone.utc).isoformat(),
            "run_id":"run","task_id":"task",**fields}


def frame(n,t,x,room="a",enemy=False):
    return {"run_id":"run","game_frame":n,"received_at_unix":t,"room":{"id":room},
        "player":{"position":{"x":x,"y":100},"inventory":{"trinkets":[0,0]}},
        "entities":[{"kind":"enemy","active_enemy":True}] if enemy else []}


class NavigationAuditTests(unittest.TestCase):
    def test_long_combat_and_teleport_do_not_pollute_navigation(self):
        start=event("task_started",10,target={"kind":"enter_door","target_id":"door-2"})
        frames=[frame(n,10+n/30,100+n*4) for n in range(30)]
        frames.extend(frame(n,10+n/30,600-n,room="b",enemy=True) for n in range(30,630))
        end=event("task_completed",31,game_frame=630)
        logs=[event("action_sent",10,controller_phase="navigation",action_id=20,room_id="a"),
              event("combat_started",11),event("action_sent",11,controller_phase="combat",action_id=15,room_id="b")]
        r=analyze_task(start,end,frames,logs)
        self.assertAlmostEqual(r["phase_seconds"]["navigation"],29/30,places=3)
        self.assertGreater(r["phase_seconds"]["combat"],19)
        self.assertEqual(r["navigation_path_px"],116)
        self.assertEqual(r["navigation_net_px"],116)
        self.assertEqual(r["navigation_command_reversals"],{"x":0,"y":0})

    def test_confirmation_wait_has_no_navigation_distance(self):
        target={"kind":"collect_pickup","target_id":"item","pickup":{"kind":"trinket","item_id":6}}
        frames=[frame(n,10+n/30,100+n) for n in range(51)]
        for f in frames[:10]:f["entities"]=[{"uid":"item","kind":"pickup","variant":350,"subtype":6}]
        frames[-1]["player"]["inventory"]["trinkets"]=[6,0]
        start=event("task_started",10,target=target,before=copy.deepcopy(frames[0]["player"]))
        r=analyze_task(start,event("task_completed",12),frames,[])
        self.assertEqual(r["confirmation"]["delay_frames"],40)
        self.assertEqual(r["navigation_path_px"],9)
        self.assertGreater(r["phase_seconds"]["confirmation"],1)

    def test_missing_frames_and_pauses_do_not_inflate_path(self):
        start=event("task_started",10,target={"kind":"visit_room","target_id":"b"})
        frames=[frame(0,10,0),frame(1,10.033,4),frame(2,20,200),frame(10,20.1,500)]
        r=analyze_task(start,event("task_completed",21),frames,[])
        self.assertEqual(r["navigation_path_px"],4)
        self.assertEqual(r["navigation_net_px"],4)

    def test_applied_inputs_use_own_frame_key_and_ack_attribution(self):
        start=event("task_started",10,target={"kind":"enter_door"})
        frames=[frame(n,10+n/30,100) for n in range(3)]
        a={"run_id":"run","room_id":"a","game_frame":0,
           "requests":[{"request_id":"req","pressed":{"move_right":True}}]}
        frames[0]["applied_controls"]=a;frames[1]["applied_controls"]=copy.deepcopy(a)
        r=analyze_task(start,event("task_completed",11),frames,
            [event("action_ack",10,request_id="req",controller_phase="navigation")])
        self.assertEqual(r["navigation_nonzero_frames"],1)
        self.assertEqual(r["navigation_input_coverage"],round(1/3,4))
        r=analyze_task(start,event("task_completed",11),frames,[])
        self.assertIsNone(r["navigation_input_coverage"])
        self.assertEqual(r["unknown_input_requests"],1)


if __name__=="__main__":unittest.main()
