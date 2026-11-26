"""Rebuild shared semantic inputs and ordinal annotations on the original run splits."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.actions import label_from_input
from isaac_jev.features_v007 import observation,semantic_history_summary,PREFIX,QUALITY_AXES,option_texts
from isaac_jev.outcomes import segment_frames,collect_player_damage,damage_evidence,label_outcome
from isaac_jev.quality_v007 import annotate,kind_gap,fit_weights,refine_feedback
from isaac_jev.recording import iter_messages
from isaac_jev.rooms import combat_active


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def run(source,destination):
    destination.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    meta=json.loads((source/'datasets/combat-v005/dataset.json').read_text())
    original=source/'datasets/combat-v005/samples.jsonl'
    rows=[json.loads(line) for line in original.open(encoding='utf-8')]
    output=[];cursor=0;names=None;max_chars=0
    for session in sorted(meta['source_sessions']):
        def messages():return iter_messages(source/'recordings'/session,include_session=True,include_diagnostics=False,only_closed=True)
        callbacks=collect_player_damage(messages())
        for segment in segment_frames(messages()):
            if cursor>=len(rows):break
            run_id,room=segment[0]['run_id'],segment[0]['room']['id']
            if run_id!=rows[cursor]['run_id'] or room!=rows[cursor]['room_id']:
                continue
            lag=meta['observation_to_action_frame_lag'];phase=[combat_active(f,True) for f in segment]
            phase_end=[len(segment)]*len(segment);end=len(segment)
            for j in range(len(segment)-1,-1,-1):
                if not phase[j]:end=j
                phase_end[j]=end
            evidence=damage_evidence(segment,callbacks)
            cg=[kind_gap(f,'enemy') for f in segment];pg=[kind_gap(f,'projectile') for f in segment]
            def phase_fn(f):return phase[f['game_frame']-segment[0]['game_frame']]
            for i in range(len(segment)-lag):
                if cursor>=len(rows):break
                frame=segment[i];expected=rows[cursor]
                key=(frame['run_id'],frame['room']['id'],frame['game_frame'])
                if key!=(expected['run_id'],expected['room_id'],expected['game_frame']):continue
                action_frame=segment[i+lag];action=label_from_input(action_frame['human_input'])[0]
                assert action==expected['action_label'] and action_frame['game_frame']==expected['action_frame']
                outcome=label_outcome(segment,i,evidence,meta['outcome_window_frames'],lag,phase_fn)
                assert outcome==expected['outcome'],(cursor,'Original outcome evidence changed')
                history=semantic_history_summary(frame,segment[max(0,i-18):i])
                prompt,numeric,fields=observation(frame,history_facts=history,profile=PREFIX+'s01')
                if names is None:names=fields
                assert fields==names
                short=label_outcome(segment,i,evidence,12,lag,phase_fn)
                quality=annotate(segment,i,lag,outcome,short,phase_end,cg,pg)
                # Store exact runtime fixture for independent encode/deploy checks.
                row={k:expected[k] for k in ('run_id','room_id','game_frame','action_frame','action_label','split','previous_action_label','outcome')}
                row.update(prompt=prompt,numeric=numeric,quality=quality,room_type=frame['room'].get('type'),
                           episode_id=session+':'+str(segment[0]['game_frame']))
                output.append(row);max_chars=max(max_chars,len(prompt));cursor+=1
                if cursor%2048==0:print(json.dumps({'stage':'semantic_data','completed':cursor,'total':len(rows),'seconds':round(time.monotonic()-started,1)}),flush=True)
                if cursor%512==0:
                    with (destination/'runtime-fixtures.jsonl').open('a',encoding='utf-8') as fixtures:
                        fixtures.write(json.dumps({'row_index':cursor-1,'frame':frame,'semantic_history':history},ensure_ascii=False)+'\n')
        print(json.dumps({'session':session,'completed':cursor,'total':len(rows)}),flush=True)
    assert cursor==len(rows),(cursor,len(rows),'Some source samples were not reconstructed')
    references=fit_weights(output)
    refinements=refine_feedback(output)
    with (destination/'samples.jsonl').open('w',encoding='utf-8') as stream:
        for row in output:stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    (destination/'numeric_names.json').write_text(json.dumps(names))
    (destination/'option_texts.json').write_text(json.dumps(option_texts(),ensure_ascii=False,indent=2))
    (destination/'quality_reference.json').write_text(json.dumps(references,ensure_ascii=False,indent=2))
    summary={'counts':dict(Counter(row['split'] for row in output)),'samples':len(output),'source_sha256':digest(original),
             'samples_sha256':digest(destination/'samples.jsonl'),'source_sessions':meta['source_sessions'],'run_splits':meta['run_splits'],
             'numeric_size':len(names),'quality_axes':list(QUALITY_AXES),'quality_grade_counts':{
                 axis:dict(Counter(str(row['quality']['grades'][j]) for row in output)) for j,axis in enumerate(QUALITY_AXES)},
             'max_text_characters':max_chars,'action_lag_frames':lag,'fixed_objective':'safe_progress',
             'future_evidence_scope':'quality and weights only; prompts contain current and past observations',
             'all_45_options_retained':True,'changed_source_labels':False,'elapsed_seconds':round(time.monotonic()-started,2)}
    summary['quality_refinements']=refinements
    assert summary['counts']==meta['counts']
    (destination/'dataset.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({'stage':'semantic_data_complete',**summary},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if (args.output/'samples.jsonl').exists():raise SystemExit('Prepared dataset already exists; verify it instead of overwriting')
    (args.output/'runtime-fixtures.jsonl').parent.mkdir(parents=True,exist_ok=True)
    (args.output/'runtime-fixtures.jsonl').write_text('')
    run(args.source,args.output)
