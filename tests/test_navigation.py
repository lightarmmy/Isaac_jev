import copy
import math
import unittest
from isaac_jev.actions import MOVES
from isaac_jev.mock import sample_frame
from isaac_jev.navigation import Navigator
from isaac_jev.terrain import Obstacle, terrain_context


def room_frame(pos=(373,380),speed=1,rocks=()):
    f=sample_frame();f["entities"]=[];f["player"].update(position={"x":pos[0],"y":pos[1]},velocity={"x":0,"y":0},can_fly=False)
    f["player"]["stats"]["move_speed"]=speed
    r=f["room"];r.update(id="1:0:84",clear=True,top_left={"x":60,"y":140},bottom_right={"x":580,"y":420},grid_width=15,grid=[],navigation_cells=[])
    r["doors"]=[{"slot":2,"open":True,"locked":False,"position":{"x":600,"y":280}}]
    for row in range(9):
        for col in range(15):
            x,y=40+40*col,120+40*row;i=row*15+col;inside=0<col<14 and 0<row<8
            door=(col,row)==(14,4);rock=(x,y) in rocks;collision=5 if door else 3 if rock else 0 if inside else 4
            r["navigation_cells"].append([i,x,y,collision,inside])
            if collision:r["grid"].append({"index":i,"position":{"x":x,"y":y},"collision":collision,"kind":"door" if door else "rock" if rock else "wall"})
    return f


def drive(f,limit=400,delay=2):
    nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"});queue={};held=(0,0);until=0;positions=[];commands=[];crossed=False;contacts=0
    for n in range(limit):
        f["game_frame"]=n;pos=(f["player"]["position"]["x"],f["player"]["position"]["y"])
        action=nav.step(f);move=MOVES[action.action_id//5];commands.append(move);queue[n+delay]=(move,action.hold_frames)
        nav.command_feedback(f,action,{"ok":True,"ack":{"game_frame":n+delay-1}})
        if n in queue:held,hold=queue[n];until=n+hold
        if n>=until:held=(0,0)
        scale=math.hypot(*held) or 1;speed=4.5*f["player"]["stats"]["move_speed"]
        v=f["player"]["velocity"];vx=.62*v["x"]+.38*speed*held[0]/scale;vy=.62*v["y"]+.38*speed*held[1]/scale
        new=(pos[0]+2*vx,pos[1]+2*vy)
        if any(o.blocks(pos,new) for o in nav.obstacles(f)):contacts+=1;break
        f["player"]["position"]={"x":new[0],"y":new[1]};f["player"]["velocity"]={"x":vx,"y":vy};positions.append(new)
        if new[0]>582 and abs(new[1]-280)<10:crossed=True;break
    return {"crossed":crossed,"positions":positions,"commands":commands,"contacts":contacts,"navigator":nav}


class NavigationTests(unittest.TestCase):
    def test_open_path_uses_actual_start_and_goal_instead_of_grid_centres(self):
        f=room_frame();nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"})
        goal,_=nav.target_position(f);route,_=nav.path(f,goal)
        self.assertEqual(route,[(373,380),goal])
        f["game_frame"]=1;nav.step(f);revision=nav.revision
        f["game_frame"]=2;f["player"]["position"]["x"]+=6;nav.step(f)
        self.assertEqual(nav.revision,revision)

    def test_aligned_walk_to_door_has_no_lateral_commands(self):
        result=drive(room_frame(pos=(240,280)))
        self.assertTrue(result["crossed"]);self.assertEqual(result["contacts"],0)
        self.assertTrue(all(move[1]==0 for move in result["commands"]))
        self.assertTrue(all(abs(y-280)<.001 for x,y in result["positions"]))

    def test_offset_approach_with_inertia_and_command_delay_settles_before_door(self):
        for pos in ((373,380),(357,312.6)):
            with self.subTest(pos=pos):
                result=drive(room_frame(pos=pos))
                self.assertTrue(result["crossed"]);self.assertEqual(result["contacts"],0)
                ys=[y for x,y in result["positions"]]
                self.assertGreater(min(ys),270)
                # No repeated up/down serpentine corrections along the open room.
                signs=[m[1] for m in result["commands"] if m[1]]
                self.assertLessEqual(sum(a!=b for a,b in zip(signs,signs[1:])),2)

    def test_measured_input_delay_and_speed_changes_do_not_restore_serpentine_motion(self):
        for speed in (.7,1,1.3):
            for delay in (1,2,3):
                with self.subTest(speed=speed,delay=delay):
                    result=drive(room_frame(speed=speed),delay=delay)
                    self.assertTrue(result["crossed"]);self.assertEqual(result["contacts"],0)
                    self.assertEqual(result["navigator"].input_delay,delay)

    def test_obstacle_route_and_feedback_avoid_inflated_rock(self):
        f=room_frame(pos=(120,280),rocks=((320,280),));nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"})
        goal,_=nav.target_position(f);route,obstacles=nav.path(f,goal)
        self.assertGreater(len(route),2)
        self.assertTrue(all(nav.segment_clear(f,a,b,obstacles) for a,b in zip(route,route[1:])))
        result=drive(f)
        self.assertTrue(result["crossed"]);self.assertEqual(result["contacts"],0)

    def test_changed_collision_replans_cached_path(self):
        f=room_frame(pos=(120,280));nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"});nav.step(f)
        revision=nav.revision;changed=room_frame(pos=(120,280),rocks=((320,280),));changed["game_frame"]=1
        nav.step(changed);self.assertGreater(nav.revision,revision);self.assertGreater(len(nav.route),2)

    def test_box_sweep_catches_corner_contact_and_can_escape_existing_contact(self):
        rock=Obstacle((240,320),(30,30),"rock")
        self.assertTrue(rock.blocks((200,295),(215,295))) # expanded box corner, outside old centre circle
        self.assertTrue(rock.blocks((210,330),(230,330)))
        self.assertFalse(rock.blocks((210,330),(190,330)))

    def test_near_door_still_aligns_before_outward_push(self):
        f=room_frame(pos=(565,294));nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"})
        action=nav.step(f);self.assertNotEqual(nav.stage,"cross_door");self.assertLessEqual(MOVES[action.action_id//5][0],0)

    def test_terrain_preview_identifies_right_blocking_and_flying_pits(self):
        f=room_frame(pos=(210,330),rocks=((240,320),));facts=terrain_context(f)
        moves={r["move"]:r for r in facts["directions"]}
        self.assertTrue(moves["right"]["blocked_within_12px"])
        self.assertEqual(moves["right"]["reason"],"rock")
        self.assertFalse(moves["left"]["blocked_within_12px"])
        for g in f["room"]["grid"]:
            if g["kind"]=="rock":g.update(kind="pit",collision=1)
        f["player"]["can_fly"]=True
        self.assertFalse(next(r for r in terrain_context(f)["directions"] if r["move"]=="right")["blocked_within_12px"])

    def test_future_terrain_preview_does_not_change_v004_model_features(self):
        from isaac_jev.features import policy_observation
        f=room_frame(pos=(210,330),rocks=((240,320),));before=policy_observation(f,include_active=False,include_controls=False)
        terrain_context(f)
        self.assertEqual(before,policy_observation(f,include_active=False,include_controls=False))
