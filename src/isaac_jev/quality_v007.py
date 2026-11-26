"""Ordinal, evidence-masked retrospective feedback; never observable model facts."""
from collections import Counter, defaultdict
import math
import statistics
from .actions import label_from_input
from .quality_labels import enemies, threat_gap
from .terrain import xy
from .weapons import weapon_state


def kind_gap(frame, kind):
    copy = {**frame, 'entities': [e for e in frame.get('entities', []) if e.get('kind') == kind]}
    return threat_gap(copy)


def annotate(segment, i, lag, outcome, short, phase_end, contact_gaps, projectile_gaps):
    start = i+lag; end = min(len(segment)-1, start+60, phase_end[start])
    complete = end >= start+60 or phase_end[start] < len(segment) and end == phase_end[start]
    before, after = segment[start], segment[end]
    a, b = enemies(before), enemies(after); net = 0.; comparisons = 0
    for uid in a.keys() & b.keys():
        old,new=a[uid],b[uid]
        if all(old.get(k)==new.get(k) for k in ('type','variant','max_hp')):
            hp0,hp1=old.get('hp'),new.get('hp')
            if isinstance(hp0,(int,float)) and isinstance(hp1,(int,float)):
                net += hp0-hp1; comparisons += 1
    cleared = before['room'].get('clear') is False and after['room'].get('clear') is True
    progress = comparisons > 0 and net > 0
    weapon=weapon_state(before); vulnerable=any(e.get('vulnerable') is True for e in a.values())
    charge = weapon['trigger'] != 'continuous' and bool(weapon.get('holding'))
    waiting = bool(a) and all(e.get('vulnerable') is False for e in a.values())
    duration=max(1,end-start)/30
    origin=xy(before['player']['position']); displacement=max((math.dist(origin,xy(f['player']['position'])) for f in segment[start:end+1]),default=0)
    ids=[label_from_input(f.get('human_input',{}))[0] for f in segment[start:min(end,start+12)+1]]
    reversals=sum(ids[j]//5 != ids[j-1]//5 for j in range(1,len(ids)))
    grades=[-1]*7; confidence=[0.]*7
    if outcome['label'] in {'positive','negative'}:
        grades[0]=4 if outcome['label']=='positive' else 0 if after['player'].get('dead') else 1
        confidence[0]=1.
    for axis,gaps in ((1,projectile_gaps),(2,contact_gaps)):
        if gaps[start] < 24 and short['label']=='positive':
            later=min(end,start+12); minimum=min(gaps[start:later+1]); improvement=gaps[later]-gaps[start]
            grades[axis]=4 if improvement>=12 and minimum>=8 else 3 if improvement>=6 else 2 if minimum>=0 else 1
            confidence[axis]=.6 if complete else .3 # geometry association; no invulnerability/source proof
    if progress or cleared:
        grades[4]=4 if cleared else 3; confidence[4]=.5 # source of enemy damage is not captured
    elif complete and comparisons and vulnerable and not charge and not waiting:
        grades[4]=1;confidence[4]=.35
    if complete and displacement>=8 and (progress or cleared):
        grades[3]=3;confidence[3]=.35 # trajectory-level association, not causal single-action credit
    elif waiting or charge:
        grades[3]=2;confidence[3]=.5
    elif complete and displacement<6 and grades[4]==1:
        grades[3]=1;confidence[3]=.35
    gap=min(min(contact_gaps[start:min(end,start+12)+1]),min(projectile_gaps[start:min(end,start+12)+1]))
    if outcome['label']=='positive':
        grades[6]=1 if gap<4 or reversals>=3 else 2 if gap<12 else 4
        confidence[6]=.5
    move=1. if outcome['label']=='positive' else .2 if outcome['label']=='negative' else .3
    if outcome['label']=='negative' and (outcome.get('damage_after_action_frames') or 30)<=12 and gap<=0:move=0.
    for axis in (1,2,3):
        if grades[axis]>=3:move*=1+.25*(grades[axis]-2)*confidence[axis]
    if grades[6]==1:move*=.3
    shoot=1.35 if progress or cleared else .85 if charge or waiting else .45 if complete and grades[4]==1 else .6
    if outcome['label']=='negative' and not progress:shoot*=.65
    return {'grades':grades,'confidence':confidence,'move_weight':move,'shoot_weight':shoot,
            'complete':complete,'net_enemy_hp_drop':round(net,3) if comparisons else None,
            'observed_dps':max(0,net)/duration if comparisons and complete else None,
            'efficiency_group':[weapon['type'],round(float(before['player'].get('stats',{}).get('damage') or 0)*2)/2,before['room'].get('type',1)],
            'observed_charge_or_wait':charge or waiting,'displacement_px':round(displacement,2),
            'minimum_gap_estimate':round(gap,2),'rapid_move_changes':reversals,'cleared':cleared,'causal_claim':False}


def fit_weights(rows):
    groups=defaultdict(list)
    for row in rows:
        q=row['quality']
        if row['split']=='train' and q['observed_dps'] is not None:
            groups[tuple(q['efficiency_group'])].append(q['observed_dps'])
    baselines={key:sorted(values) for key,values in groups.items() if len(values)>=30}
    runs=Counter(r['run_id'] for r in rows if r['split']=='train');median=statistics.median(runs.values())
    chunks=Counter((r['episode_id'],r['action_frame']//30,r['action_label']) for r in rows if r['split']=='train')
    for row in rows:
        q=row['quality']; reference=baselines.get(tuple(q['efficiency_group']))
        if q['observed_dps'] is not None and reference:
            import bisect
            percentile=(bisect.bisect_left(reference,q['observed_dps'])+bisect.bisect_right(reference,q['observed_dps']))/(2*len(reference))
            q['grades'][5]=min(4,int(percentile*5));q['confidence'][5]=.35
            q['shoot_weight']*=.8+.4*percentile
        balance=1.
        if row['split']=='train':
            balance=max(.7,min(1.5,math.sqrt(median/runs[row['run_id']])))
            balance*=min(1.,4/chunks[(row['episode_id'],row['action_frame']//30,row['action_label'])])
        q['move_weight']=round(q['move_weight']*balance,6);q['shoot_weight']=round(q['shoot_weight']*balance,6)
    return {'fitted_on':'training_only','minimum_comparable_group':30,
            'dps_reference':{json_key(key):{'samples':len(values),'quantiles':[values[int((len(values)-1)*p)] for p in (0,.2,.4,.6,.8,1)]} for key,values in baselines.items()},
            'scope':'Observed net HP change, not player-attributed DPS; ordinal grades are associations, masks preserve unknowns.'}


def json_key(key):
    return '/'.join(str(x) for x in key)


def refine_feedback(rows):
    """Protect unobserved charge waits and lower imitation of evidenced idle/no-progress."""
    import json
    counts=Counter()
    for row in rows:
        quality=row['quality']
        if quality.get('refinement_version'):continue
        state=json.loads(row['prompt'].split('\nCurrent semantic state:\n',1)[1])
        weapon=state.get('weapon',{})
        possible_charge=weapon.get('trigger')!='continuous' and weapon.get('holding') is None
        if possible_charge and quality['grades'][4]==1:
            quality['grades'][4]=-1;quality['confidence'][4]=0.
            quality['grades'][5]=-1;quality['confidence'][5]=0.
            if quality['grades'][3]==1:quality['grades'][3]=2;quality['confidence'][3]=.3
            quality['shoot_weight']=round(quality['shoot_weight']*(.85/.45),6)
            quality['refinement_reason']='unobserved_charge_wait_possible';counts['charge_wait_protected']+=1
        elif quality['grades'][3]==1 and quality['grades'][4]==1:
            quality['move_weight']=round(quality['move_weight']*.5,6)
            quality['refinement_reason']='complete_stationary_no_progress';counts['idle_move_downweighted']+=1
        quality['refinement_version']='charge-unknown-idle-progress-v7-1'
    return dict(counts)
