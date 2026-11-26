"""Retrospective quality associations. Future telemetry is annotation-only, never a policy input."""
import math
from .actions import label_from_input
from .terrain import xy


def enemies(frame):
    return {e['uid']: e for e in frame.get('entities', []) if e.get('uid') and e.get('kind') == 'enemy'
            and not e.get('friendly') and not e.get('dead')}


def confirmed_enemy_progress(before, after):
    a, b = enemies(before), enemies(after)
    drops = 0
    for uid in a.keys() & b.keys():
        old, new = a[uid], b[uid]
        same = all(old.get(k) == new.get(k) for k in ('type', 'variant', 'max_hp'))
        hp0, hp1 = old.get('hp'), new.get('hp')
        if same and isinstance(hp0, (int, float)) and isinstance(hp1, (int, float)) and hp1 < hp0 and hp0 > 0:
            drops += hp0 - max(0, hp1)
    clear = before['room'].get('clear') is False and after['room'].get('clear') is True
    return drops, clear


def threat_gap(frame):
    pos = xy(frame['player']['position']); velocity = xy(frame['player'].get('velocity', {}))
    radius = float(frame['player'].get('size') or 10)
    nearest = 999.
    for e in frame.get('entities', []):
        if e.get('kind') not in {'enemy', 'projectile'} or e.get('friendly') or e.get('dead'):continue
        target = xy(e['position']); v = xy(e.get('velocity', {}))
        dx, dy = target[0] - pos[0], target[1] - pos[1]
        vx, vy = v[0] - velocity[0], v[1] - velocity[1]
        t = max(0, min(6, -(dx * vx + dy * vy) / max(1e-6, vx * vx + vy * vy)))
        nearest = min(nearest, math.hypot(dx + vx * t, dy + vy * t) - radius - float(e.get('size') or 10))
    return nearest


def annotate_quality(segment, index, lag, short_outcome, progress_prefix, clear_prefix, gaps, phase_end):
    start = index + lag
    end = min(len(segment) - 1, start + 60, phase_end[start])
    complete = end >= start + 60 or (phase_end[start] < len(segment) and phase_end[start] <= start + 60)
    origin = xy(segment[start]['player']['position'])
    displacement = max((math.dist(origin, xy(f['player']['position'])) for f in segment[start:end + 1]), default=0)
    progress = progress_prefix[end] - progress_prefix[start] > 0 or clear_prefix[end] - clear_prefix[start] > 0
    short_end = min(end, start + 12)
    separated = (displacement >= 8 and gaps[start] < 24 and gaps[short_end] >= gaps[start] + 12
                 and short_outcome['label'] == 'positive')
    known_wait = bool(enemies(segment[start])) and all(e.get('vulnerable') is False for e in enemies(segment[start]).values())
    weapons = segment[start]['player'].get('weapons', [])
    if isinstance(weapons, dict):weapons = list(weapons.values())
    known_charge = any(isinstance(w, dict) and w.get('charge_known') and w.get('holding') for w in weapons)
    still = displacement < 6 and all(label_from_input(f.get('human_input', {}))[0] // 5 == 0 for f in segment[start:end + 1])
    closing = gaps[end] < gaps[start] - 12 and gaps[end] < 48
    unproductive = complete and still and not progress and closing and not known_wait and not known_charge
    return {'quality_window_complete': complete, 'enemy_progress': progress, 'threat_separation_improved': separated,
            'known_wait': known_wait, 'known_charge': known_charge, 'unproductive_still': unproductive,
            'movement_displacement_px': round(displacement, 2), 'gap_start_estimate': round(gaps[start], 2),
            'gap_end_estimate': round(gaps[end], 2), 'quality_causal_claim': False}


def branch_weights(annotation, outcome, scheme):
    if scheme == 'short12':
        weight = annotation['short_outcome']['weight']
        return weight, weight
    weight = outcome['weight']
    if scheme == 'original':return weight, weight
    if scheme not in {'dual_quality', 'dual_balanced'}:raise ValueError('Unknown quality scheme')
    move = shoot = weight
    if outcome['label'] == 'negative':
        delay = outcome.get('damage_after_action_frames') or 30
        move *= .35 if delay <= 12 else .7
        # A player injury is not proof that the firing direction was wrong.
        shoot = .6 if annotation['enemy_progress'] else max(.25, weight * .8)
    if annotation['threat_separation_improved']:move *= 1.5
    if annotation['enemy_progress']:shoot *= 1.25
    if annotation['unproductive_still']:move *= .35;shoot *= .5
    if not annotation['quality_window_complete']:move *= .85;shoot *= .85
    if scheme == 'dual_balanced':
        move *= annotation.get('balance_weight', 1)
        shoot *= annotation.get('balance_weight', 1)
    return max(.025, move), max(.025, shoot)
