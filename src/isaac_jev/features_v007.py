"""Shared semantic facts for V007. Outcomes are supervision, never input conditions."""
import hashlib
import json
import math
from .actions import MOVES, MOVE_NAMES, SHOT_NAMES
from .features_v005 import KNOWLEDGE_HASH, observation as legacy_observation, compact_facts
from .terrain import Obstacle, player_radius, xy, static_obstacles
from .navigation import fire_obstacles
from .weapons import weapon_state

PREFIX = 'combat-semantic-v7-'
PROFILES = {'s01': 'separate-quality-towers-v7', 's02': 'shared-text-option-scorer-v7'}
QUALITY_AXES = ('survival', 'projectile_evasion', 'contact_evasion', 'attack_access', 'progress', 'efficiency', 'robustness')
RULES = ('Isaac combat: survive while making useful progress. Use current weapon capabilities and verified item effects. '
 'Escape imminent contact/projectile danger; compare all routes and keep room to escape. When attack access is poor, '
 'seek a useful attack position, not merely a smaller enemy distance. Holding an effective safe position, charging '
 'or waiting for invulnerable enemies can be appropriate. Move and shoot together when useful. Intervening terrain '
 'is geometry: walking blockage differs from attack obstruction and depends on penetration/trajectory. '
 'Relative x points right, y down; pixels and observed pixels per 30Hz frame. Candidate estimates use observed '
 'velocity before the 4-frame action alignment, then an instantaneous switch to 4*move_speed for 6 frames; '
 'this is not native inertia or a damage probability. Cardinal alignment/range are geometric proxies, not confirmed hits '
 'for complex attacks. Unknown stays null. No past control labels, future outcomes, seed, active items or forced actions. '
 'Local entity labels are presentation aliases, not random game identifiers. '
 'Choose a movement and shooting control under this same objective.')
RULES_HASH = hashlib.sha256(RULES.encode()).hexdigest()
CANDIDATE_FIELDS = ('clearance', 'contact_gap', 'projectile_gap', 'area_hazard', 'fire_hazard',
 'aligned_targets', 'range_relation', 'lateral_gap', 'grid_intersections', 'distance', 'vulnerable_targets', 'attack_geometry_known')


def profile_key(profile):
    key = profile[len(PREFIX):] if isinstance(profile, str) and profile.startswith(PREFIX) else ''
    if key not in PROFILES:
        raise ValueError('Unknown V007 profile')
    return key


def option_texts():
    texts = []
    for move in MOVE_NAMES:
        for shoot in SHOT_NAMES:
            movement = 'Hold position' if move == 'stay' else 'Move ' + move.replace('_', ' and ')
            attack = 'release directional shooting' if shoot == 'none' else 'hold shooting ' + shoot
            texts.append(movement + ' and ' + attack + '. Interpret holding/releasing by weapon trigger; '
                         'compare escape clearance, contact/projectile exposure and attack access at the moved position.')
    return texts


OPTIONS_HASH = hashlib.sha256(json.dumps(option_texts(), ensure_ascii=False).encode()).hexdigest()


def contract(base, profile):
    key = profile_key(profile)
    return {**base, 'feature_version': profile, 'rules_sha256': RULES_HASH, 'knowledge_sha256': KNOWLEDGE_HASH,
            'head_architecture': PROFILES[key], 'control_context': False, 'deployment_condition': 'safe_progress',
            'outcome_version': 'ordinal-multiaxis-evidence-v7', 'history_version': 'past-uid-hp-v7',
            'terrain_version': 'walk-attack-separated-v7', 'weapon_version': 'capability-with-unknown-v7',
            'text_serialization': 'semantic-relations-round2-v7', 'projectile_limit': 16,
            'candidate_version': 'move9-shoot5-text-facts-v7', 'option_text_sha256': OPTIONS_HASH,
            'candidate_fields': list(CANDIDATE_FIELDS), 'quality_axes': list(QUALITY_AXES),
            'training_phase': 'enemy-projectile-imminent-hazard-v2'}


def finite(value, default=None):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default


def valid_vector(value):
    return isinstance(value, dict) and all(finite(value.get(k)) is not None for k in ('x', 'y'))


def motion_gap(relative, velocity, radius, horizon=6):
    vx, vy = velocity; dx, dy = relative
    v2 = vx*vx + vy*vy; dot = dx*vx + dy*vy
    t = max(0, min(horizon, -dot/v2)) if v2 else 0.
    gap = math.hypot(dx+vx*t, dy+vy*t)-radius
    entry = 0. if dx*dx+dy*dy <= radius*radius else None
    disc = dot*dot-v2*(dx*dx+dy*dy-radius*radius)
    if entry is None and v2 and disc >= 0:
        value = (-dot-math.sqrt(disc))/v2
        if 0 <= value <= horizon: entry = value
    return {'minimum_gap': round(gap, 2), 'closest_frame': round(t, 2),
            'first_contact_frame': round(entry, 2) if entry is not None else None, 'closing': dot < 0}


def semantic_history_summary(frame, history):
    from .features import history_summary
    summaries = history_summary(frame, history)
    past = {f['game_frame']: f for f in history if f['run_id'] == frame['run_id'] and f['room']['id'] == frame['room']['id']
            and f.get('mode') == frame.get('mode') and f['game_frame'] < frame['game_frame']}
    for row in summaries:
        for key in ('move', 'shoot', 'use_active'): row.pop(key, None)
        old = past.get(frame['game_frame']-row['age_frames'])
        if row.get('available') and old:
            row['enemy_hp'] = [[e.get('uid'), e.get('type'), e.get('variant'), e.get('max_hp'), e.get('hp')]
                               for e in old.get('entities', []) if e.get('kind') == 'enemy' and not e.get('friendly')]
    return summaries


def recent_progress(frame, summaries):
    current = {e.get('uid'): e for e in frame.get('entities', []) if e.get('kind') == 'enemy' and not e.get('friendly')}
    results = []
    for row in summaries:
        changes = []
        for uid, kind, variant, maximum, hp in row.get('enemy_hp', []):
            e = current.get(uid)
            if (e and (kind, variant, maximum) == (e.get('type'), e.get('variant'), e.get('max_hp'))
                    and finite(hp) is not None and finite(e.get('hp')) is not None):
                changes.append(hp-e['hp'])
        results.append({'age_frames': row['age_frames'], 'known': bool(changes),
                        'net_enemy_hp_drop': round(sum(changes), 2) if changes else None,
                        'attribution': 'unknown'})
    return results


def geometry(frame):
    room = frame['room']; grids = {g.get('index'): g for g in room.get('grid', [])}
    objects = [(Obstacle(xy(g['position']), (20, 20), g.get('kind', 'grid')), g.get('index'))
               for g in room.get('grid', []) if valid_vector(g.get('position')) and g.get('kind') != 'door'
               and (g.get('collision') or g.get('kind') in {'pit', 'poop', 'rock', 'wall', 'tnt'})]
    for c in room.get('navigation_cells', []):
        if len(c) >= 4 and c[0] not in grids and (c[3] or len(c) > 4 and not c[4]):
            objects.append((Obstacle((c[1], c[2]), (20, 20), 'room_void' if len(c)>4 and not c[4] else 'pit' if c[3]==1 else 'grid'), c[0]))
    return objects


def facts(frame, summaries):
    legacy_history = [{k: v for k, v in row.items() if k != 'enemy_hp'} for row in summaries]
    old_prompt, old_values, old_names = legacy_observation(frame, (), 'positive', legacy_history)
    source = json.loads(old_prompt.split('\nCurrent state:\n', 1)[1].split('\nDesired outcome condition:', 1)[0])
    numbers = dict(zip(old_names, old_values)); p = frame['player']; origin = xy(p['position'])
    pv = xy(p.get('velocity', {})); pr = max(player_radius(frame)); weapon = weapon_state(frame)
    entities = [e for e in frame.get('entities', []) if e.get('kind') in {'enemy', 'projectile'} and not e.get('friendly')
                and not e.get('dead') and valid_vector(e.get('position'))]
    enemies = sorted([e for e in entities if e.get('kind') == 'enemy'], key=lambda e: (math.dist(origin, xy(e['position'])), str(e.get('uid'))))
    targets = enemies[:4]; grids = geometry(frame); fires = fire_obstacles(frame)
    threats = []
    for e in entities:
        relative = tuple(a-b for a,b in zip(xy(e['position']), origin))
        radius = finite(e.get('size')); vel = xy(e.get('velocity', {}))
        estimate = motion_gap(relative, (vel[0]-pv[0], vel[1]-pv[1]), pr+radius) if radius and valid_vector(e.get('velocity')) else None
        threats.append({'uid': e.get('uid'), 'kind': 'fireplace' if e.get('type') == 33 else e.get('kind'),
                        'type': e.get('type'), 'dx': round(relative[0], 1), 'dy': round(relative[1], 1),
                        'vulnerable': e.get('vulnerable'), 'motion': estimate})
    threats.sort(key=lambda e: ((e['motion'] or {}).get('minimum_gap', 1e9), str(e['uid'])))
    target_facts = []; option_rows = []; move_rows = []; speed = max(1., 4*finite(p.get('stats', {}).get('move_speed'), 1))
    range_value = finite(p.get('stats', {}).get('tear_range'), finite(frame.get('planner_stats', {}).get('tear_range')))
    has_geometry = bool(frame['room'].get('navigation_cells')) and all(len(c)>4 for c in frame['room'].get('navigation_cells', []))
    for e in targets:
        ex, ey = xy(e['position'])
        target_facts.append([e.get('uid'), e.get('type'), e.get('variant'), round(ex-origin[0],1), round(ey-origin[1],1), e.get('vulnerable')])
    for m, (dx, dy) in enumerate(MOVES):
        length = math.hypot(dx, dy) or 1; mv = (dx/length*speed, dy/length*speed)
        start = (origin[0]+pv[0]*4, origin[1]+pv[1]*4)
        endpoint = (start[0]+mv[0]*3, start[1]+mv[1]*3)
        gaps = {'enemy': [], 'projectile': []}
        for e in entities:
            radius = finite(e.get('size'))
            if radius and valid_vector(e.get('velocity')):
                v = xy(e['velocity']); pos = xy(e['position'])
                relative = (pos[0]+v[0]*4-start[0], pos[1]+v[1]*4-start[1])
                gaps[e['kind']].append(motion_gap(relative, (v[0]-mv[0], v[1]-mv[1]), pr+radius)['minimum_gap'])
        contact_gap = min(gaps['enemy'], default=320.); projectile_gap = min(gaps['projectile'], default=320.)
        clearance = numbers.get('terrain_'+MOVE_NAMES[m]+'_clearance', 1)*80 if m else 80.
        hazard = bool(numbers.get('hazard_'+MOVE_NAMES[m]+'_blocked', 0))
        fire = any(o.blocks(origin, endpoint) for o in fires)
        predicted = []
        for e in targets:
            pos = xy(e['position']); ev = xy(e.get('velocity', {})); target = (pos[0]+ev[0]*7, pos[1]+ev[1]*7)
            rel = (target[0]-endpoint[0], target[1]-endpoint[1]); radius = finite(e.get('size'), 10)
            # Describes center-line grid intersections, not attack blocking.
            count = sum(o.entry(endpoint, target) is not None and o.gap(endpoint)>0 and o.gap(target)>0 for o, _ in grids)
            predicted.append((e, rel, radius, count))
        shooting = []
        for s, direction in enumerate(SHOT_NAMES):
            forward = []
            for e, rel, radius, count in predicted:
                if s == 0: continue
                axis, sign = (1, -1) if direction == 'up' else (1, 1) if direction == 'down' else (0, -1) if direction == 'left' else (0, 1)
                along, lateral = rel[axis]*sign, abs(rel[1-axis])
                if along > 0 and e.get('vulnerable') is not False:
                    forward.append((along, lateral-radius, count))
            aligned = [r for r in forward if r[1] <= 8] # explicit geometric tolerance, not hit probability
            best = min(forward, key=lambda r: (max(0,r[1]), r[0]), default=(320., 160., 0))
            relation = range_value-best[0] if range_value is not None and forward else 0.
            vector = [clearance/80, max(-4,min(4,contact_gap/100)), max(-4,min(4,projectile_gap/100)),
                      float(hazard), float(fire), min(4,len(aligned)/4), max(-4,min(4,relation/400)),
                      max(-4,min(4,best[1]/100)), min(4,best[2]/4), min(4,best[0]/400),
                      sum(e.get('vulnerable') is True for e in targets)/4, float(has_geometry and bool(forward))]
            option_rows.append(vector)
            shooting.append([direction, len(aligned), round(best[1],1) if forward else None,
                             round(relation,1) if range_value is not None and forward else None, best[2] if forward else None])
        move_rows.append([MOVE_NAMES[m], round(clearance,1), round(contact_gap,1), round(projectile_gap,1), hazard, fire, shooting])
    # Keep native UID only in the recorder/history association, not in LLM text.
    ordered_uids = list(dict.fromkeys([row[0] for row in target_facts] + [row['uid'] for row in threats]))
    aliases = {uid: 'entity_'+str(index) for index,uid in enumerate(ordered_uids)}
    for row in target_facts:row[0]=aliases[row[0]]
    for row in threats:row['uid']=aliases[row['uid']]
    build = source.get('build', {}); effects = build.get('items', [])
    state = {'player': source['player'], 'weapon': weapon, 'known_effects': effects[:8],
             'effects_omitted': max(0,len(effects)-8), 'unknown_items': build.get('other_items', [])[:8],
             'targets': {'columns': ['uid','type','variant','dx','dy','vulnerable'], 'rows': target_facts, 'omitted': max(0,len(enemies)-4)},
             'immediate_threats': threats[:8], 'threats_omitted': max(0,len(threats)-8),
             'moves': {'columns': ['move','static_clearance','contact_gap','projectile_gap','area_hazard','fire_hazard','shoot_relations'],
                       'shoot_columns': ['shoot','aligned_proxy_count','lateral_margin','raw_range_margin','grid_intersections'], 'rows': move_rows},
             'recent_progress': recent_progress(frame, summaries),
             'history': legacy_history, 'areas': source.get('hazards'), 'familiars': source.get('familiars'),
             'geometry_known': has_geometry, 'last_attack_failure_reason': None,
             'limits': 'Grid intersections do not account for attack penetration. Contact damage amounts and invulnerability cooldown unknown. '
                       'Complex attack range/trajectory may differ from raw TearRange. All 45 controls remain options.'}
    names, values = [], []
    for name, value in zip(old_names, old_values):
        if not name.startswith('condition_'): names.append(name); values.append(value)
    for row in state['recent_progress']:
        names += ['progress_'+str(row['age_frames'])+'_known', 'progress_'+str(row['age_frames'])+'_hp_drop']
        values += [float(row['known']), max(-4,min(4,(row['net_enemy_hp_drop'] or 0)/50))]
    for action, vector in enumerate(option_rows):
        for field, value in zip(CANDIDATE_FIELDS, vector): names.append(f'option_{action}_{field}'); values.append(value)
    return state, values, names


def observation(frame, history=(), condition='safe_progress', history_facts=None, profile=PREFIX+'s01'):
    profile_key(profile)
    if condition != 'safe_progress': raise ValueError('V007 always uses the safe_progress objective')
    summaries = semantic_history_summary(frame, history) if history_facts is None else history_facts
    state, numeric, names = facts(frame, summaries)
    text = RULES+'\nCurrent semantic state:\n'+json.dumps(compact_facts(state), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return text, numeric, names
