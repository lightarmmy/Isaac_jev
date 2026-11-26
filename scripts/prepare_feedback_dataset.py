"""Rebuild feedback inputs on an immutable quality subset with identical raw labels/splits."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.recording import iter_messages
from isaac_jev.outcomes import segment_frames
from isaac_jev.features_v009 import observation,history_summary,PREFIX


def feedback_training_weights(row,summary):
    quality=dict(row['quality']);flags=[]
    if row['split']!='train':return quality
    progress=quality['grades'][4]>=3 or quality.get('cleared')
    if progress and quality['displacement_px']>=8 and summary['no_net_hp_progress_frames']>=15:
        quality['move_weight']*=1.5;flags.append('progress_after_stalled_reposition')
    if progress and row['action_label']%5!=0:
        quality['shoot_weight']*=1.3;flags.append('useful_directional_attack')
    if progress and quality['grades'][5]>=3:
        quality['shoot_weight']*=1.2;flags.append('high_relative_efficiency')
    if quality['grades'][4]==1 and not quality.get('observed_charge_or_wait') and quality['displacement_px']<6 and summary['no_net_hp_progress_frames']>=30:
        quality['move_weight']*=.3;quality['shoot_weight']*=.3;flags.append('prolonged_unproductive_wait')
    quality['feedback_alignment']=flags
    return quality


def run(args):
    destination=args.output;destination.mkdir(parents=True,exist_ok=True)
    if (destination/'dataset.json').exists():raise RuntimeError('Feedback data already prepared')
    meta=json.loads((args.dataset/'dataset.json').read_text());path=args.dataset/'samples.jsonl'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['samples_sha256']
    rows=[json.loads(line) for line in path.open()]
    expected={(r['episode_id'],r['game_frame']):i for i,r in enumerate(rows)}
    assert len(expected)==len(rows),'Duplicate episode/frame identities need explicit reconstruction'
    output=[None]*len(rows);fields=None;validation=[i for i,r in enumerate(rows) if r['split']=='validation']
    chosen={validation[j*(len(validation)-1)//31] for j in range(32)};fixtures=[];matched=0
    for session_number,session in enumerate(meta['source_sessions'],1):
        for segment in segment_frames(iter_messages(args.source/'recordings'/session,include_session=True,include_diagnostics=False,only_closed=True)):
            episode=session+':'+str(segment[0]['game_frame'])
            for j,frame in enumerate(segment):
                index=expected.get((episode,frame['game_frame']))
                if index is None:continue
                row=rows[index]
                assert row['run_id']==frame['run_id'] and row['room_id']==frame['room']['id']
                summary=history_summary(frame,segment[max(0,j-60):j])
                text,numeric,names=observation(frame,history_facts=summary)
                assert numeric[:930]==row['numeric'],'Legacy numerical inputs changed'
                if fields is None:fields=names
                assert fields==names
                output[index]={**row,'prompt':text,'numeric':numeric,'feedback_version':'past-observed-v1',
                               'quality':feedback_training_weights(row,summary['feedback'])}
                matched+=1
                if index in chosen:fixtures.append({'row_index':index,'frame':frame,'feedback_history':summary})
                if matched%512==0:print(json.dumps({'stage':'rebuilding_feedback','completed':matched,'total':len(rows)}),flush=True)
        print(json.dumps({'stage':'feedback_source_scan','sessions':session_number,'total_sessions':len(meta['source_sessions']),'completed':matched,'total':len(rows)}),flush=True)
    assert matched==len(rows) and all(r is not None for r in output)
    assert len(fixtures)==32 and {f['row_index'] for f in fixtures}==chosen
    sample_path=destination/'samples.jsonl'
    with sample_path.open('w') as stream:
        for row in output:stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
    with (destination/'runtime-fixtures.jsonl').open('w') as stream:
        for fixture in fixtures:stream.write(json.dumps(fixture,ensure_ascii=False)+'\n')
    (destination/'numeric_names.json').write_text(json.dumps(fields))
    for name in ('option_texts.json','quality_reference.json','room-audit.json'):
        (destination/name).write_bytes((args.dataset/name).read_bytes())
    result={**meta,'parent_dataset_sha256':meta['samples_sha256'],
            'samples_sha256':hashlib.sha256(sample_path.read_bytes()).hexdigest(),'numeric_size':len(fields),
            'feature_profile':PREFIX+'s01','input_changed':True,'changed_source_labels':False,
            'feedback_reconstruction':{'rows':matched,'legacy_numeric_prefix_identical':True,'independent_validation_fixtures':32,
              'history_frames':60,'no_future_or_cross_room_history':True,'applied_input_missing_stays_unknown':True},
            'feedback_training_weight_counts':dict(Counter(flag for row in output for flag in row['quality'].get('feedback_alignment',[]))),
            'evaluation_quality_weights_unchanged':True,
            'max_text_characters':max(len(r['prompt']) for r in output)}
    (destination/'dataset.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'stage':'feedback_data_complete','counts':result['counts'],'numeric_size':len(fields),'maximum_characters':result['max_text_characters']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);run(parser.parse_args())
