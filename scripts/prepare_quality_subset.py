"""Audit complete, damage-free room visits and select a small training-only subset."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.outcomes import health_loss
from isaac_jev.recording import iter_messages


def audit_rooms(messages, rooms):
    previous=None; visits=[]; active=None
    for message in messages:
        if message.get('type')=='event':
            room_id=message.get('room_id')
            if room_id is None and previous and previous['run_id']==message.get('run_id') and 0<=message.get('game_frame',-100)-previous['game_frame']<=1:
                room_id=previous['room']['id']
            key=(message.get('run_id'),room_id)
            if key in rooms and message.get('event')=='damage' and message.get('entity_type')==1 and (message.get('amount') or 0)>0:
                rooms[key]['damage_events']+=1
            continue
        if message.get('type')!='frame':continue
        key=(message['run_id'],message['room']['id'])
        adjacent=bool(previous and previous['run_id']==message['run_id'] and
                      previous.get('_recording_session')==message.get('_recording_session') and
                      message['game_frame']==previous['game_frame']+1)
        same=adjacent and previous['room']['id']==message['room']['id']
        eligible=message.get('mode')=='manual' and message.get('capture_phase')=='pre_update'
        if active is not None and (not same or not eligible):
            visits.append(active);active=None
        if key in rooms:
            room=rooms[key];room['observed_frames']+=1
            if not eligible and message['room'].get('clear') is False:room['non_manual_combat']+=1
            if message['player'].get('dead'):room['death_frames']+=1
            if same:
                loss=health_loss(previous,message)
                if loss is None:room['unknown_health_transitions']+=1
                elif loss>0:room['health_losses']+=1
            if eligible and message['room'].get('clear') is False and active is None:
                recording_file=message.get('_recording_session')
                session_name=Path(recording_file).parent.name if recording_file else None
                active={'key':key,'start_frame':message['game_frame'],'session':session_name,
                        'recording_file':recording_file,
                        'entry_observed':bool(adjacent and previous['room']['id']!=message['room']['id']),
                        'cleared':False,'end_frame':message['game_frame']}
            if active is not None:
                active['end_frame']=message['game_frame']
                if message['room'].get('clear') is True:
                    active['cleared']=True;visits.append(active);active=None
        previous=message
    if active is not None:visits.append(active)
    for visit in visits:
        if visit['key'] in rooms:rooms[visit['key']]['visits'].append({k:v for k,v in visit.items() if k!='key'})


def new_room():
    return {'observed_frames':0,'damage_events':0,'health_losses':0,'unknown_health_transitions':0,
            'non_manual_combat':0,'death_frames':0,'visits':[]}


def accepted_room(room):
    reasons=[field for field in ('damage_events','health_losses','unknown_health_transitions','non_manual_combat','death_frames') if room[field]]
    if not any(v['entry_observed'] and v['cleared'] for v in room['visits']):reasons.append('no_continuous_entry_to_clear')
    return not reasons,reasons


def run(args):
    output=args.output;output.mkdir(parents=True,exist_ok=True)
    if (output/'dataset.json').exists():raise RuntimeError('Subset already prepared; inspect existing outputs')
    dataset=args.dataset;meta=json.loads((dataset/'dataset.json').read_text())
    source_path=dataset/'samples.jsonl'
    assert hashlib.sha256(source_path.read_bytes()).hexdigest()==meta['samples_sha256']
    rows=[json.loads(line) for line in source_path.open()]
    rooms={key:new_room() for key in {(r['run_id'],r['room_id']) for r in rows if r['split']=='train'}}
    for index,session in enumerate(meta['source_sessions'],1):
        audit_rooms(iter_messages(args.source/'recordings'/session,include_session=True,include_diagnostics=False,only_closed=True),rooms)
        print(json.dumps({'stage':'auditing_complete_rooms','session':index,'total_sessions':len(meta['source_sessions'])}),flush=True)
    accepted={key for key,room in rooms.items() if accepted_room(room)[0]}
    candidates=defaultdict(list);rejected=Counter()
    for index,row in enumerate(rows):
        if row['split']!='train':continue
        key=(row['run_id'],row['room_id']);q=row['quality']
        if key not in accepted:rejected['room_not_verified_damage_free']+=1;continue
        # Require this exact sample to lie in an observed uninterrupted successful visit.
        session=row['episode_id'].rsplit(':',1)[0]
        if not any(v['session']==session and v['entry_observed'] and v['cleared'] and
                   v['start_frame']<=row['game_frame'] and row['action_frame']<v['end_frame'] for v in rooms[key]['visits']):
            rejected['outside_complete_successful_visit']+=1;continue
        if row['outcome']['label']!='positive' or q['move_weight']<=0 or q['shoot_weight']<=0:
            rejected['uncertain_or_unsupported_outcome']+=1;continue
        if q['grades'][6]<2 or q['minimum_gap_estimate']<args.minimum_gap:
            rejected['narrow_or_unstable_movement']+=1;continue
        useful=q['grades'][4]>=3 or q['grades'][3]>=3 or q.get('cleared')
        mechanical_wait=q.get('observed_charge_or_wait') and q.get('complete')
        if not useful and not mechanical_wait:rejected['no_progress_or_mechanical_wait_evidence']+=1;continue
        candidates[key].append(index)
    # Temporal sampling retains transitions and valid stationary shooting. Rooms receive balanced quotas.
    pool={}
    for key,ids in sorted(candidates.items()):
        chosen=[];last={};previous_action=None
        for index in ids:
            row=rows[index];episode=row['episode_id'];changed=row['action_label']!=previous_action
            if changed or row['game_frame']-last.get(episode,-10000)>=3:
                chosen.append(index);last[episode]=row['game_frame']
            previous_action=row['action_label']
        if len(chosen)>args.per_room:
            chosen=[chosen[j*(len(chosen)-1)//(args.per_room-1)] for j in range(args.per_room)]
        pool[key]=chosen
    selected=[]
    for offset in range(args.per_room):
        for key in sorted(pool):
            if offset<len(pool[key]):selected.append(pool[key][offset])
            if len(selected)>=args.max_train:break
        if len(selected)>=args.max_train:break
    selected_set=set(selected)
    indices=[i for i,r in enumerate(rows) if r['split']!='train' or i in selected_set]
    assert len(selected_set)>=32,('Too few verified quality samples',len(selected_set))
    new_rows=[{**rows[i],'original_row_index':i} for i in indices]
    path=output/'samples.jsonl'
    with path.open('w') as stream:
        for row in new_rows:stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    mapping={old:new for new,old in enumerate(indices)}
    fixtures=[{**f,'row_index':mapping[f['row_index']]} for f in map(json.loads,(dataset/'runtime-fixtures.jsonl').open()) if f['row_index'] in mapping]
    with (output/'runtime-fixtures.jsonl').open('w') as stream:
        for fixture in fixtures:stream.write(json.dumps(fixture,ensure_ascii=False)+'\n')
    assert sum(new_rows[f['row_index']]['split']=='validation' for f in fixtures)>=32
    for name in ('numeric_names.json','option_texts.json','quality_reference.json'):
        (output/name).write_bytes((dataset/name).read_bytes())
    audit=[]
    for key,room in sorted(rooms.items()):
        okay,reasons=accepted_room(room)
        audit.append({'run_id':key[0],'room_id':key[1],**room,'accepted':okay,'rejection_reasons':reasons,
                      'eligible_quality_rows':len(candidates.get(key,[])),'selected_rows':sum(i in selected_set for i in candidates.get(key,[]))})
    (output/'room-audit.json').write_text(json.dumps(audit,indent=2))
    result={**meta,'counts':dict(Counter(r['split'] for r in new_rows)),'samples':len(new_rows),
            'samples_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'parent_dataset_sha256':meta['samples_sha256'],
            'original_row_indices':indices,'training_filter':{'version':'complete-room-no-damage-quality-v1',
              'max_train':args.max_train,'per_room':args.per_room,'minimum_gap_estimate':args.minimum_gap,
              'rooms_audited':len(rooms),'damage_free_completed_rooms':len(accepted),'selected_rooms':len(pool),
              'row_rejections':dict(rejected),'selected_actions':dict(Counter(rows[i]['action_label'] for i in selected_set)),
              'scope':'Only train filtered; original validation/test unchanged. Raw labels/prompts/numeric preserved.'},
            'changed_source_labels':False}
    result['quality_grade_counts']={axis:dict(Counter(str(r['quality']['grades'][j]) for r in new_rows))
                                   for j,axis in enumerate(meta['quality_axes'])}
    result['training_quality_grade_counts']={axis:dict(Counter(str(r['quality']['grades'][j]) for r in new_rows if r['split']=='train'))
                                            for j,axis in enumerate(meta['quality_axes'])}
    result['parent_quality_refinements']=meta.get('quality_refinements')
    result['quality_refinements']=dict(Counter(r['quality'].get('refinement_reason','none') for r in new_rows))
    (output/'dataset.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'stage':'quality_subset_complete','counts':result['counts'],'filter':result['training_filter']}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--max-train',type=int,default=2000)
    p.add_argument('--per-room',type=int,default=96);p.add_argument('--minimum-gap',type=float,default=8)
    a=p.parse_args()
    if a.max_train<32 or a.per_room<2:p.error('Invalid subset budget')
    run(a)
