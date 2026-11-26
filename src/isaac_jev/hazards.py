"""Known hazard geometry; unknown effect IDs are retained without fabricated damage."""
import math
from .terrain import Obstacle, xy, player_radius

HAZARD_VERSION='observed-area-hazards-v1'
CREEP={22:'red_creep',23:'green_creep',24:'yellow_creep',25:'slowing_white_creep',26:'slowing_black_creep',56:'brown_creep',94:'slippery_creep',155:'growing_slippery_creep'}
PLAYER_CREEP={32,37,44,45,46,53,54,78,90,92,204}

def area_hazards(frame):
    p=frame['player'];pos=xy(p['position']);flying=bool(p.get('can_fly'));result=[]
    owned={e.get('id') for e in p.get('inventory',{}).get('collectibles',[])}
    for e in frame.get('entities',[]):
        if e.get('dead'):continue
        kind=e.get('kind');variant=e.get('variant');owner=e.get('owner_type')
        row=None
        if kind=='effect' and variant in CREEP:
            radius=e.get('effect_radius')
            measured=isinstance(radius,(int,float)) and radius>0
            radius=float(radius) if measured else max(24,float(e.get('size') or 10)*max(1,(e.get('size_multi') or {}).get('x',1)))
            row={'kind':CREEP[variant],'shape':'circle','radius':round(radius,1),'extent_source':'observed' if measured else 'conservative_estimate',
                 'harmful':not flying and owner not in {1,3},'ground_only':True}
        elif kind=='laser' and owner not in {1,3} and not e.get('friendly'):
            endpoint=e.get('laser_endpoint')
            if endpoint:row={'kind':'hostile_laser','shape':'segment','endpoint':endpoint,'radius':float(e.get('laser_radius') or e.get('size') or 8),
                             'harmful':True,'extent_source':'observed'}
        elif kind=='bomb':
            row={'kind':'bomb','shape':'circle','radius':float(e.get('bomb_radius') or 80*float(e.get('bomb_radius_multiplier') or 1)),
                 'harmful':not bool(owned & {223,375}),'extent_source':'estimated_explosion_radius','countdown':e.get('bomb_countdown')}
        if row:
            row.update(position=e['position'],velocity=e.get('velocity',{}),timeout=e.get('effect_timeout',e.get('laser_timeout')),
                       owner='player' if owner==1 else 'familiar' if owner==3 else 'enemy_or_unknown',variant=variant)
            row['distance']=round(math.dist(pos,xy(e['position'])),1);result.append(row)
    for g in frame['room'].get('grid',[]):
        if g.get('kind') in {'spikes','fire'} and g.get('state') not in {2,1000}:
            result.append({'kind':g['kind'],'shape':'box','position':g['position'],'half_size':[20,20],
                           'harmful':not flying if g['kind']=='spikes' else True,'ground_only':g['kind']=='spikes',
                           'extent_source':'grid','distance':round(math.dist(pos,xy(g['position'])),1),'static':True})
    return sorted(result,key=lambda h:h['distance'])

def hazard_obstacles(frame):
    rx,ry=player_radius(frame);out=[]
    for h in area_hazards(frame):
        if not h['harmful']:continue
        if h['shape']=='circle':out.append(Obstacle(xy(h['position']),(h['radius']+max(rx,ry),)*2,h['kind'],'circle'))
        elif h['shape']=='box':out.append(Obstacle(xy(h['position']),(h['half_size'][0]+rx,h['half_size'][1]+ry),h['kind']))
        elif h['shape']=='segment':
            a,b=xy(h['position']),xy(h['endpoint']);length=math.dist(a,b);steps=max(1,int(length/max(8,h['radius'])))
            out.extend(Obstacle((a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps),(h['radius']+max(rx,ry),)*2,h['kind'],'circle') for i in range(steps+1))
    return out

def imminent_hazard(frame):
    pos=xy(frame['player']['position']);velocity=xy(frame['player'].get('velocity',{}))
    end=(pos[0]+velocity[0]*6,pos[1]+velocity[1]*6)
    return any(o.gap(pos)<=10 or o.blocks(pos,end) for o in hazard_obstacles(frame) if o.kind not in {'spikes','fire'})
