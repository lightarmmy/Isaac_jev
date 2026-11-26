import copy
import math
import unittest
from isaac_jev.actions import MOVES
from isaac_jev.mock import sample_frame
from isaac_jev.navigation import Navigator, NavigationBlocked, fire_obstacles
from isaac_jev.terrain import Obstacle, terrain_context


def room_frame(pos=(373,380),speed=1,rocks=(),doors=(2,)):
    f=sample_frame();f["entities"]=[];f["player"].update(position={"x":pos[0],"y":pos[1]},velocity={"x":0,"y":0},can_fly=False)
    f["player"]["stats"]["move_speed"]=speed
    r=f["room"];r.update(id="1:0:84",clear=True,top_left={"x":60,"y":140},bottom_right={"x":580,"y":420},grid_width=15,grid=[],navigation_cells=[])
    door_positions={0:(40,280),1:(320,120),2:(600,280),3:(320,440)}
    r["doors"]=[{"slot":s,"open":True,"locked":False,"position":{"x":door_positions[s][0],"y":door_positions[s][1]}} for s in doors]
    for row in range(9):
        for col in range(15):
            x,y=40+40*col,120+40*row;i=row*15+col;inside=0<col<14 and 0<row<8
            door=(x,y) in [door_positions[s] for s in doors];rock=(x,y) in rocks;collision=5 if door else 3 if rock else 0 if inside else 4
            r["navigation_cells"].append([i,x,y,collision,inside])
            if collision:r["grid"].append({"index":i,"position":{"x":x,"y":y},"collision":collision,"kind":"door" if door else "rock" if rock else "wall"})
    return f


def drive(f,limit=400,delay=2,dispatch_every=1,finish_distance=None):
    nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"});queue={};held=(0,0);until=0;positions=[];commands=[];crossed=False;contacts=0
    applied=[];origin=f["player"]["position"]["x"]
    for n in range(limit):
        f["game_frame"]=n;pos=(f["player"]["position"]["x"],f["player"]["position"]["y"])
        if n%dispatch_every==0:
            action=nav.step(f);move=MOVES[action.action_id//5];commands.append(move);queue[n+delay]=(move,action.hold_frames)
            nav.command_feedback(f,action,{"ok":True,"ack":{"game_frame":n+delay-1}})
        if n in queue:held,hold=queue[n];until=n+hold
        if n>=until:held=(0,0)
        applied.append(held!=(0,0))
        scale=math.hypot(*held) or 1;speed=4.5*f["player"]["stats"]["move_speed"]
        v=f["player"]["velocity"];vx=.62*v["x"]+.38*speed*held[0]/scale;vy=.62*v["y"]+.38*speed*held[1]/scale
        new=(pos[0]+2*vx,pos[1]+2*vy)
        if any(o.blocks(pos,new) for o in nav.obstacles(f)):contacts+=1;break
        f["player"]["position"]={"x":new[0],"y":new[1]};f["player"]["velocity"]={"x":vx,"y":vy};positions.append(new)
        if new[0]>582 and abs(new[1]-280)<10:crossed=True;break
        if finish_distance is not None and new[0]-origin>=finish_distance:break
    return {"crossed":crossed,"positions":positions,"commands":commands,"contacts":contacts,"navigator":nav,"applied":applied}


class NavigationTests(unittest.TestCase):
    def test_live_npc_fire_detours_clear_room_pickup_route(self):
        for variant in (0,1,2,3,4):
            with self.subTest(variant=variant):
                f=room_frame(pos=(560,280))
                f['entities']=[
                    {'uid':'fire','type':33,'variant':variant,'kind':'enemy','active_enemy':False,
                     'vulnerable':False,'collision_class':4,'hp':5,'size':13,'position':{'x':440,'y':280}},
                    {'uid':'item','kind':'pickup','variant':100,'subtype':76,'size':12,'position':{'x':320,'y':280}},
                ]
                nav=Navigator(f,{'kind':'collect_treasure_item','target_id':'item'})
                route,obstacles=nav.path(f,(320,280))
                self.assertGreater(len(route),2)
                fire=next(o for o in obstacles if o.kind=='fire')
                self.assertTrue(fire.blocks((560,280),(320,280)))
                self.assertTrue(all(nav.segment_clear(f,a,b,obstacles) for a,b in zip(route,route[1:])))
                self.assertTrue(all(fire.gap(p)>0 for p in route))

    def test_extinguished_fires_and_visual_effects_do_not_block_pickup(self):
        for changes in ({'dead':True},{'hp':0},{'collision_class':0},{'collision_class':3},{'type':1000,'variant':66}):
            with self.subTest(changes=changes):
                f=room_frame(pos=(560,280))
                f['entities']=[{'type':33,'kind':'enemy','hp':5,'collision_class':4,
                                'size':13,'position':{'x':440,'y':280},**changes}]
                nav=Navigator(f,{})
                self.assertEqual(nav.path(f,(320,280))[0],[(560.,280.),(320,280)])

    def test_flying_still_avoids_scaled_fire_collision(self):
        f=room_frame();f['player']['can_fly']=True
        f['entities']=[{'type':33,'size':13,'size_multi':{'x':2,'y':1},'position':{'x':440,'y':280}}]
        obstacle=fire_obstacles(f)[0]
        self.assertGreaterEqual(obstacle.extent[0],26+f['player']['size'])

    def test_appearing_fire_replans_cached_route(self):
        f=room_frame(pos=(120,280));nav=Navigator(f,{'kind':'enter_door','target_id':'door-2'})
        nav.step(f);revision=nav.revision
        f['game_frame']+=1;f['entities']=[{'type':33,'size':13,'position':{'x':320,'y':280}}]
        nav.step(f)
        self.assertGreater(nav.revision,revision);self.assertGreater(len(nav.route),2)

    def test_contact_escapes_before_unreachable_or_consumed_target(self):
        f=room_frame(pos=(459.7,280));f['entities']=[
            {'type':33,'kind':'enemy','size':13,'position':{'x':440,'y':280}}]
        nav=Navigator(f,{'kind':'collect_treasure_item','target_id':'missing'})
        nav.stuck=121
        action=nav.step(f);move=MOVES[action.action_id//5]
        self.assertEqual(nav.stage,'escape_fire');self.assertGreater(move[0],0)
        self.assertGreater(fire_obstacles(f)[0].gap(nav.motion(f,move,1)[-1]),fire_obstacles(f)[0].gap((459.7,280)))

    def test_inward_momentum_does_not_force_waiting_in_fire(self):
        f=room_frame(pos=(459.7,280));f['player']['velocity']={'x':-4.5,'y':0}
        f['entities']=[{'type':33,'size':13,'position':{'x':440,'y':280}}]
        nav=Navigator(f,{})
        action=nav.escape_fire(f)
        self.assertIsNotNone(action);self.assertNotEqual(action.action_id,0)
        self.assertGreater(MOVES[action.action_id//5][0],0)

    def test_escape_never_crosses_another_fire_or_room_wall(self):
        f=room_frame(pos=(550,280));f['entities']=[{'type':33,'size':13,'position':{'x':545,'y':280}}]
        nav=Navigator(f,{})
        action=nav.escape_fire(f)
        points=nav.motion(f,MOVES[action.action_id//5],action.hold_frames)
        self.assertTrue(all(nav.room_contains(f,p) for p in points))
        self.assertGreater(fire_obstacles(f)[0].gap(points[-1]),fire_obstacles(f)[0].gap((550,280)))

    def test_open_door_tunnels_cross_before_regular_path_planning(self):
        for slot,pos in ((1,(321.46,147.25)),(3,(320,424.85)),(0,(54,280)),(2,(588,280))):
            with self.subTest(slot=slot):
                f=room_frame(pos=pos,doors=(slot,));nav=Navigator(f,{"kind":"enter_door","target_id":"door-"+str(slot)})
                action=nav.step(f)
                self.assertEqual(nav.stage,"cross_door");self.assertEqual(MOVES[action.action_id//5],{0:(-1,0),1:(0,-1),2:(1,0),3:(0,1)}[slot])

    def test_door_tunnel_start_can_retreat_to_another_target_without_coordinate_snap(self):
        f=room_frame(pos=(320,424.85),doors=(2,3));nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"})
        action=nav.step(f)
        self.assertEqual(nav.route[0],(320,424.85));self.assertLess(MOVES[action.action_id//5][1],0)
        self.assertEqual(f["player"]["position"],{"x":320,"y":424.85})

    def test_unknown_or_closed_door_does_not_relax_other_room_boundaries(self):
        for pos in ((400,424.85),(320,424.85)):
            f=room_frame(pos=pos,doors=(2,3))
            if pos[0]==320:f["room"]["doors"][1]["open"]=False
            nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"})
            with self.assertRaises(NavigationBlocked):nav.step(f)

    def test_residual_momentum_waits_then_resumes_same_goal(self):
        f=room_frame(pos=(500,400));f["player"]["velocity"]={"x":0,"y":9}
        nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"});action=nav.step(f)
        self.assertEqual(action.action_id,0);self.assertEqual(nav.stage,"settling")
        f["game_frame"]=4;f["player"].update(position={"x":500,"y":407},velocity={"x":0,"y":0})
        action=nav.step(f);self.assertNotEqual(action.action_id,0);self.assertEqual(nav.recovery_attempts,1)
        self.assertEqual(nav.recovery_frames,4)

    def test_momentum_recovery_is_bounded_and_reports_structured_failure(self):
        f=room_frame(pos=(500,400));f["player"]["velocity"]={"x":0,"y":9}
        nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"})
        for n in range(10):f["game_frame"]=n;self.assertEqual(nav.step(f).action_id,0)
        f["game_frame"]=10
        with self.assertRaises(NavigationBlocked) as error:nav.step(f)
        self.assertTrue(error.exception.recoverable);self.assertEqual(error.exception.reason_code,"momentum_recovery_exhausted")

    def test_straight_240px_walk_preserves_coverage_with_sparse_dispatch(self):
        f=room_frame(pos=(120,280));result=drive(f,dispatch_every=2,delay=2,finish_distance=240)
        self.assertEqual(result["contacts"],0)
        travel=result["positions"][-1][0]-120;seconds=len(result["positions"])/30
        self.assertGreaterEqual(travel,240);self.assertGreaterEqual(travel/seconds,80)
        self.assertGreaterEqual(sum(result["applied"])/len(result["applied"]),.8)

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
        self.assertFalse(rock.blocks((212,330),(211,330))) # partial outward step, still in expanded padding
        circle=Obstacle((320,280),(30,30),"pickup","circle")
        self.assertFalse(circle.blocks((295,280),(294,280)))
        self.assertTrue(circle.blocks((295,280),(296,280)))

    def test_empty_collidable_pedestal_is_not_a_target_and_can_be_left(self):
        f=room_frame(pos=(300,280));f["entities"]=[{"uid":"pedestal","kind":"pickup","variant":100,"subtype":0,
            "size":12,"collision_class":4,"position":{"x":320,"y":280}}]
        f["room"]["doors"]=[{"slot":0,"open":True,"locked":False,"position":{"x":40,"y":280}}]
        f["room"]["navigation_cells"][60][3]=5
        next(g for g in f["room"]["grid"] if g["index"]==60).update(kind="door",collision=5)
        nav=Navigator(f,{"kind":"enter_door","target_id":"door-0"})
        action=nav.step(f)
        self.assertLess(MOVES[action.action_id//5][0],0)
        obstacle=next(o for o in nav.obstacles(f) if o.kind=="empty_pedestal")
        self.assertTrue(obstacle.blocks((270,280),(340,280))) # still physically collidable

    def test_noncollidable_empty_pedestal_is_ignored_but_real_item_stays_avoided(self):
        f=room_frame(pos=(120,280));e={"uid":"pedestal","kind":"pickup","variant":100,"subtype":0,
            "size":12,"collision_class":0,"position":{"x":320,"y":280}};f["entities"]=[e]
        nav=Navigator(f,{"kind":"enter_door","target_id":"door-2"});goal,_=nav.target_position(f)
        self.assertEqual(len(nav.path(f,goal)[0]),2)
        e["subtype"]=379
        self.assertGreater(len(nav.path(f,goal)[0]),2)

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
