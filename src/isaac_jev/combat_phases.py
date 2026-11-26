"""Past-only NPC phase evidence. Render offsets and target positions are not physics."""
import math
import re

from .features_v007 import finite, valid_vector, motion_gap
from .terrain import player_radius, xy


def contact_enabled(entity):
    # IsaacDocs: NONE=0, PLAYERONLY=1, PLAYEROBJECTS=2, ENEMIES=3, ALL=4.
    value = entity.get('collision_class')
    return None if type(value) is not int else False if value in (0, 3) else True if value in (1, 2, 4) else None


def phase(entity):
    name = re.sub(r'[^a-z]', '', str(entity.get('animation') or '').lower())
    if any(word in name for word in ('jumpdown', 'land', 'fall', 'descend')):
        return 'descending_or_landing'
    if any(word in name for word in ('jumpup', 'leap', 'takeoff', 'ascend')):
        return 'ascending'
    if any(word in name for word in ('inair', 'airborne', 'hover')):
        return 'airborne'
    return 'noncontact_unknown' if contact_enabled(entity) is False else 'other_or_unknown'


def contiguous_frames(frame, history, limit=240):
    n = frame['game_frame']
    past = {f['game_frame']: f for f in history if f['run_id'] == frame['run_id']
            and f['room']['id'] == frame['room']['id'] and f.get('mode') == frame.get('mode')
            and n-limit <= f['game_frame'] < n}
    first = n
    while first-1 in past:
        first -= 1
    return [past[i] for i in range(first, n)] + [frame]


def enemies(frame):
    return {e['uid']: e for e in frame.get('entities', []) if e.get('uid')
            and e.get('kind') == 'enemy' and not e.get('friendly') and not e.get('dead')
            and valid_vector(e.get('position'))}


def phase_evidence(frame, frames):
    result = {}
    for uid, entity in enemies(frame).items():
        trail = []
        for f in reversed(frames):
            old = enemies(f).get(uid)
            if not old or any(old.get(k) != entity.get(k) for k in ('type', 'variant', 'subtype')):
                break
            trail.append((f['game_frame'], old))
        trail.reverse()
        # Learn only observed noncontact -> player-contact transitions of this NPC/animation.
        samples = []
        for (_, a), (_, b) in zip(trail, trail[1:]):
            if (a.get('animation') == b.get('animation') == entity.get('animation')
                    and phase(a) == 'descending_or_landing'
                    and contact_enabled(a) is False and contact_enabled(b) is True
                    and finite(b.get('animation_frame')) is not None):
                samples.append(float(b['animation_frame']))
        same_phase = []
        for tick, old in reversed(trail):
            if (old.get('animation'), old.get('state')) != (entity.get('animation'), entity.get('state')):
                break
            if same_phase:
                newer = same_phase[-1][1]
                if any(finite(old.get(k)) is not None and finite(newer.get(k)) is not None
                       and old[k]>newer[k] for k in ('animation_frame', 'state_frame')):
                    break
            same_phase.append((tick, old))
        age = frame['game_frame'] - same_phase[-1][0] if same_phase else 0
        pending = phase(entity) in ('ascending', 'airborne', 'descending_or_landing') and contact_enabled(entity) is not True
        anim_frame = finite(entity.get('animation_frame'))
        eta = None
        if pending and samples and anim_frame is not None and anim_frame <= max(samples):
            # Empirical animation-frame envelope, not a universal time bound.
            rates = [(b['animation_frame']-a['animation_frame'])/(tb-ta) for (ta, a), (tb, b)
                     in zip(same_phase[::-1], same_phase[-2::-1])
                     if tb>ta and finite(a.get('animation_frame')) is not None and finite(b.get('animation_frame')) is not None
                     and b['animation_frame']>a['animation_frame']]
            rate = sum(rates)/len(rates) if rates else None
            if rate:
                eta = [round(max(0, min(samples)-anim_frame)/rate, 1), round((max(samples)-anim_frame)/rate, 1)]
        points = [xy(old['position']) for _, old in same_phase[:6]]
        drift = max((math.dist(xy(entity['position']), p) for p in points), default=0.)
        # Neither TargetPosition nor GetPlayerTarget is a documented landing contract.
        result[uid] = {'phase': phase(entity), 'animation': entity.get('animation'),
                       'animation_frame': entity.get('animation_frame'), 'overlay': entity.get('overlay_animation'),
                       'overlay_frame': entity.get('overlay_frame'), 'state': entity.get('state'),
                       'state_frame': entity.get('state_frame'), 'phase_observed_frames': age,
                       'phase_start_known': bool(same_phase and len(same_phase)<len(trail)),
                       'contact_now': contact_enabled(entity), 'attackable_now': entity.get('vulnerable'),
                       'pending_landing': pending, 'eta_frames': eta, 'timing_samples': len(samples),
                       'ground_projection': xy(entity['position']) if pending else None,
                       'projection_pad_px': round(12+drift, 1) if pending else None,
                       'sprite_offset': entity.get('sprite_offset'), 'position_offset': entity.get('position_offset'),
                       'npc_target_position': entity.get('npc_target_position'),
                       'observed_target_position': entity.get('observed_target_position')}
    return result


def immediate_contact_gap(frame, movement=(0, 0)):
    origin = xy(frame['player']['position']); pv = xy(frame['player'].get('velocity', {}))
    speed = max(1., 4*finite(frame['player'].get('stats', {}).get('move_speed'), 1))
    length = math.hypot(*movement) or 1
    mv = tuple(v*speed/length for v in movement); gaps = []
    for e in enemies(frame).values():
        if contact_enabled(e) is False:
            continue
        pos = xy(e['position']); vel = xy(e.get('velocity', {}))
        relative = tuple(pos[j]+vel[j]*4-origin[j]-pv[j]*4 for j in range(2))
        gaps.append(motion_gap(relative, tuple(vel[j]-mv[j] for j in range(2)),
                               max(player_radius(frame))+finite(e.get('size'), 10), horizon=3)['minimum_gap'])
    return min(gaps, default=320.)
