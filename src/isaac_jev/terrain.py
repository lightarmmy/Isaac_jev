"""Player-sized static collision geometry and opt-in terrain facts for future training."""
from dataclasses import dataclass
import math
from .actions import MOVES, MOVE_NAMES


def xy(obj):return float(obj.get("x",0)),float(obj.get("y",0))


def player_radius(frame):
    p=frame["player"];size=max(8,float(p.get("size",10)));multi=p.get("size_multi") or {}
    return size*max(1,float(multi.get("x",1))),size*max(1,float(multi.get("y",1)))


@dataclass(frozen=True)
class Obstacle:
    center:tuple
    extent:tuple
    kind:str="grid"
    shape:str="box"

    def gap(self,pos):
        dx=abs(pos[0]-self.center[0])-self.extent[0];dy=abs(pos[1]-self.center[1])-self.extent[1]
        if self.shape=="circle":return math.dist(pos,self.center)-self.extent[0]
        return math.hypot(max(0,dx),max(0,dy))+min(max(dx,dy),0)

    def entry(self,a,b):
        """First segment contact in [0,1], or None. Tangencies count as contact."""
        vx,vy=b[0]-a[0],b[1]-a[1]
        if self.shape=="circle":
            x,y=a[0]-self.center[0],a[1]-self.center[1];aa=vx*vx+vy*vy
            if x*x+y*y<=self.extent[0]**2:return 0.0
            if aa<1e-12:return None
            bb=x*vx+y*vy;cc=x*x+y*y-self.extent[0]**2;disc=bb*bb-aa*cc
            if disc<0:return None
            t=(-bb-math.sqrt(disc))/aa
            return t if 0<=t<=1 else None
        low,high=0.0,1.0
        for axis,v in enumerate((vx,vy)):
            left=self.center[axis]-self.extent[axis];right=self.center[axis]+self.extent[axis]
            if abs(v)<1e-12:
                if a[axis]<left or a[axis]>right:return None
            else:
                t1,t2=(left-a[axis])/v,(right-a[axis])/v
                low=max(low,min(t1,t2));high=min(high,max(t1,t2))
                if low>high:return None
        return low

    def blocks(self,a,b):
        if self.entry(a,b) is None:return False
        # Conservative radius padding can contain a character already touching a
        # rock. Permit an outward escape, never motion deeper into that obstacle.
        gap=self.gap(a)
        if gap<=0 and self.gap(b)>gap+1e-6:
            samples=[self.gap((a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t)) for t in (.01,.25,.5,.75,1)]
            if min(samples)>=gap-1e-6:return False
        return True


def static_obstacles(frame,avoid_interactions=False,padding=2):
    """40 px grid boxes, expanded by player size; include collision-only cells."""
    rx,ry=player_radius(frame);room=frame["room"];flying=frame["player"].get("can_fly",False)
    grids={g.get("index"):g for g in room.get("grid",[]) if g.get("index") is not None}
    entries=list(room.get("grid",[]));known=set(grids)
    for c in room.get("navigation_cells",[]):
        if len(c)>3 and c[3] and c[0] not in known:
            entries.append({"index":c[0],"position":{"x":c[1],"y":c[2]},"collision":c[3],"kind":"pit" if c[3]==1 else "grid"})
    obstacles=[]
    for g in entries:
        kind=g.get("kind","grid");collision=g.get("collision",0)
        if kind=="door" or (flying and (kind=="pit" or collision==1)):continue
        avoid=avoid_interactions and kind in {"stairs","trapdoor","teleporter","button","spikes","fire","tnt"}
        if collision or avoid:obstacles.append(Obstacle(xy(g["position"]),(20+rx+padding,20+ry+padding),kind))
    return obstacles


def terrain_context(frame):
    """Experimental facts only: NOT injected into deployed v004 model inputs."""
    pos=xy(frame["player"]["position"]);rx,ry=player_radius(frame);room=frame["room"]
    tl,br=xy(room["top_left"]),xy(room["bottom_right"]);obstacles=static_obstacles(frame,padding=0)
    reach=80;directions=[]
    for name,(dx,dy) in zip(MOVE_NAMES[1:],MOVES[1:]):
        length=math.hypot(dx,dy);ux,uy=dx/length,dy/length
        end=(pos[0]+ux*reach,pos[1]+uy*reach);contacts=[]
        for obstacle in obstacles:
            if obstacle.blocks(pos,end):
                t=obstacle.entry(pos,end);contacts.append((max(0,t or 0)*reach,obstacle.kind))
        limits=[]
        for coordinate,u,left,right in ((pos[0],ux,tl[0]+rx,br[0]-rx),(pos[1],uy,tl[1]+ry,br[1]-ry)):
            if abs(u)>1e-9:limits.append(max(0,((right if u>0 else left)-coordinate)/u))
        if limits:contacts.append((min(limits),"room_boundary"))
        clearance,reason=min(contacts,default=(reach,"none"))
        directions.append({"move":name,"clearance_px":round(min(reach,clearance),1),"blocked_within_12px":clearance<=12,"reason":reason if clearance<=reach else "none"})
    # 9x9 local occupancy at 20 px pitch. This describes player-centre collision,
    # rather than an image of the room or a privileged map of future rooms.
    occupancy=[]
    for row in range(-4,5):
        cells=[]
        for col in range(-4,5):
            point=(pos[0]+col*20,pos[1]+row*20)
            outside=not (tl[0]+rx<=point[0]<=br[0]-rx and tl[1]+ry<=point[1]<=br[1]-ry)
            cells.append(int(outside or any(o.gap(point)<0 for o in obstacles)))
        occupancy.append(cells)
    nearby=sorted(obstacles,key=lambda o:math.dist(o.center,pos))[:12]
    return {"version":"terrain-preview-v1","player_radius_px":[rx,ry],"local_occupancy_pitch_px":20,
        "local_occupancy_9x9":occupancy,"directions":directions,
        "nearby_obstacles":[{"kind":o.kind,"dx":round(o.center[0]-pos[0],1),"dy":round(o.center[1]-pos[1],1),"expanded_half_size":list(o.extent)} for o in nearby],
        "limitations":"Static geometry approximation. Does not model moving enemies, projectile occlusion, special weapons or a complete physics rollout."}
