"""V005 contextual combat features; legacy heads remain on their original contract."""
import hashlib
import json
import math
from pathlib import Path
from .terrain import combat_terrain, xy
from .hazards import area_hazards, HAZARD_VERSION
from .weapons import weapon_state

VERSION='combat-build-terrain-weapons-v5'
RULES=(Path(__file__).parent/'rules/combat_v005.txt').read_text(encoding='utf-8')
RULES_HASH=hashlib.sha256(RULES.encode()).hexdigest()
KNOWLEDGE_PATH=Path(__file__).parent/'rules/item_combat_v005.json'
KNOWLEDGE=json.loads(KNOWLEDGE_PATH.read_text(encoding='utf-8')) if KNOWLEDGE_PATH.exists() else {}
KNOWLEDGE_HASH=hashlib.sha256(json.dumps(KNOWLEDGE,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
TEXT_SERIALIZATION='column-tables-round2-v1'

def contract(base):
    return {**base,'feature_version':VERSION,'rules_sha256':RULES_HASH,'knowledge_sha256':KNOWLEDGE_HASH,
            'terrain_version':'native-mask-relative-v1','weapon_version':'observed-charge-v1','hazard_version':HAZARD_VERSION,
            'text_serialization':TEXT_SERIALIZATION,
            'control_context':False,'projectile_limit':16,'training_phase':'enemy-projectile-imminent-hazard-v2'}

def compact_facts(facts):
    """Keep all fields while sharing record keys and bounding text float precision."""
    def pack(value):
        if isinstance(value,float):
            result=round(value,2)
            return int(result) if result.is_integer() else result
        if isinstance(value,dict):return {key:pack(item) for key,item in value.items()}
        if isinstance(value,list):
            rows=[pack(item) for item in value]
            if len(rows)>1 and all(isinstance(row,dict) for row in rows):
                fields=list(rows[0])
                if all(set(row)==set(fields) for row in rows):
                    return {'fields':fields,'rows':[[row[key] for key in fields] for row in rows]}
            return rows
        return value
    result=pack(facts)
    terrain=result.get('terrain',{})
    if 'mask_rows_rle' in terrain:
        terrain['mask_rows']=[''.join(symbol*count for symbol,count in row) for row in terrain.pop('mask_rows_rle')]
    occupancy=terrain.get('local_occupancy_7x7',[])
    if occupancy and isinstance(occupancy[0],list):
        terrain['local_occupancy_7x7']=[''.join(str(cell) for cell in row) for row in occupancy]
    return result

def compact_prompt(prompt):
    before,state=prompt.split('\nCurrent state:\n',1)
    state,after=state.split('\nDesired outcome condition:',1)
    return RULES+'\nCurrent state:\n'+json.dumps(compact_facts(json.loads(state)),ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\nDesired outcome condition:'+after

def observation(frame,history,condition,history_facts=None):
    from .features import policy_observation
    prompt,values,names=policy_observation(frame,history,condition,history_facts,False,False)
    facts=json.loads(prompt.split('\nCurrent state:\n',1)[1].split('\nDesired outcome condition:',1)[0])
    p=frame['player'];px,py=xy(p['position']);terrain=combat_terrain(frame);weapon=weapon_state(frame)
    def add(name,value,scale=1):
        known=isinstance(value,(int,float)) and math.isfinite(value)
        names.append(name);values.append(max(-4,min(4,value/scale)) if known else 0)
    equipment=[];missing=[]
    critical={69,118,114,395,229,329,52,168,223,375,108,317}
    for item in sorted(p.get('inventory',{}).get('collectibles',[]),key=lambda i:(0 if i['id'] in critical else 1 if i.get('item_type')==4 else 2,i['id'])):
        entry=KNOWLEDGE.get(str(item['id']))
        if entry:equipment.append([item['id'],entry.get('name',item.get('name')),entry['summary']])
        else:missing.append([item['id'],item.get('name','unknown')])
    facts['build']={'items':equipment[:20],'other_items':missing[:24],'items_omitted':max(0,len(equipment)-20)+max(0,len(missing)-24),
                    'knowledge_missing':bool(missing),'weapon':weapon}
    facts['player']['stats']={**p.get('stats',{}),**frame.get('planner_stats',{})}
    for key,scale in [('charge_ratio',1),('charge_known',1),('holding',1),('held_frames',90),('fire_delay',30)]:add('weapon_'+key,weapon.get(key),scale)
    for kind in range(1,16):add('weapon_type_'+str(kind),int(weapon['type']==kind))
    for key,scale in [('tear_range',400),('luck',10)]:
        value=p.get('stats',{}).get(key,frame.get('planner_stats',{}).get(key));add('player_'+key,value,scale);add('player_'+key+'_known',value is not None)
    facts['terrain']=terrain
    facts.pop('move_estimates',None)
    for y,row in enumerate(terrain['local_occupancy_7x7']):
        for x,value in enumerate(row):add(f'terrain_{y}_{x}',value)
    for row in terrain['directions']:add('terrain_'+row['move']+'_clearance',row['clearance_px'],80)
    familiars=[e for e in frame.get('entities',[]) if e.get('kind')=='familiar' or e.get('type')==3]
    familiars.sort(key=lambda e:math.dist((px,py),xy(e['position'])))
    facts['familiars']={'count':len(familiars),'fields':['variant','dx','dy','vx','vy','state','charge_ratio'],
        'nearest':[[e.get('variant'),round(xy(e['position'])[0]-px,1),round(xy(e['position'])[1]-py,1),*xy(e.get('velocity',{})),
                    e.get('state'),(e.get('familiar_weapon') or {}).get('charge_ratio')] for e in familiars[:4]]}
    for i in range(4):
        e=familiars[i] if i<len(familiars) else {};pos=xy(e.get('position',{}));vel=xy(e.get('velocity',{}))
        for key,value,scale in [('present',bool(e),1),('dx',pos[0]-px if e else 0,320),('dy',pos[1]-py if e else 0,280),('vx',vel[0],10),('vy',vel[1],10)]:add(f'familiar{i}_{key}',value,scale)
    hazards=area_hazards(frame);facts['hazards']={'fields':['kind','dx','dy','radius','harmful','timeout','endpoint_rel','extent_source'],
        'nearest':[[h['kind'],round(xy(h['position'])[0]-px,1),round(xy(h['position'])[1]-py,1),h.get('radius'),h['harmful'],h.get('timeout',h.get('countdown')),
            [round(xy(h['endpoint'])[0]-px,1),round(xy(h['endpoint'])[1]-py,1)] if h.get('endpoint') else None,h['extent_source']] for h in hazards[:8]],
        'omitted':max(0,len(hazards)-8),'unknown_effects':sum(e.get('kind')=='effect' and e.get('variant') not in {h.get('variant') for h in hazards} for e in frame.get('entities',[]))}
    from .hazards import hazard_obstacles
    obstacles=hazard_obstacles(frame)
    from .actions import MOVES, MOVE_NAMES
    facts['hazard_directions']=[]
    for name,(dx,dy) in zip(MOVE_NAMES,MOVES):
        length=math.hypot(dx,dy) or 1;end=(px+dx/length*40,py+dy/length*40)
        hit=any(o.blocks((px,py),end) for o in obstacles)
        add('hazard_'+name+'_blocked',hit);facts['hazard_directions'].append([name,hit])
    for i in range(8):
        h=hazards[i] if i<len(hazards) else {};pos=xy(h.get('position',{}))
        for key,value,scale in [('present',bool(h),1),('dx',pos[0]-px if h else 0,320),('dy',pos[1]-py if h else 0,280),('radius',h.get('radius'),100),('harmful',h.get('harmful'),1)]:add(f'hazard{i}_{key}',value,scale)
    bullets=[e for e in frame.get('entities',[]) if e.get('kind')=='projectile' and not e.get('friendly') and not e.get('dead')]
    def risk(e):
        x,y=xy(e['position']);vx,vy=xy(e.get('velocity',{}));pvx,pvy=xy(p.get('velocity',{}));rx,ry=x-px,y-py;vx-=pvx;vy-=pvy
        t=max(0,min(10,-(rx*vx+ry*vy)/max(1e-6,vx*vx+vy*vy)));return math.hypot(rx+vx*t,ry+vy*t)-float(e.get('size') or 10)
    bullets.sort(key=risk)
    facts['projectiles']={'fields':['dx','dy','vx','vy','size','height'],
        'nearest_threats':[[round(xy(e['position'])[0]-px,1),round(xy(e['position'])[1]-py,1),*xy(e.get('velocity',{})),e.get('size'),e.get('height')] for e in bullets[:16]]}
    facts['projectiles_omitted']=max(0,len(bullets)-16)
    numeric_index={name:i for i,name in enumerate(names)}
    for i in range(16):
        e=bullets[i] if i<len(bullets) else {};pos=xy(e.get('position',{}));vel=xy(e.get('velocity',{}))
        for key,value,scale in [('present',bool(e),1),('dx',pos[0]-px if e else 0,320),('dy',pos[1]-py if e else 0,280),('vx',vel[0],10),('vy',vel[1],10),('size',e.get('size'),20),('height',e.get('height'),40),('falling_speed',e.get('falling_speed'),10),('falling_accel',e.get('falling_accel'),5)]:
            name=f'projectile{i}_{key}'
            if name in numeric_index:values[numeric_index[name]]=max(-4,min(4,(value or 0)/scale))
            else:add(name,value,scale)
    if history_facts is None:
        past={f['game_frame']:f for f in history if f['room']['id']==frame['room']['id'] and f['run_id']==frame['run_id']}
        for h in facts['history']:
            old=past.get(frame['game_frame']-h['age_frames'])
            if h.get('available') and old:h['weapon_charge_ratio']=weapon_state(old)['charge_ratio']
    else:
        source={h['age_frames']:h for h in history_facts}
        for h in facts['history']:
            if 'weapon_charge_ratio' in source.get(h['age_frames'],{}):h['weapon_charge_ratio']=source[h['age_frames']]['weapon_charge_ratio']
    text=RULES+'\nCurrent state:\n'+json.dumps(compact_facts(facts),ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\nDesired outcome condition: '+condition+'.\nChoose combat controls.'
    return text,values,names
