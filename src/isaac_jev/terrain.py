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

def combat_terrain(frame):
    """Whole native room mask plus player-relative geometry, including L-room voids."""
    pos=xy(frame['player']['position']);rx,ry=player_radius(frame);room=frame['room']
    cells=room.get('navigation_cells',[]);width=room.get('grid_width',0)
    grids={g.get('index'):g for g in room.get('grid',[])};mask={};points={}
    symbols={'wall':'W','pit':'P','door':'D','spikes':'S','fire':'F','tnt':'T','poop':'O'}
    for c in cells:
        if len(c)<3 or not width:continue
        index=c[0];g=grids.get(index,{})
        outside=len(c)>4 and not c[4];collision=c[3] if len(c)>3 else g.get('collision',0)
        symbol='W' if outside else symbols.get(g.get('kind'),'X' if collision else '.')
        if not outside and collision==1:symbol='P'
        mask[index]=symbol;points[index]=(c[1],c[2])
    obstacles=static_obstacles(frame,padding=0)
    obstacles.extend(Obstacle(points[i],(20+rx,20+ry),'room_wall') for i,s in mask.items() if s=='W' and not any(g.get('index')==i and g.get('collision') for g in room.get('grid',[])))
    # Only colliders whose bounds intersect a query's reachable box can affect it.
    # Keep the full set for the room map and nearest-obstacle output.
    def intersects_reach(obstacle,reach):
        ex,ey=obstacle.extent
        if obstacle.shape=='circle':ey=ex
        return abs(obstacle.center[0]-pos[0])<=reach+ex and abs(obstacle.center[1]-pos[1])<=reach+ey
    local_obstacles=[o for o in obstacles if intersects_reach(o,120)]
    direction_obstacles=[o for o in obstacles if intersects_reach(o,80)]
    tl,br=xy(room['top_left']),xy(room['bottom_right'])
    def blocked(point):
        return not(tl[0]+rx<=point[0]<=br[0]-rx and tl[1]+ry<=point[1]<=br[1]-ry) or any(o.gap(point)<0 for o in local_obstacles)
    local=[[int(blocked((pos[0]+x*40,pos[1]+y*40))) for x in range(-3,4)] for y in range(-3,4)]
    directions=[]
    def boundary_gap(point):return min(point[0]-tl[0]-rx,br[0]-rx-point[0],point[1]-tl[1]-ry,br[1]-ry-point[1])
    for name,(dx,dy) in zip(MOVE_NAMES[1:],MOVES[1:]):
        length=math.hypot(dx,dy);unit=(dx/length,dy/length)
        clearance=0
        for step in range(1,41):
            point=(pos[0]+unit[0]*step*2,pos[1]+unit[1]*step*2)
            outside=boundary_gap(point)<0 and boundary_gap(point)<=boundary_gap(pos)
            if outside or any(o.blocks(pos,point) for o in direction_obstacles):break
            clearance=step*2
        directions.append({'move':name,'clearance_px':clearance,'blocked_within_12px':clearance<12})
    rows=[]
    for r in range(max(mask,default=-1)//max(1,width)+1):
        row=[]
        for c in range(width):
            symbol=mask.get(r*width+c,'?')
            if row and row[-1][0]==symbol:row[-1][1]+=1
            else:row.append([symbol,1])
        rows.append(row)
    origin=points.get(0,tl)
    nearby=sorted(obstacles,key=lambda o:math.dist(o.center,pos))[:12]
    return {'shape':room.get('shape'),'cell_px':40,'grid_width':width,'map_origin_rel':[round(origin[0]-pos[0],1),round(origin[1]-pos[1],1)],
        'mask_rows_rle':rows,'legend':'W=wall/void X=obstacle P=pit D=door S=spikes F=fire T=TNT O=poop .=floor ?=unknown',
        'mask_known':bool(mask) and all(len(c)>4 for c in cells),'player_radius':[rx,ry],
        'local_pitch_px':40,'local_occupancy_7x7':local,'directions':directions,
        'nearby_obstacles':[[o.kind,round(o.center[0]-pos[0],1),round(o.center[1]-pos[1],1),*o.extent] for o in nearby]}
