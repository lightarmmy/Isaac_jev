"""Versioned, Torch-free contracts and observation transforms for nine controlled experiments."""
import hashlib
import json

PREFIX = 'combat-experiment-v6-'
EXPERIMENTS = {
    'e01': {'name': 'V005 baseline', 'label_scheme': 'original', 'prompt_scheme': 'full', 'head_design': 'factorized'},
    'e02': {'name': 'Short injury attribution', 'label_scheme': 'short12', 'prompt_scheme': 'full', 'head_design': 'factorized'},
    'e03': {'name': 'Safety and progress weights', 'label_scheme': 'dual_quality', 'prompt_scheme': 'full', 'head_design': 'factorized'},
    'e04': {'name': 'Quality and episode balancing', 'label_scheme': 'dual_balanced', 'prompt_scheme': 'full', 'head_design': 'factorized'},
    'e05': {'name': 'Threat-focused text', 'label_scheme': 'original', 'prompt_scheme': 'focused', 'head_design': 'factorized'},
    'e06': {'name': 'Observed tactical summaries', 'label_scheme': 'original', 'prompt_scheme': 'tactical', 'head_design': 'factorized'},
    'e07': {'name': 'Move gate and eight directions', 'label_scheme': 'original', 'prompt_scheme': 'full', 'head_design': 'hierarchical'},
    'e08': {'name': 'Separate move and shoot towers', 'label_scheme': 'original', 'prompt_scheme': 'full', 'head_design': 'decoupled'},
    'e09': {'name': 'Combined quality, tactics and gate', 'label_scheme': 'dual_balanced', 'prompt_scheme': 'tactical', 'head_design': 'hierarchical'},
}
ARCHITECTURES = {'factorized': 'two-tower-factorized-v2', 'hierarchical': 'two-tower-move-gate-v1',
                 'decoupled': 'separate-move-shoot-towers-v1'}
FOCUSED_RULES = ('Isaac combat controls: movement and shooting are separate. Avoid enemy contact, projectiles and harmful areas '
                 'while maintaining useful fire. Standing can be appropriate during effective firing, charging or safe waiting. '
                 'Unknown fields remain unknown. No active items. No future states or past control labels are available.')
TACTICAL_RULES = FOCUSED_RULES + (' Candidate geometric gaps use a short linear estimate, not a simulator or a guarantee of safety. '
                                'Use observed terrain, threats, weapon range and firing alignment together.')


def specification(profile):
    if not isinstance(profile, str) or not profile.startswith(PREFIX) or profile[len(PREFIX):] not in EXPERIMENTS:
        raise ValueError('Unknown experiment profile')
    key = profile[len(PREFIX):]
    return {'id': key, **EXPERIMENTS[key]}


def experiment_contract(base, profile):
    from .features_v005 import contract as v005_contract, RULES
    spec = specification(profile)
    rules = {'full': RULES, 'focused': FOCUSED_RULES, 'tactical': TACTICAL_RULES}[spec['prompt_scheme']]
    return {**v005_contract(base), 'feature_version': profile,
            'rules_sha256': hashlib.sha256(rules.encode()).hexdigest(),
            'head_architecture': ARCHITECTURES[spec['head_design']],
            'experiment_id': spec['id'], 'prompt_scheme': spec['prompt_scheme'],
            'label_scheme': spec['label_scheme'], 'head_design': spec['head_design'],
            'outcome_version': 'observed-damage-short12-v1' if spec['label_scheme']=='short12' else 'observed-damage-quality-weights-v1' if spec['label_scheme'].startswith('dual_') else base['outcome_version'],
            'experiment_contract_version': 'controlled-nine-v1'}


def unpack_rows(value):
    if isinstance(value, dict) and 'fields' in value and 'rows' in value:
        return [dict(zip(value['fields'], row)) for row in value['rows']]
    return value if isinstance(value, list) else []


def transform_prompt(prompt, numeric, names, scheme):
    if scheme == 'full':
        return prompt
    if scheme not in {'focused', 'tactical'}:
        raise ValueError('Unknown prompt scheme')
    _, text = prompt.split('\nCurrent state:\n', 1)
    text, ending = text.split('\nDesired outcome condition:', 1)
    source = json.loads(text)
    facts = {k: source[k] for k in ('player', 'enemies', 'projectile_count', 'projectiles', 'projectiles_omitted',
                                   'hazards', 'hazard_directions', 'history', 'familiars') if k in source}
    build = source.get('build', {})
    facts['build'] = {'weapon': build.get('weapon'), 'item_names': [row[:2] for row in build.get('items', [])],
                      'unknown_items': build.get('other_items', []), 'knowledge_missing': build.get('knowledge_missing', True)}
    terrain = source.get('terrain', {})
    facts['terrain'] = {k: terrain[k] for k in ('shape', 'mask_known', 'local_occupancy_7x7', 'directions',
                                              'player_rel', 'room_bounds_rel') if k in terrain}
    if scheme == 'tactical':
        from .actions import MOVE_NAMES
        values = dict(zip(names, numeric))
        facts['candidate_moves'] = {'fields': ['move', 'threat_gap_px_estimate', 'terrain_clearance_px', 'hazard_blocked'],
            'rows': [[move, round(values.get(move + '_threat_gap', 0) * 100, 1),
                      round(values.get('terrain_' + move + '_clearance', 0) * 80, 1),
                      bool(values.get('hazard_' + move + '_blocked', 0))] for move in MOVE_NAMES]}
        enemies = unpack_rows(source.get('enemies', []))
        weapon_range = source.get('player', {}).get('stats', {}).get('tear_range')
        facts['shoot_alignment_estimate'] = []
        for direction, axis, sign in [('left', 'dx', -1), ('right', 'dx', 1), ('up', 'dy', -1), ('down', 'dy', 1)]:
            other = 'dy' if axis == 'dx' else 'dx'
            aligned = [e for e in enemies if e.get('vulnerable') is not False and e.get(axis, 0) * sign > 0
                       and abs(e.get(other, 9999)) <= 24]
            ranged = sum(abs(e[axis]) <= weapon_range for e in aligned) if isinstance(weapon_range, (int, float)) else None
            facts['shoot_alignment_estimate'].append([direction, len(aligned), ranged])
    rules = FOCUSED_RULES if scheme == 'focused' else TACTICAL_RULES
    return rules + '\nCurrent state:\n' + json.dumps(facts, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\nDesired outcome condition:' + ending


def observation(frame, history, condition, history_facts, profile):
    from .features_v005 import observation as v005_observation
    prompt, numeric, names = v005_observation(frame, history, condition, history_facts)
    return transform_prompt(prompt, numeric, names, specification(profile)['prompt_scheme']), numeric, names
