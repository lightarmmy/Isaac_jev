"""Past-only execution feedback and bounded attack-route facts for the feedback policy."""
from functools import lru_cache
import hashlib
import heapq
import json
import math
from .actions import MOVES, SHOTS, MOVE_NAMES, SHOT_NAMES
from .features_v007 import facts as previous_facts, semantic_history_summary, geometry, finite, valid_vector
from .features_v005 import compact_facts
from .outcomes import health_loss
from .terrain import Obstacle, player_radius, static_obstacles, xy
from .navigation import fire_obstacles
from .weapons import weapon_state

PREFIX='combat-feedback-v9-'
RULES=('Isaac: actively establish attack access and eliminate enemies promptly while managing immediate danger. '
       'Passive survival without combat progress is not the goal. Read weapon capabilities and verified item effects. '
       'Compare each movement consequence: escape margin, attack geometry and path distance to a clear-line position. '
       'A route is a current static-geometry estimate, not a safe trajectory or instruction. '
       'Use recent observed feedback to reassess ineffective attacks and unproductive positions. '
       'Observed manual keys, AI callback-applied input, geometric opportunities and enemy HP changes are different facts. '
       'Terrain on a centerline depends on penetration; raw TearRange and alignment are proxies for complex weapons. '
       'Holding an effective firing position is useful; otherwise approach a reachable clear-line position. '
       'Waiting needs a mechanism such as charging, release timing or invulnerable targets, not just survival. '
       'Avoid imminent threats and retain escape space. All 45 controls remain available. '
       'Coordinates: right/down positive, pixels, 30Hz sampled frames. Unknown remains null. '
       'Candidate endpoint uses current velocity for the four-frame alignment then three frames of instantaneous steering; '
       'it does not simulate native inertia. Choose movement and shooting together.')
RULES_HASH=hashlib.sha256(RULES.encode()).hexdigest()


def contract(base,profile):
    from .features_v007 import contract as previous_contract
    if profile!=PREFIX+'s01':raise ValueError('Unknown feedback profile')
    result=previous_contract(base,'combat-semantic-v7-s01')
    result.update(feature_version=profile,rules_sha256=RULES_HASH,history_version='observed-feedback-60f-v1',
                  text_serialization='candidate-consequences-feedback-v1',feedback_version='applied-observed-estimated-separated-v1',
                  terrain_version='bounded-clear-line-route-v1',feedback_history_frames=60)
    return result


def compatible(a,b):
    return a['run_id']==b['run_id'] and a['room']['id']==b['room']['id'] and a.get('mode')==b.get('mode')


def hp_changes(before,after):
    def entities(frame):
        return {e.get('uid'):e for e in frame.get('entities',[]) if e.get('kind')=='enemy' and not e.get('friendly') and not e.get('dead')}
    a,b=entities(before),entities(after);changes=[]
    for uid in a.keys()&b.keys():
        x,y=a[uid],b[uid]
        if all(x.get(k)==y.get(k) for k in ('type','variant','max_hp')) and finite(x.get('hp')) is not None and finite(y.get('hp')) is not None:
            changes.append(x['hp']-y['hp'])
    stable=bool(a) and set(a)==set(b) and len(changes)==len(a)
    return changes,stable


def feedback_summary(frame,history):
    n=frame['game_frame'];past={f['game_frame']:f for f in history if compatible(f,frame) and n-60<=f['game_frame']<n}
    earliest=n
    while earliest-1 in past:earliest-=1
    frames=[past[i] for i in range(earliest,n)]+[frame]
    window=frames[-31:];full=len(window)==31
    travel=sum(math.dist(xy(a['player']['position']),xy(b['player']['position'])) for a,b in zip(window,window[1:]))
    losses=[health_loss(a,b) for a,b in zip(window,window[1:])]
    changes,stable=hp_changes(window[0],frame) if len(window)>1 else ([],False)
    requested=[];move_requested=[];applied={}
    # Current pre-update controls describe a prior frame; current human keys are not past execution.
    for f in frames[-31:]:
        if f['game_frame']<n and f.get('mode')=='manual':
            inputs=f.get('human_input',{});s=inputs.get('shoot');m=inputs.get('move')
            if valid_vector(s):requested.append(any(abs(v)>.25 for v in xy(s)))
            if valid_vector(m):move_requested.append(any(abs(v)>.25 for v in xy(m)))
        controls=f.get('applied_controls') or {};tick=controls.get('game_frame')
        if f.get('mode')!='ai' or type(tick) is not int or not n-30<=tick<n or tick>f['game_frame']:continue
        if controls.get('run_id')!=frame['run_id'] or controls.get('room_id')!=frame['room']['id']:continue
        shots=[];moves=[]
        for request in controls.get('requests',[]):
            pressed=request.get('pressed',{});queried=request.get('queried',{})
            if any(queried.get(str(i)) for i in range(4,8)):shots.append(any(pressed.get(str(i),False) for i in range(4,8)))
            if any(queried.get(str(i)) for i in range(4)):moves.append(any(pressed.get(str(i),False) for i in range(4)))
        if shots:applied[tick]=any(shots)
        if moves:move_requested.append(any(moves))
    no_progress=0
    for a,b in reversed(list(zip(frames,frames[1:]))):
        delta,known=hp_changes(a,b)
        if not known or sum(delta)>0 or not any(e.get('vulnerable') is True for e in b.get('entities',[]) if e.get('kind')=='enemy'):break
        no_progress+=1
    weapon=weapon_state(frame);old_weapon=weapon_state(window[0]);charge_delta=None
    if full and weapon['type']==old_weapon['type'] and weapon['charge_known'] and old_weapon['charge_known']:
        charge_delta=weapon['charge_ratio']-old_weapon['charge_ratio']
    clear=bool(len(window)>1 and window[0]['room'].get('clear') is False and frame['room'].get('clear') is True)
    return {'available_past_frames':len(frames)-1,'window_frames':30,'full_window':full,
            'travel_px':round(travel,1) if full else None,
            'displacement_px':round(math.dist(xy(window[0]['player']['position']),xy(frame['player']['position'])),1) if full else None,
            'manual_shoot_observed_fraction':round(sum(requested)/len(requested),3) if requested else None,
            'shoot_observed_fraction':round(sum(requested)/len(requested),3) if requested else round(sum(applied.values())/len(applied),3) if applied else None,
            'ai_shoot_applied_frames':len(applied),'ai_shoot_applied_fraction':round(sum(applied.values())/len(applied),3) if applied else None,
            'movement_observed_fraction':round(sum(move_requested)/len(move_requested),3) if move_requested else None,
            'observed_hp_loss':sum(losses) if full and all(v is not None for v in losses) else None,
            'comparable_enemy_hp_change':round(sum(changes),2) if full and stable else None,
            'clear_transition_observed':clear,'no_net_hp_progress_frames':no_progress,
            'charge_ratio_change':round(charge_delta,3) if charge_delta is not None else None,
            'damage_attribution':'unknown','input_sources':'manual keys observed; AI only explicit prior callback queries'}


def history_summary(frame,history):
    return {'legacy':semantic_history_summary(frame,history),'feedback':feedback_summary(frame,history)}


@lru_cache(maxsize=12)
def box_index(boxes):
    obstacles=[Obstacle(center,extent,kind,shape) for center,extent,kind,shape in boxes];bins={}
    for i,o in enumerate(obstacles):
        for x in range(math.floor((o.center[0]-o.extent[0])/80),math.floor((o.center[0]+o.extent[0])/80)+1):
            for y in range(math.floor((o.center[1]-o.extent[1])/80),math.floor((o.center[1]+o.extent[1])/80)+1):
                bins.setdefault((x,y),[]).append(i)
    return obstacles,bins


def nearby_boxes(a,b,obstacles,bins):
    indices=set()
    for x in range(math.floor(min(a[0],b[0])/80),math.floor(max(a[0],b[0])/80)+1):
        for y in range(math.floor(min(a[1],b[1])/80),math.floor(max(a[1],b[1])/80)+1):indices.update(bins.get((x,y),()))
    return [obstacles[i] for i in indices]


@lru_cache(maxsize=12)
def route_graph(cells,width,boxes):
    obstacles,bins=box_index(boxes)
    nodes={int(c[0]):(c[1],c[2]) for c in cells if len(c)>4 and c[4] and all(o.gap((c[1],c[2]))>0 for o in nearby_boxes((c[1],c[2]),(c[1],c[2]),obstacles,bins))}
    graph={i:[] for i in nodes}
    for i,pos in nodes.items():
        row,col=divmod(i,width)
        for r,c in ((row-1,col),(row+1,col),(row,col-1),(row,col+1)):
            j=r*width+c
            if r<0 or not 0<=c<width or j not in nodes:continue
            if not any(o.blocks(pos,nodes[j]) for o in nearby_boxes(pos,nodes[j],obstacles,bins)):graph[i].append(j)
    return nodes,graph,obstacles,bins


def distances(graph,seeds):
    costs=dict(seeds);parents={i:None for i in seeds};queue=[(v,i) for i,v in seeds.items()];heapq.heapify(queue)
    while queue:
        cost,i=heapq.heappop(queue)
        if cost!=costs[i]:continue
        for j in graph[i]:
            value=cost+40
            if value<costs.get(j,float('inf')):costs[j]=value;parents[j]=i;heapq.heappush(queue,(value,j))
    return costs,parents


def attack_routes(frame,target_labels,preferred_goals=None,allow_untargetable=False,node_limit=96):
    room=frame['room'];cells=room.get('navigation_cells') or [];width=room.get('grid_width')
    if not cells or type(width) is not int or width<=0 or any(len(c)<5 for c in cells):return [],[None]*9
    obstacles=static_obstacles(frame,avoid_interactions=True)+fire_obstacles(frame)
    signature=tuple((o.center,o.extent,o.kind,o.shape) for o in obstacles)
    rx,ry=player_radius(frame);tl=xy(room['top_left']);br=xy(room['bottom_right'])
    bounded=tuple(tuple(c) for c in cells if tl[0]+rx<=c[1]<=br[0]-rx and tl[1]+ry<=c[2]<=br[1]-ry)
    nodes,graph,obstacles,bins=route_graph(bounded,width,signature)
    origin=xy(frame['player']['position'])
    def anchors(point):
        nearest=sorted(nodes,key=lambda i:math.dist(point,nodes[i]))[:4]
        return {i:math.dist(point,nodes[i]) for i in nearest if math.dist(point,nodes[i])<=80 and not any(o.blocks(point,nodes[i]) for o in nearby_boxes(point,nodes[i],obstacles,bins))}
    seeds=anchors(origin)
    if not seeds:return [],[None]*9
    costs,parents=distances(graph,seeds);raw=geometry(frame);routes=[];goals=[]
    ray_boxes,ray_bins=box_index(tuple((o.center,o.extent,o.kind,o.shape) for o,_ in raw if o.kind not in {'pit','door'}))
    enemies={e.get('uid'):e for e in frame.get('entities',[]) if e.get('kind')=='enemy' and not e.get('friendly') and not e.get('dead')}
    reach=finite(frame['player'].get('stats',{}).get('tear_range'))
    for uid,label in target_labels:
        e=enemies.get(uid)
        if not e or (e.get('vulnerable') is False and not allow_untargetable):continue
        target=xy(e['position']);best=None
        preferred=(preferred_goals or {}).get(uid)
        preferred_node=next((i for i,pos in nodes.items() if preferred and math.dist(pos,preferred)<.1),None)
        ordered=sorted(costs,key=lambda i:costs[i])[:node_limit]
        if preferred_node in costs:ordered=[preferred_node]+[i for i in ordered if i!=preferred_node]
        for i in ordered:
            pos=nodes[i];dx,dy=target[0]-pos[0],target[1]-pos[1];distance=math.hypot(dx,dy)
            if distance<max(player_radius(frame))+finite(e.get('size'),10)+(8 if i==preferred_node else 20):continue
            if reach is not None and distance>reach:continue
            if min(abs(dx),abs(dy))>finite(e.get('size'),10)+8:continue
            if any(o.entry(pos,target) is not None and o.gap(pos)>0 and o.gap(target)>0 for o in nearby_boxes(pos,target,ray_boxes,ray_bins)):continue
            best=i;break
        if best is None:continue
        chain=[best]
        while parents[chain[-1]] is not None:chain.append(parents[chain[-1]])
        chain.reverse();first=next((i for i in chain if math.dist(origin,nodes[i])>8),best)
        goals.append(best);routes.append([label,round(nodes[best][0]-origin[0],1),round(nodes[best][1]-origin[1],1),round(costs[best],1),
                                         round(nodes[first][0]-origin[0],1),round(nodes[first][1]-origin[1],1)])
        if len(routes)==3:break
    if not goals:return [],[None]*9
    reverse,_=distances(graph,{i:0 for i in goals});pv=xy(frame['player'].get('velocity',{}));speed=max(1.,4*finite(frame['player'].get('stats',{}).get('move_speed'),1))
    move_costs=[]
    for move in MOVES:
        scale=math.hypot(*move) or 1
        endpoint=tuple(origin[j]+pv[j]*4+move[j]*speed*3/scale for j in range(2))
        options=[distance+reverse[i] for i,distance in anchors(endpoint).items() if i in reverse]
        move_costs.append(round(min(options),1) if options else None)
    return routes,move_costs


def relation_status(row,geometry_known):
    direction,aligned,lateral,margin,intersections=row
    if lateral is None:return 'no_forward_target'
    if lateral>8:return 'off_axis'
    if margin is not None and margin<0:return 'raw_out_of_range'
    if not geometry_known:return 'geometry_unknown'
    if intersections:return 'terrain_on_line'
    return 'aligned_proxy' if aligned else 'off_axis'


def observation(frame,history=(),condition='safe_progress',history_facts=None,profile=PREFIX+'s01'):
    if profile!=PREFIX+'s01' or condition!='safe_progress':raise ValueError('Feedback policy contract mismatch')
    summaries=history_summary(frame,history) if history_facts is None else history_facts
    if not isinstance(summaries,dict) or not {'legacy','feedback'}<=summaries.keys():raise ValueError('Bridge needs feedback history support')
    state,values,names=previous_facts(frame,summaries['legacy']);feedback=dict(summaries['feedback'])
    targets=sorted([e for e in frame.get('entities',[]) if e.get('kind')=='enemy' and not e.get('friendly') and not e.get('dead') and valid_vector(e.get('position'))],
                   key=lambda e:(math.dist(xy(frame['player']['position']),xy(e['position'])),str(e.get('uid'))))[:4]
    labels=list(zip([e.get('uid') for e in targets],[r[0] for r in state['targets']['rows']]))
    routes,move_costs=attack_routes(frame,labels)
    consequences=[]
    for row,cost in zip(state.pop('moves')['rows'],move_costs):
        consequences.append([*row[:6],[[r[0],relation_status(r,state['geometry_known'])] for r in row[6][1:]],cost])
    direction=weapon_state(frame).get('shoot_direction');shoot=None
    if valid_vector(direction):
        x,y=xy(direction);sign=lambda v:1 if v>.25 else -1 if v<-.25 else 0
        vector=(sign(x),0) if abs(x)>=abs(y) else (0,sign(y))
        if vector in SHOTS and vector!=(0,0):shoot=SHOT_NAMES[SHOTS.index(vector)]
    feedback['current_attack_geometry']=next((status for d,status in consequences[0][6] if d==shoot),'direction_unobserved')
    state.pop('history');state.pop('recent_progress');state.pop('last_attack_failure_reason');state.pop('limits')
    state['feedback']=feedback
    state['candidate_consequences']={'columns':['move','walk_margin','contact_gap','bullet_gap','ground_hazard','fire_on_path','shoot_geometry','clear_line_path_cost'],
                                     'rows':consequences}
    state['attack_routes']={'columns':['target','position_dx','position_dy','path_px','first_leg_dx','first_leg_dy'],'rows':routes,
                            'scope':'current static geometry; raw range/alignment proxy; dynamic enemies/projectiles excluded from route cost'}
    # Shorten repeated threat descriptions; retain omission count and all numerical candidate facts.
    state['threats_omitted']+=max(0,len(state['immediate_threats'])-6);state['immediate_threats']=state['immediate_threats'][:6]
    def add(name,value):names.append(name);values.append(float(value))
    add('feedback_history_frames',min(60,feedback['available_past_frames'])/60)
    add('feedback_full_window',feedback['full_window'])
    for name,value,scale in [('travel',feedback['travel_px'],320),('observed_shoot',feedback['shoot_observed_fraction'],1),
                             ('applied_shoot',feedback['ai_shoot_applied_fraction'],1),('hp_loss',feedback['observed_hp_loss'],6),
                             ('enemy_hp',feedback['comparable_enemy_hp_change'],50),('charge_change',feedback['charge_ratio_change'],1)]:
        add('feedback_'+name+'_known',value is not None);add('feedback_'+name,0 if value is None else max(-4,min(4,value/scale)))
    add('feedback_no_progress_frames',min(60,feedback['no_net_hp_progress_frames'])/60)
    add('feedback_route_known',bool(routes));add('feedback_route_cost',min((r[3]/800 for r in routes),default=0))
    for i,cost in enumerate(move_costs):add(f'feedback_move_{i}_route_known',cost is not None);add(f'feedback_move_{i}_route_cost',0 if cost is None else min(4,cost/800))
    text=RULES+'\nCurrent feedback state:\n'+json.dumps(compact_facts(state),ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return text,values,names
