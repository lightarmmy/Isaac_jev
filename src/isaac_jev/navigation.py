"""Stable room paths and velocity-aware steering through normal directional input."""
import heapq
import math
from .actions import MOVES, Action
from .terrain import Obstacle, player_radius, static_obstacles, xy


def distance(a,b):return math.dist(a,b)


def is_shop_item(entity):
    price=entity.get("pickup_price")
    return bool(entity.get("pickup_shop")) or (price is not None and price!=0)


DOOR_DIRECTIONS={0:(-1,0),1:(0,-1),2:(1,0),3:(0,1),4:(-1,0),5:(0,-1),6:(1,0),7:(0,1)}


class Navigator:
    def __init__(self,frame,target):
        self.room_id=frame["room"]["id"];self.target=target;self.last_position=None;self.stuck=0
        self.last_frame=None;self.route=[];self.waypoint_index=1;self.goal=None;self.signature=None
        self.revision=0;self.stage="approach"
        self.mask_cells=None;self.mask={};self.mask_origin=(0,0)
        self.pending_inputs={}
        self.input_delay=2

    def describe(self):
        return {"revision":self.revision,"stage":self.stage,"goal":self.goal,
            "waypoint_index":self.waypoint_index,"waypoints":self.route,"stuck_frames":self.stuck,"input_delay_frames":self.input_delay}

    def command_feedback(self,frame,action,result):
        provisional=frame["game_frame"]+self.input_delay
        self.pending_inputs.pop(provisional,None)
        if result.get("ok"):
            acknowledged=(result.get("ack") or {}).get("game_frame")
            if type(acknowledged) is int:
                self.input_delay=max(1,min(5,acknowledged-frame["game_frame"]+1))
            self.pending_inputs[frame["game_frame"]+self.input_delay]=(MOVES[action.action_id//5],action.hold_frames)

    def obstacles(self,frame):
        radius=max(player_radius(frame));obstacles=static_obstacles(frame,avoid_interactions=True)
        for e in frame.get("entities",[]):
            if e.get("dead"):continue
            kind=e.get("kind");size=float(e.get("size",10))
            if kind=="pickup":
                target=self.target.get("target_id")==e.get("uid")
                if is_shop_item(e) or (not target and e.get("variant",0)>=50):
                    obstacles.append(Obstacle(xy(e["position"]),(size+radius+8,)*2,"pickup","circle"))
            elif kind=="bomb" or (kind=="laser" and e.get("owner_type")!=1):
                obstacles.append(Obstacle(xy(e["position"]),(radius+size+35,)*2,kind,"circle"))
            elif kind=="slot" or e.get("type")==6:
                obstacles.append(Obstacle(xy(e["position"]),(radius+size+12,)*2,"slot","circle"))
            elif kind=="effect" and e.get("variant") in {22,23,24,25,26,27,28,29,30,32,53,54,55,56}:
                obstacles.append(Obstacle(xy(e["position"]),(radius+size+12,)*2,kind,"circle"))
        return obstacles

    def segment_clear(self,frame,a,b,obstacles,door_exit=False):
        if any(o.blocks(a,b) for o in obstacles):return False
        if door_exit:return True # Only the aligned selected door can cross room bounds.
        rx,ry=player_radius(frame);tl=xy(frame["room"]["top_left"]);br=xy(frame["room"]["bottom_right"])
        def inside(pos):return tl[0]+rx<=pos[0]<=br[0]-rx and tl[1]+ry<=pos[1]<=br[1]-ry
        if not inside(a) or not inside(b):return False
        cells=frame["room"].get("navigation_cells",[])
        if cells and len(cells[0])>4:
            width=frame["room"]["grid_width"]
            if cells is not self.mask_cells:
                self.mask_cells=cells;self.mask={int(c[0]):bool(c[4]) for c in cells}
                first=cells[0];row,col=divmod(int(first[0]),width)
                self.mask_origin=(first[1]-col*40,first[2]-row*40)
            steps=max(1,math.ceil(distance(a,b)/10))
            for i in range(steps+1):
                pos=(a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps)
                col=math.floor((pos[0]-self.mask_origin[0])/40+.5);row=math.floor((pos[1]-self.mask_origin[1])/40+.5)
                if not 0<=col<width or row<0 or not self.mask.get(row*width+col,False):return False
        return True

    def path(self,frame,goal):
        room=frame["room"];cells=room.get("navigation_cells",[])
        if not cells or not isinstance(cells[0],list):raise ValueError("Mod needs navigation_cells arrays; reload Isaac Jev Bridge 0.4.2")
        start=xy(frame["player"]["position"]);obstacles=self.obstacles(frame)
        if self.segment_clear(frame,start,goal,obstacles):return [start,goal],obstacles
        width=room["grid_width"];points={int(c[0]):(float(c[1]),float(c[2])) for c in cells}
        valid={i for i,pos in points.items() if self.segment_clear(frame,pos,pos,obstacles)}
        if not valid:raise ValueError("No supported walkable path")
        sources=sorted(valid,key=lambda i:distance(points[i],start));ends=sorted(valid,key=lambda i:distance(points[i],goal))
        source=next((i for i in sources if self.segment_clear(frame,start,points[i],obstacles)),None)
        end=next((i for i in ends if self.segment_clear(frame,points[i],goal,obstacles)),None)
        if source is None or end is None:raise ValueError("Start/target cannot connect to a safe path")
        queue=[(distance(points[source],goal),source)];cost={source:0};parent={}
        while queue:
            _,current=heapq.heappop(queue)
            if current==end:break
            row,col=divmod(current,width)
            for dx,dy in MOVES[1:]:
                nc,nr=col+dx,row+dy
                if not 0<=nc<width or nr<0:continue
                nxt=nr*width+nc
                if nxt not in valid:continue
                if dx and dy and (row*width+nc not in valid or nr*width+col not in valid):continue
                a,b=points[current],points[nxt]
                if not self.segment_clear(frame,a,b,obstacles):continue
                new=cost[current]+distance(a,b)
                if new<cost.get(nxt,float("inf")):
                    cost[nxt]=new;parent[nxt]=current;heapq.heappush(queue,(new+distance(b,points[end]),nxt))
        if end not in cost:raise ValueError("Target is blocked by obstacles or unsupported interactions")
        route=[end]
        while route[-1]!=source:route.append(parent[route[-1]])
        route=[start]+[points[i] for i in reversed(route)]+[goal]
        smooth=[start];at=0
        while at<len(route)-1:
            nxt=next(i for i in range(len(route)-1,at,-1) if self.segment_clear(frame,route[at],route[i],obstacles))
            smooth.append(route[nxt]);at=nxt
        return smooth,obstacles

    def target_position(self,frame):
        target=self.target
        if target["kind"] in {"enter_door","inspect_shop"}:
            door=next((d for d in frame["room"]["doors"] if "door-"+str(d["slot"])==target["target_id"]),None)
            if not door:raise ValueError("Door disappeared")
            if not door.get("open") and not door.get("locked"):raise ValueError("Door is closed")
            if door.get("locked") and frame["player"].get("inventory",{}).get("keys",0)<1:raise ValueError("Insufficient keys")
            goal=xy(door["position"]);tl=xy(frame["room"]["top_left"]);br=xy(frame["room"]["bottom_right"]);rx,ry=player_radius(frame)
            anchor=(max(tl[0]+rx+2,min(br[0]-rx-2,goal[0])),max(tl[1]+ry+2,min(br[1]-ry-2,goal[1])))
            return anchor,door
        entity=next((e for e in frame["entities"] if e.get("uid")==target["target_id"]),None)
        if not entity:return None,None
        if is_shop_item(entity):raise ValueError("Shop goods are view-only")
        return xy(entity["position"]),None

    @staticmethod
    def axis_input(delta,velocity):
        # Release early for input delay and natural coasting. Velocity is per
        # engine update; Isaac has two engine updates per sampled logic frame.
        if abs(delta)<=5:return 0
        if delta*velocity>0 and abs(delta)<=abs(velocity)*3.3+5:return 0
        return 1 if delta>0 else -1

    def pending_motion(self,frame):
        pos=xy(frame["player"]["position"]);velocity=xy(frame["player"].get("velocity",{}))
        speed=max(1,4.5*float(frame["player"].get("stats",{}).get("move_speed",1)));points=[pos]
        for n in range(frame["game_frame"],frame["game_frame"]+self.input_delay):
            previous=max((key for key in self.pending_inputs if key<=n),default=None)
            move=(0,0)
            if previous is not None:
                candidate,hold=self.pending_inputs[previous]
                if n<previous+hold:move=candidate
            scale=math.hypot(*move) or 1
            velocity=tuple(.62*velocity[i]+.38*speed*move[i]/scale for i in range(2))
            pos=(pos[0]+2*velocity[0],pos[1]+2*velocity[1]);points.append(pos)
        return points,velocity,speed

    def motion(self,frame,move,hold):
        points,velocity,speed=self.pending_motion(frame);pos=points[-1];scale=math.hypot(*move) or 1
        for _ in range(hold):
            velocity=tuple(.62*velocity[i]+.38*speed*move[i]/scale for i in range(2))
            pos=(pos[0]+2*velocity[0],pos[1]+2*velocity[1]);points.append(pos)
        return points

    def make_action(self,move,hold,n):
        self.pending_inputs={key:value for key,value in self.pending_inputs.items() if key>=n-6}
        self.pending_inputs[n+self.input_delay]=(move,hold)
        return Action(MOVES.index(move)*5,False,hold)

    def step(self,frame):
        if frame["room"]["id"]!=self.room_id:raise ValueError("Navigation room changed")
        pos=xy(frame["player"]["position"]);n=frame["game_frame"]
        if n!=self.last_frame:
            if self.last_position is not None and distance(pos,self.last_position)<.2:self.stuck+=max(1,n-(self.last_frame or n))
            else:self.stuck=0
            self.last_frame=n;self.last_position=pos
        if self.stuck>120:raise ValueError("Character is stuck; choose another target")
        goal,door=self.target_position(frame)
        if goal is None:return None
        obstacles=self.obstacles(frame);signature=(tuple(obstacles),bool(frame["player"].get("can_fly")))
        if self.goal is None or distance(goal,self.goal)>4 or signature!=self.signature or not self.route:
            self.route,_=self.path(frame,goal);self.goal=goal;self.signature=signature;self.waypoint_index=1;self.revision+=1
        velocity=xy(frame["player"].get("velocity",{}));self.stage="approach"
        if door:
            outward=DOOR_DIRECTIONS[door["slot"]];cross=1 if outward[0] else 0
            plane=(pos[0]-goal[0])*outward[0]+(pos[1]-goal[1])*outward[1]
            aligned=abs(pos[cross]-xy(door["position"])[cross])<=5 and abs(velocity[cross])<.4
            if plane>=-6 and aligned:
                self.stage="cross_door";points=self.motion(frame,outward,2)
                if not all(self.segment_clear(frame,a,b,obstacles,door_exit=True) for a,b in zip(points,points[1:])):raise ValueError("Door approach blocked")
                return self.make_action(outward,2,n)
            if plane>=-18:self.stage="align_door"
        # Progress is monotonic; visibility permits skipping a corner, not
        # snapping backwards to a newly selected nearest grid centre.
        visible=[i for i in range(self.waypoint_index,len(self.route)) if self.segment_clear(frame,pos,self.route[i],obstacles)]
        if visible:self.waypoint_index=max(visible)
        waypoint=self.route[self.waypoint_index];pending,predicted_velocity,_=self.pending_motion(frame)
        predicted=pending[-1];delta=(waypoint[0]-predicted[0],waypoint[1]-predicted[1])
        move=tuple(self.axis_input(delta[i],predicted_velocity[i]) for i in range(2))
        hold=1 if distance(pos,waypoint)<45 or self.stage=="align_door" else 3
        points=self.motion(frame,move,hold)
        if not all(self.segment_clear(frame,a,b,obstacles) for a,b in zip(points,points[1:])):
            candidates=[]
            for alternative in MOVES:
                trajectory=self.motion(frame,alternative,1)
                if all(self.segment_clear(frame,a,b,obstacles) for a,b in zip(trajectory,trajectory[1:])):
                    cost=distance(trajectory[-1],waypoint)+3*distance(alternative,move);candidates.append((cost,alternative))
            if not candidates:raise ValueError("Approach blocked by collision or momentum")
            _,move=min(candidates);hold=1
        return self.make_action(move,hold,n)
