"""Offline input design preview; no model requests, action choice, training or game input.

Reuses current V005 geometry for comparison, adds observed-motion threat relations
and unexpanded grid-intersection facts. This is not a deployable feature contract.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from isaac_jev.actions import MOVE_NAMES, MOVES
from isaac_jev.experiments import unpack_rows
from isaac_jev.features_v005 import observation
from isaac_jev.navigation import fire_obstacles
from isaac_jev.terrain import Obstacle, player_radius, xy
from isaac_jev.weapons import weapon_state

VERSION = "terrablind-transfer-offline-preview-v1"
PRINCIPLES = (
    "Survive while making useful progress against enemies. Compare immediate danger, "
    "escape room and useful attack access together. When attack access is poor, compare "
    "repositioning opportunities; approaching an enemy is useful only when it improves "
    "attack access without entering contact danger. Holding a safe effective firing "
    "position or waiting for a required charge can be reasonable. Interpret geometric "
    "obstructions using the current attack capabilities. Unknown mechanisms stay unknown. "
    "Movement and shooting may occur together. Estimates are not confirmed future hits."
)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def known_vector(value):
    return isinstance(value, dict) and all(finite_number(value.get(k)) for k in ("x", "y"))


def motion_relation(relative, velocity, radius, horizon=6):
    """Exact constant-velocity circle sweep; the choice of circle is an approximation."""
    dx, dy = relative
    vx, vy = velocity
    speed2 = vx * vx + vy * vy
    dot = dx * vx + dy * vy
    closest_at = max(0., min(float(horizon), -dot / speed2)) if speed2 else 0.
    gap = math.hypot(dx + vx * closest_at, dy + vy * closest_at) - radius
    c = dx * dx + dy * dy - radius * radius
    contact = 0. if c <= 0 else None
    if contact is None and speed2:
        discriminant = dot * dot - speed2 * c
        if discriminant >= 0:
            entry = (-dot - math.sqrt(discriminant)) / speed2
            if 0 <= entry <= horizon:
                contact = entry
    return {"closing_now": dot < 0, "closest_at_frames_estimate": round(closest_at, 2),
            "minimum_gap_px_estimate": round(gap, 2),
            "first_contact_frames_estimate": round(contact, 2) if contact is not None else None}


def threat_relations(frame):
    player = frame["player"]
    position = xy(player["position"])
    fires = {obstacle.center for obstacle in fire_obstacles(frame)}
    rows = []
    for entity in frame.get("entities", []):
        if entity.get("kind") not in {"enemy", "projectile"} or entity.get("friendly") or entity.get("dead"):
            continue
        if not known_vector(entity.get("position")):
            continue
        relative = tuple(a - b for a, b in zip(xy(entity["position"]), position))
        row = {"uid": entity.get("uid"), "kind": entity.get("kind"), "type": entity.get("type"),
               "semantic_kind": "live_fireplace_contact_hazard" if xy(entity["position"]) in fires else entity.get("kind"),
               "variant": entity.get("variant"), "dx_px": round(relative[0], 1), "dy_px": round(relative[1], 1),
               "distance_px": round(math.hypot(*relative), 1), "vulnerable": entity.get("vulnerable"),
               "active_enemy": entity.get("active_enemy"), "collision_class": entity.get("collision_class"),
               "contact_damage": None, "motion_estimate": None}
        size = entity.get("size")
        multi = entity.get("size_multi") or {}
        if (finite_number(size) and size > 0 and known_vector(entity.get("velocity"))
                and known_vector(player.get("velocity")) and finite_number(player.get("size"))):
            scale_values = [multi.get(k, 1) for k in ("x", "y")]
            if all(finite_number(value) and value > 0 for value in scale_values):
                radius = max(player_radius(frame)) + size * max(1, *scale_values)
                velocity = tuple(a - b for a, b in zip(xy(entity["velocity"]), xy(player["velocity"])))
                row["motion_estimate"] = motion_relation(relative, velocity, radius)
        rows.append(row)
    rows.sort(key=lambda row: (
        row["motion_estimate"] is None,
        (row["motion_estimate"] or {}).get("minimum_gap_px_estimate", 1e9), row["distance_px"], str(row["uid"])))
    return {"evaluated_count": len(rows), "shown": rows[:8], "omitted_count": max(0, len(rows) - 8),
            "horizon_frames": 6,
            "assumption": "Observed velocities remain constant; conservative circle extents; geometry is not confirmed damage. "
                          "No inertia, future turn, projectile height or pending-input rollout is simulated. "
                          "Contact damage amounts are absent from current capture."}


def attack_relations(frame):
    """Describe intervening geometry without deciding whether a weapon is blocked."""
    player = frame["player"]
    origin = xy(player["position"])
    room = frame["room"]
    grids = {g.get("index"): g for g in room.get("grid", []) if g.get("index") is not None}
    geometry = [(Obstacle(xy(g["position"]), (20, 20), g.get("kind", "unknown_grid")), g.get("index"))
                for g in room.get("grid", []) if known_vector(g.get("position")) and g.get("kind") != "door"
                and (g.get("collision") or g.get("kind") in {"pit", "rock", "poop", "tnt", "wall"})]
    for cell in room.get("navigation_cells", []):
        if len(cell) < 4 or cell[0] in grids:
            continue
        outside = len(cell) > 4 and not cell[4]
        if outside or cell[3]:
            kind = "room_void" if outside else "pit" if cell[3] == 1 else "unknown_grid"
            geometry.append((Obstacle((cell[1], cell[2]), (20, 20), kind), cell[0]))
    enemies = [e for e in frame.get("entities", []) if e.get("kind") == "enemy"
               and not e.get("friendly") and not e.get("dead") and known_vector(e.get("position"))]
    enemies.sort(key=lambda e: (math.dist(origin, xy(e["position"])), str(e.get("uid", ""))))
    rows = []
    for entity in enemies[:4]:
        target = xy(entity["position"])
        dx, dy = round(target[0] - origin[0], 1), round(target[1] - origin[1], 1)
        intersections = []
        for obstacle, index in geometry:
            entry = obstacle.entry(origin, target)
            # Endpoint containment is distinct from an intervening obstacle.
            if entry is not None and obstacle.gap(origin) > 0 and obstacle.gap(target) > 0:
                intersections.append({"kind": obstacle.kind, "grid_index": index,
                                      "entry_distance_px": round(entry * math.dist(origin, target), 1)})
        intersections.sort(key=lambda item: item["entry_distance_px"])
        rows.append({"uid": entity.get("uid"), "type": entity.get("type"), "variant": entity.get("variant"),
                     "vulnerable": entity.get("vulnerable"), "distance_px": round(math.hypot(dx, dy), 1),
                     "cardinal_offsets_px": {"left": [-dx, abs(dy)], "right": [dx, abs(dy)],
                                             "up": [-dy, abs(dx)], "down": [dy, abs(dx)]},
                     "intervening_grid_geometry": intersections[:4], "grid_intersections_omitted": max(0, len(intersections) - 4),
                     "can_hit_now": None, "move_then_attack": None})
    return {"targets": rows, "omitted_targets": max(0, len(enemies) - 4),
            "offset_columns": ["forward_distance_px", "lateral_offset_px"],
            "geometry_known": bool(room.get("navigation_cells")) and all(len(c) > 4 for c in room.get("navigation_cells", [])),
            "assumption": "Straight player-center to target-center line, unexpanded 40px grid boxes. "
                          "Intervening pits/rocks/walls are geometric facts, not shared attack blockers. "
                          "Weapon trajectory, firing width, penetration, target motion and alternative attack-position paths remain unverified."}


def preview(frame, history_facts=None):
    before = time.perf_counter()
    # All existing features are computed under their legacy contract for comparison.
    prompt, numeric, names = observation(frame, (), "positive", history_facts)
    facts = json.loads(prompt.split("\nCurrent state:\n", 1)[1].split("\nDesired outcome condition:", 1)[0])
    numbers = dict(zip(names, numeric))
    history = [{k: v for k, v in item.items() if k in {
        "age_frames", "available", "player_dx", "player_dy", "vx", "vy", "health",
        "enemy_count", "projectile_count", "nearest_enemy", "nearest_projectiles", "weapon_charge_ratio"}}
        for item in unpack_rows(facts.get("history", []))]
    weapon = weapon_state(frame)
    fires = fire_obstacles(frame)
    origin = xy(frame["player"]["position"])
    fire_queries = {}
    for name, (dx, dy) in zip(MOVE_NAMES, MOVES):
        length = math.hypot(dx, dy) or 1
        endpoint = (origin[0] + dx / length * 40, origin[1] + dy / length * 40)
        fire_queries[name] = any(obstacle.blocks(origin, endpoint) for obstacle in fires)
    state = {"units": "Positions/clearance in pixels, dx right positive, dy down positive; velocities pixels per 30Hz logic frame.",
             "player": facts["player"],
             "capabilities": {"weapon": weapon, "trigger_source": "type_and_inventory_inference",
                              "weapon_effects": (facts.get("build") or {}).get("items", []),
                              "unknown_items": (facts.get("build") or {}).get("other_items", []),
                              "attack_penetration": None, "range_semantics": "Raw tear_range is not verified for every attack behavior."},
             "immediate_threats": threat_relations(frame), "attack_relations": attack_relations(frame),
             "known_area_hazards": facts.get("hazards"), "area_hazard_directions": facts.get("hazard_directions"),
             "familiars": facts.get("familiars"), "history": history,
             "recent_attack_feedback": {"state": "unknown", "reason": "Current schema does not record a causal last-attack failure reason."},
             "movement_options": [{"id": name,
                "static_clearance_px_estimate": round(numbers.get("terrain_" + name + "_clearance", 0) * 80, 1) if name != "stay" else None,
                "legacy_candidate_min_gap_px_estimate": round(numbers.get(name + "_threat_gap", 0) * 100, 1),
                "known_area_hazard_intersection_estimate": bool(numbers.get("hazard_" + name + "_blocked", 0)),
                "fire_contact_intersection_estimate": fire_queries[name]}
                 for name in MOVE_NAMES],
             "movement_option_assumptions": "Legacy constant candidate speed 4*move_speed, immediate direction change, 6-frame threat horizon; "
                 "static clearance clipped at 80px; hazard query uses a 40px segment. Different horizons are not a calibrated rollout. "
                 "All nine options remain present, including risky ones; no action is recommended or intercepted."}
    duration = (time.perf_counter() - before) * 1000
    return {"preview_version": VERSION, "instructions": PRINCIPLES, "state": state,
            "audit": {"room_id": frame["room"]["id"], "game_frame": frame["game_frame"],
                      "build_ms": round(duration, 3), "old_numeric_size": len(numeric),
                      "legacy_v005_text_characters": len(prompt),
                      "future_labels_in_input": False, "deployable_contract": False,
                      "scope": "Offline text preview only; no API call or gameplay input."}}


def render_text(result):
    """Compact comparison text; retain full evidence in the accompanying JSON."""
    state = result["state"]
    weapon = state["capabilities"]["weapon"]
    stats = state["player"].get("stats", {})
    def value(item):
        if item is None:
            return "unknown"
        if isinstance(item, bool):
            return "yes" if item else "no"
        return str(round(item, 2)) if isinstance(item, float) else str(item)
    lines = [PRINCIPLES, "", "Units: px; dx right+, dy down+; logic 30Hz. Threat horizon 6 frames.",
             f"Player: health={state['player'].get('health')}; can_fly={value(state['player'].get('can_fly'))}; "
             f"velocity=({value(state['player'].get('vx'))},{value(state['player'].get('vy'))}); "
             f"damage={value(stats.get('damage'))}; move_speed={value(stats.get('move_speed'))}.",
             f"Weapon: {weapon['name']}; trigger={weapon['trigger']} (inferred); "
             f"charge={value(weapon.get('charge_ratio'))}; holding={value(weapon.get('holding'))}; "
             f"raw tear_range={value(stats.get('tear_range'))} (not a universal weapon range).",
             "Attack penetration/last attack failure reason: unknown."]
    items = state["capabilities"]["weapon_effects"]
    for item in items[:6]:
        lines.append(f"Known effect: {item[1]}: {item[2]}")
    if len(items) > 6:
        lines.append(f"Additional known effects omitted from text: {len(items) - 6}.")
    familiars = state.get("familiars") or {}
    if familiars.get("count"):
        lines.append(f"Familiars: count={familiars['count']}; variant/dx/dy/vx/vy/state/charge={familiars.get('nearest')}; roles unverified.")
    lines.append("Threats (observed motion; conservative circles, not confirmed damage):")
    for threat in state["immediate_threats"]["shown"][:6]:
        estimate = threat["motion_estimate"]
        relation = "motion estimate unknown" if estimate is None else (
            f"closing={value(estimate['closing_now'])}; minimum gap={estimate['minimum_gap_px_estimate']}px; "
            + (f"first circle contact={estimate['first_contact_frames_estimate']} frames" if estimate['first_contact_frames_estimate'] is not None
               else "no circle contact within 6 frames"))
        lines.append(f"{threat['uid']} {threat['semantic_kind']}: dx={threat['dx_px']},dy={threat['dy_px']}; {relation}.")
    omitted = state["immediate_threats"]["evaluated_count"] - min(6, len(state["immediate_threats"]["shown"]))
    if omitted:
        lines.append(f"Additional potential threats omitted: {omitted}.")
    lines.append("Attack geometry (not guaranteed hits; interpret terrain using capabilities; "
                 f"native geometry known={value(state['attack_relations']['geometry_known'])}):")
    for target in state["attack_relations"]["targets"]:
        offsets = "; ".join(f"{direction} forward={pair[0]},lateral={pair[1]}"
                            for direction, pair in target["cardinal_offsets_px"].items() if pair[0] > 0)
        intersections = ",".join(item["kind"] for item in target["intervening_grid_geometry"]) or "none observed"
        lines.append(f"{target['uid']}: vulnerable={value(target['vulnerable'])}; {offsets}; intervening grid={intersections}.")
    lines.append("Movement facts: option | static clearance<=80px | legacy threat gap | area hazard | fire.")
    for row in state["movement_options"]:
        lines.append(f"{row['id']} | {value(row['static_clearance_px_estimate'])} | "
                     f"{row['legacy_candidate_min_gap_px_estimate']} | "
                     f"{value(row['known_area_hazard_intersection_estimate'])} | {value(row['fire_contact_intersection_estimate'])}")
    lines.append("Legacy move estimates assume instant turn at 4*move_speed for 6 frames; hazard queries use 40px. Inertia/delay not simulated.")
    hazard = state.get("known_area_hazards") or {}
    for row in hazard.get("nearest", [])[:4]:
        lines.append(f"Area observation: {row[0]} dx={row[1]},dy={row[2]}; harmful={value(row[4])}; extent={row[-1]}.")
    if hazard.get("omitted") or hazard.get("unknown_effects"):
        lines.append(f"Area hazards omitted={hazard.get('omitted', 0)}; unclassified effects={hazard.get('unknown_effects', 0)}.")
    lines.append("History (observations only):")
    for row in state["history"]:
        if row.get("available"):
            lines.append(f"age={row['age_frames']} frames: observed player delta=({row.get('player_dx')},{row.get('player_dy')}); "
                         f"enemies={row.get('enemy_count')},projectiles={row.get('projectile_count')}; health={row.get('health')}.")
        else:
            lines.append(f"age={row['age_frames']} frames: unavailable.")
    lines.append("Unverified: attack-position paths, movement-dependent hit opportunities, damage attribution. All 9 moves remain options.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Recorded frame/bundle JSON, or a list of frame bundles.")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True, help="Output prefix for .json and .txt previews.")
    args = parser.parse_args()
    bundle = json.loads(args.source.read_text(encoding="utf-8-sig"))
    if isinstance(bundle, list):
        bundle = bundle[args.index]
    frame = copy.deepcopy(bundle.get("frame", bundle))
    result = preview(frame, bundle.get("policy_history"))
    result["audit"]["source"] = str(args.source)
    result["audit"]["source_sha256"] = hashlib.sha256(args.source.read_bytes()).hexdigest()
    result["audit"]["source_index"] = args.index
    text = render_text(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    args.output.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    print(json.dumps({**result["audit"], "text_characters": len(text), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
