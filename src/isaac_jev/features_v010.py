"""Phase-aware threats and committed attack-route facts, shared by training/runtime."""
from functools import lru_cache
import hashlib
import json
import math

from .actions import MOVES, MOVE_NAMES, SHOTS, SHOT_NAMES
from .combat_phases import contiguous_frames, enemies, phase_evidence, immediate_contact_gap
from .features_v007 import finite, valid_vector
from .features_v009 import observation as old_observation, history_summary as old_history, attack_routes, RULES as OLD_RULES
from .features_v005 import compact_facts
from .terrain import xy, player_radius

PREFIX = 'combat-tactical-v10-'
RULES = OLD_RULES + (' Current contact collision and future landing danger are separate. '
    'An unattackable enemy can still threaten you. Animation phases are name-based cues; '
    'landing zones are uncertain ground-position proxies, target fields are unverified hints, '
    'and render offsets are not physical height. Timing is empirical from prior collision restorations '
    'of the same NPC/animation; null means unknown, never safe. '
    'Prefer sustained progress along a committed attack route when immediate escape is unnecessary. '
    'Reassess terrain-obstructed fire or no HP progress; firing into walls is not useful progress. '
    'Route commitment resets at most every 60 frames and on invalid geometry; it does not execute controls. '
    'Legacy constant-motion contact estimates are unfiltered; use phase-aware contact and landing facts.')
RULES_HASH = hashlib.sha256(RULES.encode()).hexdigest()


def contract(base, profile):
    from .features_v009 import contract as previous_contract
    if profile != PREFIX+'s01':
        raise ValueError('Unknown tactical profile')
    return {**previous_contract(base, 'combat-feedback-v9-s01'), 'feature_version': profile,
            'rules_sha256': RULES_HASH, 'history_version': 'past-phase240-route60-v1',
            'feedback_history_frames': 240, 'feedback_version': 'applied-direction-phase-route-v1',
            'terrain_version': 'committed-clear-line-route60-v1', 'text_serialization': 'tactical-consequences-v1',
            'outcome_version': 'ordinal-phase-aware-contact-v10', 'phase_version': 'animation-collision-evidence-v1'}


def observed_shoot_direction(frame, frames):
    n = frame['game_frame']
    if frame.get('mode') == 'ai':
        for f in reversed(frames):
            c = f.get('applied_controls') or {}; tick = c.get('game_frame')
            if (type(tick) is not int or not n-6 <= tick < n or tick > f['game_frame']
                    or c.get('run_id') != frame['run_id'] or c.get('room_id') != frame['room']['id']):
                continue
            requests = c.get('requests') or []
            queried = {str(i): any(r.get('queried', {}).get(str(i)) for r in requests) for i in range(4, 8)}
            pressed = [i for i in range(4, 8) if any(r.get('queried', {}).get(str(i))
                       and r.get('pressed', {}).get(str(i)) for r in requests)]
            direction = {4: 'left', 5: 'right', 6: 'up', 7: 'down'}
            if len(pressed) == 1:
                return {'direction': direction[pressed[0]], 'source': 'ai_callback', 'age_frames': n-tick}
            if len(pressed)>1:
                return {'direction': None, 'source': 'conflicting_callback_queries', 'age_frames': n-tick}
            if all(queried.values()):
                return {'direction': 'none', 'source': 'ai_callback', 'age_frames': n-tick}
    else:
        # Keep N's human action strictly out of N's input (N+4 imitation alignment).
        for f in reversed(frames[:-1]):
            value = f.get('human_input', {}).get('shoot')
            if n-f['game_frame']>3:
                break
            if valid_vector(value):
                x, y = xy(value); sign = lambda v: 1 if v>.25 else -1 if v<-.25 else 0
                vector = (sign(x), 0) if abs(x)>=abs(y) else (0, sign(y))
                return {'direction': SHOT_NAMES[SHOTS.index(vector)], 'source': 'past_manual_keys', 'age_frames': n-f['game_frame']}
    return {'direction': None, 'source': 'unobserved', 'age_frames': None}


@lru_cache(maxsize=64)
def reference_route(serialized):
    anchor = json.loads(serialized); origin = xy(anchor['player']['position'])
    candidates = sorted(enemies(anchor).values(), key=lambda e: (math.dist(origin, xy(e['position'])), str(e['uid'])))[:4]
    labels = [(e['uid'], str(i)) for i, e in enumerate(candidates)]
    routes, _ = attack_routes(anchor, labels, allow_untargetable=True, node_limit=None)
    if not routes:
        return None
    row = min(routes, key=lambda r: (r[3], r[0])); entity = candidates[int(row[0])]
    return {'uid': entity['uid'], 'identity': [entity.get(k) for k in ('type', 'variant', 'subtype')],
            'goal': [origin[0]+row[1], origin[1]+row[2]], 'path_px': row[3]}


def route_feedback(frame, frames):
    boundary = frame['game_frame']//60*60
    anchor = next(f for f in frames if f['game_frame']>=boundary)
    previous = reference_route(json.dumps(anchor, sort_keys=True, separators=(',', ':')))
    origin = xy(frame['player']['position']); current = enemies(frame)
    retained = previous and previous['uid'] in current and previous['identity'] == [current[previous['uid']].get(k) for k in ('type', 'variant', 'subtype')]
    if retained:
        labels = [(previous['uid'], 'pursuit_target')]
        routes, costs = attack_routes(frame, labels, preferred_goals={previous['uid']: previous['goal']}, allow_untargetable=True, node_limit=None)
    else:
        labels = [(e['uid'], 'pursuit_'+str(i)) for i, e in enumerate(sorted(current.values(),
                  key=lambda e: (math.dist(origin, xy(e['position'])), str(e['uid'])))[:4])]
        routes, _ = attack_routes(frame, labels, allow_untargetable=True, node_limit=None)
        row = min(routes, key=lambda r: (r[3], r[0]), default=None)
        labels = [next(pair for pair in labels if pair[1] == row[0])] if row else []
        routes, costs = attack_routes(frame, labels, allow_untargetable=True, node_limit=None) if labels else ([], [None]*9)
    row = routes[0] if routes else None
    if not row:
        return {'target': None, 'status': 'route_unknown', 'age_frames': frame['game_frame']-anchor['game_frame'],
                'path_px': None, 'progress_px': None, 'position_delta': None, 'next_waypoint': None,
                'target_identity': None, 'scope': 'static geometry only'}, costs
    entity = current[labels[0][0]]; goal = [origin[0]+row[1], origin[1]+row[2]]
    same_goal = bool(retained and math.dist(goal, previous['goal'])<1)
    return {'target': row[0], 'target_identity': [entity.get('type'), entity.get('variant'),
            round(entity['position']['x']-origin[0], 1), round(entity['position']['y']-origin[1], 1), entity.get('vulnerable')],
            'status': 'committed' if same_goal else 'replanned_geometry' if retained else 'new_target',
            'age_frames': frame['game_frame']-anchor['game_frame'], 'path_px': row[3],
            'progress_px': round(previous['path_px']-row[3], 1) if same_goal else None,
            'position_delta': row[1:3], 'next_waypoint': row[4:6],
            'scope': 'static geometry only; no control override'}, costs


def history_summary(frame, history):
    frames = contiguous_frames(frame, history)
    return {**old_history(frame, frames[:-1]), 'tactical': {'identity': [frame['run_id'], frame['room']['id'], frame['game_frame']], 'direction': observed_shoot_direction(frame, frames),
            'phases': phase_evidence(frame, frames), 'route': route_feedback(frame, frames)}}


def observation(frame, history=(), condition='safe_progress', history_facts=None, profile=PREFIX+'s01'):
    if profile != PREFIX+'s01' or condition != 'safe_progress':
        raise ValueError('Tactical policy contract mismatch')
    summaries = history_summary(frame, history) if history_facts is None else history_facts
    if not isinstance(summaries, dict) or 'tactical' not in summaries:
        raise ValueError('Bridge needs tactical history support')
    if summaries['tactical']['identity'] != [frame['run_id'], frame['room']['id'], frame['game_frame']]:
        raise ValueError('Tactical feedback observation identity mismatch')
    prompt, values, names = old_observation(frame, condition=condition, history_facts=summaries)
    state = json.loads(prompt.split('\nCurrent feedback state:\n', 1)[1]); evidence = summaries['tactical']
    route, route_costs = evidence['route']; direction = evidence['direction']
    state['feedback']['observed_shoot_direction'] = direction
    state['feedback']['current_attack_geometry'] = ('not_shooting' if direction['direction']=='none' else
        next((status for d, status in state['candidate_consequences']['rows'][0][6] if d==direction['direction']), 'direction_unobserved'))
    state['pursuit'] = route
    state['attack_routes'] = {'scope': 'replaced by committed pursuit route'}
    phase_rows = []; origin = xy(frame['player']['position']); entities = enemies(frame)
    landing = [(e, evidence['phases'][uid]) for uid, e in entities.items() if evidence['phases'][uid]['pending_landing']]
    selected = sorted(entities, key=lambda uid: (not evidence['phases'][uid]['pending_landing'],
                      math.dist(origin, xy(entities[uid]['position'])), str(uid)))[:4]
    for i, uid in enumerate(selected):
        e = entities[uid]; p = evidence['phases'][uid]
        relative = lambda v: [round(v['x']-origin[0], 1), round(v['y']-origin[1], 1)] if valid_vector(v) else None
        phase_rows.append(['phase_'+str(i), e.get('type'), e.get('variant'), *relative(e['position']),
            p['animation'], p['animation_frame'], p['overlay'], p['overlay_frame'], p['state'], p['state_frame'],
            p['phase'], p['phase_observed_frames'], p['phase_start_known'], p['contact_now'], p['attackable_now'],
            p['eta_frames'], p['timing_samples'], p['projection_pad_px'],
            xy(p['sprite_offset']) if valid_vector(p['sprite_offset']) else None,
            xy(p['position_offset']) if valid_vector(p['position_offset']) else None,
            relative(p['npc_target_position']), relative(p['observed_target_position'])])
    state['npc_phases'] = {'columns': ['id','type','variant','dx','dy','animation','anim_frame','overlay','overlay_frame',
        'state','state_frame','phase_cue','phase_age','start_known','contact_now','attackable','landing_eta','timing_samples',
        'projection_pad','sprite_offset','position_offset','unverified_npc_target','unverified_player_target'], 'rows': phase_rows,
        'omitted': max(0, len(entities)-len(selected)), 'zone_basis': 'current ground position proxy; pad is heuristic, not a bound'}
    def add(name, value):
        names.append(name); values.append(float(value))
    add('tactical_direction_known', direction['direction'] is not None)
    add('tactical_route_committed', route['status']=='committed')
    add('tactical_route_age', route['age_frames']/60)
    add('tactical_route_progress_known', route['progress_px'] is not None)
    add('tactical_route_progress', max(-4, min(4, (route['progress_px'] or 0)/100)))
    add('tactical_pending_landing_count', len(landing)/4)
    eta = min((p['eta_frames'][0] for _, p in landing if p['eta_frames']), default=None)
    add('tactical_landing_eta_known', eta is not None); add('tactical_landing_eta', min(4, (eta or 0)/30))
    pv = xy(frame['player'].get('velocity', {})); speed = max(1., 4*finite(frame['player'].get('stats', {}).get('move_speed'), 1))
    for i, (move, cost) in enumerate(zip(MOVES, route_costs)):
        length = math.hypot(*move) or 1; margins = []
        for e, p in landing:
            # Evaluate earliest empirical restoration (capped at 12f) or an explicit 12f unknown-time probe.
            horizon = min(12., p['eta_frames'][0]) if p['eta_frames'] else 12.
            point = tuple(origin[j]+pv[j]*min(4., horizon)+move[j]*speed*max(0., horizon-4)/length for j in range(2))
            margins.append(math.dist(point, p['ground_projection'])-max(player_radius(frame))-finite(e.get('size'), 10)-p['projection_pad_px'])
        gap = round(min(margins), 1) if margins else None
        immediate = round(immediate_contact_gap(frame, move), 1)
        row = state['candidate_consequences']['rows'][i]
        row[-1] = cost; row.extend([immediate, gap])
        add(f'tactical_move_{i}_contact_gap', max(-4, min(4, immediate/100)))
        add(f'tactical_move_{i}_landing_known', gap is not None)
        add(f'tactical_move_{i}_landing_gap', 0 if gap is None else max(-4, min(4, gap/100)))
        add(f'tactical_move_{i}_route_known', cost is not None)
        add(f'tactical_move_{i}_route_cost', 0 if cost is None else min(4, cost/800))
    columns = state['candidate_consequences']['columns']; columns[2] = 'constant_motion_gap_unfiltered'
    columns.extend(['contact_now_gap', 'landing_projection_gap'])
    state['landing_probe'] = 'earliest empirical restoration <=12f, otherwise 12f probe with unknown timing; approximate steering'
    text = RULES+'\nCurrent feedback state:\n'+json.dumps(compact_facts(state), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return text, values, names
