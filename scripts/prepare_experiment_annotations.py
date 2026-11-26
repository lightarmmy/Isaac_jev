"""Align retrospective quality metadata with the immutable V005 sample ordering."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from isaac_jev.actions import label_from_input
from isaac_jev.outcomes import collect_player_damage, damage_evidence, label_outcome, segment_frames
from isaac_jev.recording import iter_messages
from isaac_jev.rooms import combat_active
from isaac_jev.quality_labels import annotate_quality, confirmed_enemy_progress, threat_gap


def prepare(recordings, metadata, destination):
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    excluded = {(x['run_id'], x['room_id']) for k in ('excluded_active_rooms', 'excluded_combat_bomb_rooms') for x in meta[k]}
    output = []; counts = Counter(); outcomes = Counter(); started = time.monotonic()
    for session in sorted(meta['source_sessions']):
        def messages():return iter_messages(recordings / session, include_session=True, include_diagnostics=False, only_closed=True)
        callbacks = collect_player_damage(messages())
        for segment in segment_frames(messages()):
            run, room = segment[0]['run_id'], segment[0]['room']['id']
            if (run, room) in excluded:continue
            phases = [combat_active(f, True) for f in segment]
            def phase(f):return phases[f['game_frame'] - segment[0]['game_frame']]
            evidence = damage_evidence(segment, callbacks)
            progress = [0.]; clear = [0]; gaps = [threat_gap(f) for f in segment]
            for before, after in zip(segment, segment[1:]):
                hp, done = confirmed_enemy_progress(before, after)
                progress.append(progress[-1] + hp); clear.append(clear[-1] + done)
            phase_end = [len(segment)] * len(segment); next_end = len(segment)
            for i in range(len(segment)-1, -1, -1):
                if not phases[i]:next_end = i
                phase_end[i] = next_end
            lag = meta['observation_to_action_frame_lag']
            for i in range(len(segment)-lag):
                obs, frame = segment[i], segment[i+lag]
                if not all(phases[j] and segment[j]['player'].get('controls_enabled', True) and not segment[j]['player'].get('dead')
                           for j in range(i, i+lag+1)):continue
                action, _ = label_from_input(frame['human_input']); previous, _ = label_from_input(obs['human_input'])
                release = action % 5 == 0 and previous % 5 != 0
                if frame['game_frame'] % meta['stride'] and not release:continue
                outcome = label_outcome(segment, i, evidence, meta['outcome_window_frames'], lag, phase)
                short = label_outcome(segment, i, evidence, 12, lag, phase)
                quality = annotate_quality(segment, i, lag, short, progress, clear, gaps, phase_end)
                row = {'run_id': run, 'room_id': room, 'game_frame': obs['game_frame'], 'action_frame': frame['game_frame'],
                       'action_label': action, 'split': meta['run_splits'][run], 'outcome': outcome, 'short_outcome': short,
                       'recording_session': session, 'episode_id': session + ':' + str(segment[0]['game_frame']),
                       'room_type': obs['room'].get('type'), **quality}
                output.append(row); counts[row['split']] += 1; outcomes[(row['split'], outcome['label'])] += 1
        print(json.dumps({'session': session, 'samples': len(output), 'elapsed_seconds': round(time.monotonic()-started,1)}),flush=True)
    assert dict(counts) == meta['counts'], (counts, meta['counts'])
    assert all(outcomes[(split, label)] == count for split, labels in meta['outcome_counts'].items() for label, count in labels.items())
    runs = Counter(r['run_id'] for r in output if r['split'] == 'train')
    chunks = Counter((r['episode_id'], r['action_frame']//30, r['action_label']) for r in output if r['split'] == 'train')
    median = statistics.median(runs.values())
    for r in output:
        if r['split'] == 'train':
            run_weight = max(.5, min(2., (median/runs[r['run_id']])**.5))
            chunk_weight = min(1., 4/chunks[(r['episode_id'],r['action_frame']//30,r['action_label'])])
            r['balance_weight'] = round(run_weight * chunk_weight, 6)
        else:r['balance_weight'] = 1.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('w', encoding='utf-8') as f:
        for r in output:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
    report = {'counts':dict(counts), 'quality':dict(Counter(k for r in output for k in ('enemy_progress','threat_separation_improved','unproductive_still','known_wait','known_charge') if r[k])),
              'input_scope':'Closed manual V005 source sessions only; no AI data', 'ordering':'Original V005 sample order',
              'balance_fitted_on':'training split only', 'future_fields':'annotation only'}
    destination.with_suffix('.summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('recordings',type=Path);p.add_argument('metadata',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();prepare(a.recordings,a.metadata,a.output)
