"""Rebuild V010 on the unchanged no-damage-room subset with phase-aware supervision."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from isaac_jev.combat_phases import contact_enabled
from isaac_jev.features_v010 import observation, history_summary, PREFIX
from isaac_jev.features_v009 import attack_routes
from isaac_jev.quality_labels import threat_gap
from isaac_jev.recording import iter_messages
from isaac_jev.outcomes import segment_frames
from isaac_jev.terrain import xy
from prepare_feedback_dataset import feedback_training_weights


def phase_training_weights(row, summary, window, observation_frame=None):
    quality = copy.deepcopy(feedback_training_weights(row, summary['feedback']))
    if row['split'] != 'train':
        return quality
    if not window:
        return quality
    flags = quality.setdefault('tactical_alignment', [])
    removed = any(e.get('kind')=='enemy' and not e.get('friendly') and contact_enabled(e) is False
                  for f in window for e in f.get('entities', []))
    if removed:
        # Outcomes are annotation-only. Do not count an airborne noncolliding NPC as current contact pressure.
        contact = [threat_gap({**f, 'entities': [e for e in f.get('entities', []) if e.get('kind')=='enemy'
                    and contact_enabled(e) is not False]}) for f in window]
        bullets = [threat_gap({**f, 'entities': [e for e in f.get('entities', []) if e.get('kind')=='projectile']}) for f in window]
        gap = min(*contact, *bullets)
        if min(contact)>=24 and quality['grades'][2]>=0:
            quality['grades'][2] = -1; quality['confidence'][2] = 0
            flags.append('airborne_contact_axis_masked')
        if quality['grades'][6]>=0:
            old_grade = quality['grades'][6]
            grade = 1 if gap<4 or quality.get('rapid_move_changes', 0)>=3 else 2 if gap<12 else 4
            quality['grades'][6] = grade
            if old_grade==1 and grade!=1:
                quality['move_weight'] /= .3
                flags.append('false_airborne_robustness_penalty_removed')
        quality['phase_minimum_gap_estimate'] = round(gap, 2)
    route, _ = summary['tactical']['route']
    # Route progress is credited only when the same target and attack position remain valid.
    if (route['path_px'] is not None and row['action_label']//5!=0
            and (quality['grades'][4]>=3 or quality.get('cleared'))):
        frame = observation_frame or window[0]; last = window[-1]
        identity = route['target_identity']; origin = xy(frame['player']['position'])
        candidates = [e for e in frame.get('entities', []) if e.get('kind')=='enemy'
                      and [e.get('type'), e.get('variant')]==identity[:2]
                      and abs(e['position']['x']-origin[0]-identity[2])<1
                      and abs(e['position']['y']-origin[1]-identity[3])<1]
        if len(candidates)==1:
            uid = candidates[0]['uid']; goal = [origin[i]+route['position_delta'][i] for i in range(2)]
            later, _ = attack_routes(last, [(uid, 'pursuit_target')], preferred_goals={uid: goal}, allow_untargetable=True, node_limit=None)
            if later:
                end = xy(last['player']['position']); end_goal = [end[i]+later[0][i+1] for i in range(2)]
                if all(abs(a-b)<1 for a,b in zip(goal, end_goal)):
                    progress = route['path_px']-later[0][3]
                    quality['observed_pursuit_progress_px'] = round(progress, 1)
                    if progress>=8:
                        quality['move_weight'] *= 1.4; flags.append('safe_route_progress_with_attack_progress')
    quality['tactical_refinement_version'] = 'phase-route-association-v10-1'
    return quality


def run(args):
    dest = args.output; dest.mkdir(parents=True, exist_ok=True)
    if (dest/'dataset.json').exists():
        raise RuntimeError('Tactical dataset exists; preserve its immutable lineage')
    meta = json.loads((args.dataset/'dataset.json').read_text(encoding='utf-8'))
    path = args.dataset/'samples.jsonl'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['samples_sha256']
    rows = [json.loads(line) for line in path.open(encoding='utf-8')]
    expected = {(r['episode_id'], r['game_frame']): i for i,r in enumerate(rows)}
    assert len(expected)==len(rows)
    output = [None]*len(rows); names = None; fixtures = []; matched = 0
    validation = [i for i,r in enumerate(rows) if r['split']=='validation']
    chosen = {validation[j*(len(validation)-1)//31] for j in range(32)}
    wanted_sessions = {r['episode_id'].split(':', 1)[0] for r in rows}
    for number, session in enumerate(meta['source_sessions'], 1):
        if session not in wanted_sessions:
            continue
        messages = iter_messages(args.source/'recordings'/session, include_session=True, include_diagnostics=False, only_closed=True)
        for segment in segment_frames(messages):
            episode = session+':'+str(segment[0]['game_frame'])
            for j, frame in enumerate(segment):
                index = expected.get((episode, frame['game_frame']))
                if index is None:
                    continue
                row = rows[index]; assert row['run_id']==frame['run_id'] and row['room_id']==frame['room']['id']
                summary = history_summary(frame, segment[max(0,j-240):j])
                text, numeric, fields = observation(frame, history_facts=summary)
                assert numeric[:len(row['numeric'])]==row['numeric'], 'Original numerical prefix changed'
                if names is None: names = fields
                assert names==fields
                # Action occurs N+4; geometry window spans the following 12 frames only.
                start = j+row['action_frame']-row['game_frame']
                window = segment[start:min(len(segment), start+13)]
                output[index] = {**row, 'prompt': text, 'numeric': numeric,
                    'quality': phase_training_weights(row, summary, window, frame), 'feedback_version': 'phase-route-v10-1'}
                if index in chosen:
                    fixtures.append({'row_index': index, 'frame': frame, 'tactical_history': summary})
                matched += 1
        print(json.dumps({'stage':'tactical_reconstruction','sessions':number,'completed':matched,'total':len(rows)}), flush=True)
    assert matched==len(rows) and len(fixtures)==32 and all(r is not None for r in output)
    sample_path = dest/'samples.jsonl'
    with sample_path.open('w', encoding='utf-8') as stream:
        for row in output: stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n')
    with (dest/'runtime-fixtures.jsonl').open('w', encoding='utf-8') as stream:
        for fixture in fixtures: stream.write(json.dumps(fixture, ensure_ascii=False)+'\n')
    (dest/'numeric_names.json').write_text(json.dumps(names), encoding='utf-8')
    for filename in ('option_texts.json', 'quality_reference.json', 'room-audit.json'):
        (dest/filename).write_bytes((args.dataset/filename).read_bytes())
    report = {**meta, 'parent_dataset_sha256': meta['samples_sha256'], 'feature_profile': PREFIX+'s01',
              'samples_sha256': hashlib.sha256(sample_path.read_bytes()).hexdigest(), 'numeric_size': len(names),
              'changed_source_labels': False, 'evaluation_quality_weights_unchanged': True,
              'tactical_reconstruction': {'rows': matched, 'history_frames': 240, 'route_commitment_frames': 60,
                'legacy_numeric_prefix_identical': True, 'independent_validation_fixtures': 32,
                'future_used_for_supervision_only': True, 'old_missing_npc_offsets_stay_unknown': True},
              'tactical_weight_counts': dict(Counter(flag for r in output for flag in r['quality'].get('tactical_alignment', []))),
              'feedback_weight_counts': dict(Counter(flag for r in output for flag in r['quality'].get('feedback_alignment', []))),
              'max_text_characters': max(len(r['prompt']) for r in output)}
    (dest/'dataset.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'stage':'tactical_data_complete','counts':report['counts'],'numeric_size':len(names),
                      'tactical_weight_counts':report['tactical_weight_counts']}), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    for name in ('source', 'dataset', 'output'): parser.add_argument('--'+name, type=Path, required=True)
    run(parser.parse_args())
