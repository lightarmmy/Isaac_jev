"""Feedback navigation through room grid cells, using normal directional inputs."""
import heapq
import math
from .actions import MOVES, Action


def xy(obj):return float(obj.get("x",0)),float(obj.get("y",0))


def distance(a,b):return math.hypot(a[0]-b[0],a[1]-b[1])


def is_shop_item(entity):
    price=entity.get("pickup_price")
    return bool(entity.get("pickup_shop")) or (price is not None and price!=0)


class Navigator:
    def __init__(self,frame,target):
        self.room_id=frame["room"]["id"];self.target=target;self.last_position=None;self.stuck=0

    def obstacles(self,frame):
        p=frame["player"];radius=max(8,float(p.get("size",10)))
        obstacles=[]
        for g in frame["room"].get("grid",[]):
            kind=g.get("kind","grid");collision=g.get("collision",0)
            if kind=="door":continue
            if kind=="pit" and p.get("can_fly"):continue
            if collision or kind in {"stairs","trapdoor","teleporter","button","spikes","fire","tnt"}:
                obstacles.append((xy(g["position"]),18+radius))
        for e in frame.get("entities",[]):
            kind=e.get("kind")
            if kind=="pickup":
                target=self.target.get("target_id")==e.get("uid")
                # Shop goods are obstacles, even for a mistakenly submitted pickup target.
                if is_shop_item(e) or (not target and (e.get("variant") in {50,51,52,53,54,55,56,57,58,60,100} or e.get("variant",0)>=50)):
                    obstacles.append((xy(e["position"]),float(e.get("size",10))+radius+8))
            elif kind=="bomb" or (kind=="laser" and e.get("owner_type")!=1):
                obstacles.append((xy(e["position"]),radius+float(e.get("size",20))+35))
            elif kind=="slot" or e.get("type")==6:
                obstacles.append((xy(e["position"]),radius+float(e.get("size",15))+12))
            elif kind=="effect" and e.get("variant") in {22,23,24,25,26,27,28,29,30,32,53,54,55,56}:
                obstacles.append((xy(e["position"]),radius+float(e.get("size",10))+12))
        return obstacles

    def path(self,frame,goal):
        room=frame["room"];cells=room.get("navigation_cells",[])
        if not cells or not isinstance(cells[0],list):raise ValueError("Mod needs navigation_cells arrays; reload Isaac Jev Bridge 0.4.1")
        p=frame["player"];start=xy(p["position"]);radius=max(8,float(p.get("size",10)))
        tl=xy(room["top_left"]);br=xy(room["bottom_right"]);obstacles=self.obstacles(frame)
        width=room["grid_width"]
        points={int(c[0]):(float(c[1]),float(c[2])) for c in cells}
        def free(pos):
            return tl[0]+radius<=pos[0]<=br[0]-radius and tl[1]+radius<=pos[1]<=br[1]-radius and all(distance(pos,o)>r for o,r in obstacles)
        grid_by_index={g.get("index"):g for g in room.get("grid",[])}
        allowed=set()
        for cell in cells:
            index=int(cell[0]);g=grid_by_index.get(index,{})
            collision=cell[3] if len(cell)>3 else 0;inside=cell[4] if len(cell)>4 else True
            if inside and (not collision or g.get("kind")=="door" or (g.get("kind")=="pit" and p.get("can_fly"))):allowed.add(index)
        valid={i for i,pos in points.items() if i in allowed and free(pos)}
        if not valid:raise ValueError("No supported walkable path")
        source=min(valid,key=lambda i:distance(points[i],start));end=min(valid,key=lambda i:distance(points[i],goal))
        queue=[(0,source)];cost={source:0};parent={}
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
                # Check the swept segment, not just endpoints.
                a,b=points[current],points[nxt]
                if not all(free((a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)) for t in (0.25,0.5,0.75)):continue
                new=cost[current]+distance(a,b)
                if new<cost.get(nxt,float("inf")):
                    cost[nxt]=new;parent[nxt]=current;heapq.heappush(queue,(new+distance(points[nxt],points[end]),nxt))
        if end not in cost:raise ValueError("Target is blocked by obstacles or unsupported interactions")
        route=[end]
        while route[-1]!=source:route.append(parent[route[-1]])
        route.reverse();waypoints=[points[i] for i in route]
        # The start-to-grid connection must also avoid forbidden pickups.
        first=waypoints[0]
        if any(distance((start[0]+(first[0]-start[0])*t,start[1]+(first[1]-start[1])*t),o)<=r for o,r in obstacles for t in (0.25,0.5,0.75)):
            raise ValueError("Start position cannot connect to a safe path")
        return waypoints,obstacles

    def step(self,frame):
        pos=xy(frame["player"]["position"])
        if self.last_position is not None and distance(pos,self.last_position)<0.2:self.stuck+=1
        else:self.stuck=0
        self.last_position=pos
        if self.stuck>120:raise ValueError("Character is stuck; choose another target")
        target=self.target
        if target["kind"] in {"enter_door","inspect_shop"}:
            door=next((d for d in frame["room"]["doors"] if "door-"+str(d["slot"])==target["target_id"]),None)
            if not door:raise ValueError("Door disappeared")
            if not door.get("open") and not door.get("locked"):raise ValueError("Door is closed")
            if door.get("locked") and frame["player"].get("inventory",{}).get("keys",0)<1:raise ValueError("Insufficient keys")
            goal=xy(door["position"])
            if distance(pos,goal)<25:
                dirs={0:(-1,0),1:(0,-1),2:(1,0),3:(0,1),4:(-1,0),5:(0,-1),6:(1,0),7:(0,1)}
                move=dirs[door["slot"]]
                # A door approach may not cross a shop item or unsupported object.
                sweep=(pos[0]+move[0]*12,pos[1]+move[1]*12)
                if any(distance(sweep,o)<=r for o,r in self.obstacles(frame)):raise ValueError("Door approach blocked")
                return Action(MOVES.index(move)*5,False,3)
        else:
            entity=next((e for e in frame["entities"] if e.get("uid")==target["target_id"]),None)
            if not entity:return None
            if is_shop_item(entity):raise ValueError("Shop goods are view-only")
            goal=xy(entity["position"])
        route,obstacles=self.path(frame,goal)
        # The nearest grid centre can be behind the character. Replanning must
        # advance to the next cell instead of oscillating back to that centre.
        waypoint=next((p for p in route[1:] if distance(p,pos)>8),goal)
        if distance(pos,route[-1])<22:waypoint=goal
        dx,dy=waypoint[0]-pos[0],waypoint[1]-pos[1]
        move=(0 if abs(dx)<3 else (1 if dx>0 else -1),0 if abs(dy)<3 else (1 if dy>0 else -1))
        speed=max(1,4*float(frame["player"].get("stats",{}).get("move_speed",1)))
        scale=math.hypot(*move) or 1
        # Avoid buying/activating something during the final precise approach.
        future=(pos[0]+move[0]*speed*3/scale,pos[1]+move[1]*speed*3/scale)
        if any(distance((pos[0]+(future[0]-pos[0])*t,pos[1]+(future[1]-pos[1])*t),o)<=r for o,r in obstacles for t in (0.25,0.5,0.75,1)):
            raise ValueError("Final approach blocked")
        return Action(MOVES.index(move)*5,False,3)
