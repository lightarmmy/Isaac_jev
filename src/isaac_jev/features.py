"""Bounded policy inputs derived from full raw telemetry; no future/seed features."""
from pathlib import Path
import hashlib
import json
import math
from .actions import MOVES, MOVE_NAMES, SHOT_NAMES, ACTION_VERSION, label_from_input
from .protocol import finite

FEATURE_VERSION = "combat-projectiles-history-conditioned-v3"
PROJECTILE_LIMIT = 8
HISTORY_OFFSETS = (3, 9, 18)
CONDITIONS = ("positive", "negative", "unknown")
DEPLOYMENT_CONDITION = "positive"
RULES = (Path(__file__).parent / "rules" / "combat_v1.txt").read_text(encoding="utf-8")
RULES_SHA256 = hashlib.sha256(RULES.encode()).hexdigest()
ACTIVE_EFFECTS = {
    34: "Book of Belial: increases damage for the current room; use when combat benefit justifies the charge.",
    45: "Yum Heart: restores one red heart, subject to available red heart capacity.",
    58: "Book of Shadows: grants temporary invulnerability; timing matters.",
    105: "D6: rerolls collectible pedestals in the current room; ordinary combat alone is not a reason to use it.",
}

def _xy(obj):
    obj = obj if isinstance(obj, dict) else {}
    return finite(obj.get("x")), finite(obj.get("y"))

def _clip(v, scale):
    return max(-4.0, min(4.0, finite(v) / scale))

def controls(frame):
    if frame.get("mode") in {"manual", "shadow"}:
        action, active = label_from_input(frame.get("human_input", {}))
    else:
        execution = frame.get("execution", {})
        action, active = execution.get("action_id", 0), execution.get("use_active", False)
    return int(action), bool(active)


def history_summary(frame, history):
    """Only select past snapshots from this uninterrupted room visit."""
    past = {f["game_frame"]: f for f in history
            if f["run_id"] == frame["run_id"] and f["room"]["id"] == frame["room"]["id"]
            and f.get("mode") == frame.get("mode") and f["game_frame"] < frame["game_frame"]}
    # A missing logic frame is a boundary, not a state that can be interpolated.
    earliest = frame["game_frame"]
    while earliest - 1 in past:
        earliest -= 1
    px, py = _xy(frame["player"].get("position"))
    summaries = []
    for offset in HISTORY_OFFSETS:
        old = past.get(frame["game_frame"] - offset) if frame["game_frame"] - offset >= earliest else None
        if old is None:
            summaries.append({"age_frames": offset, "available": False})
            continue
        op = old["player"]
        x, y = _xy(op.get("position")); vx, vy = _xy(op.get("velocity"))
        action, active = controls(old)
        threats = [e for e in old.get("entities", []) if e.get("kind") in {"enemy", "projectile"}
                   and not e.get("friendly") and not e.get("dead")]
        enemies = [e for e in threats if e.get("kind") == "enemy"]
        bullets = sorted((e for e in threats if e.get("kind") == "projectile"),
                         key=lambda e: math.hypot(_xy(e.get("position"))[0] - x, _xy(e.get("position"))[1] - y))
        summaries.append({"age_frames": offset, "available": True,
            "player_dx": round(x-px, 1), "player_dy": round(y-py, 1),
            "vx": round(vx, 2), "vy": round(vy, 2), "health": op.get("health", {}),
            "move": MOVE_NAMES[action//5], "shoot": SHOT_NAMES[action%5], "use_active": active,
            "enemy_count": len(enemies), "projectile_count": len(bullets),
            "nearest_enemy": [{"type": e.get("type"), "dx": round(_xy(e.get("position"))[0]-x, 1),
                               "dy": round(_xy(e.get("position"))[1]-y, 1)}
                              for e in sorted(enemies, key=lambda e: math.hypot(_xy(e.get("position"))[0]-x, _xy(e.get("position"))[1]-y))[:1]],
            "nearest_projectiles": [{"dx": round(_xy(e.get("position"))[0]-x, 1),
                                     "dy": round(_xy(e.get("position"))[1]-y, 1),
                                     "vx": round(_xy(e.get("velocity"))[0], 2),
                                     "vy": round(_xy(e.get("velocity"))[1], 2)} for e in bullets[:2]]})
    return summaries


def policy_observation(frame: dict, history=(), condition=DEPLOYMENT_CONDITION, history_facts=None) -> tuple[str, list[float], list[str]]:
    if condition not in CONDITIONS:
        raise ValueError("Unknown outcome condition")
    p, room = frame["player"], frame["room"]
    px, py = _xy(p.get("position"))
    vx, vy = _xy(p.get("velocity"))
    left, top = _xy(room.get("top_left"))
    right, bottom = _xy(room.get("bottom_right"))
    health, stats, active = p.get("health", {}), p.get("stats", {}), p.get("active", {})
    entities = frame.get("entities", [])
    enemies = [e for e in entities if e.get("kind") == "enemy" and not e.get("friendly") and not e.get("dead")]
    bullets = [e for e in entities if e.get("kind") == "projectile" and not e.get("friendly")]
    def distance(e):
        x, y = _xy(e.get("position"))
        return math.hypot(x - px, y - py)
    enemies.sort(key=lambda e: (distance(e), str(e.get("uid", ""))))
    names, values = [], []
    def add(name, value, scale=1.0):
        names.append(name);values.append(_clip(value, scale))
    add("player_x", px-left, max(1,right-left));add("player_y", py-top,max(1,bottom-top))
    for name, value, scale in [("vx",vx,10),("vy",vy,10),("red",health.get("red"),24),
            ("max_red",health.get("max_red"),24),("soul",health.get("soul"),24),
            ("damage",stats.get("damage"),20),("move_speed",stats.get("move_speed"),2),
            ("shot_speed",stats.get("shot_speed"),2),("fire_delay",stats.get("max_fire_delay"),30),
            ("can_fly",bool(p.get("can_fly")),1),("enemies",len(enemies),10),
            ("bullets",len(bullets),30),
            ("active_ready",bool(active.get("ready")),1),("active_charge",active.get("charge"),12)]:add(name,value,scale)
    move_summaries=[]
    harmful=enemies+bullets
    for index,(dx,dy) in enumerate(MOVES):
        # Short geometric summary, not a complete simulator. Keep raw telemetry for auditing this approximation.
        length=math.hypot(dx,dy) or 1
        speed=max(1,4*finite(stats.get("move_speed"),1))
        ux,uy=dx/length*speed,dy/length*speed
        nearest=999.0
        for e in harmful:
            ex,ey=_xy(e.get("position"));evx,evy=_xy(e.get("velocity"))
            rx,ry=ex-px,ey-py;rvx,rvy=evx-ux,evy-uy
            v2=rvx*rvx+rvy*rvy
            t=max(0,min(6,-(rx*rvx+ry*rvy)/v2)) if v2>1e-6 else 0
            gap=math.hypot(rx+rvx*t,ry+rvy*t)-finite(e.get("size"),10)-finite(p.get("size"),10)
            nearest=min(nearest,gap)
        ex,ey=px+ux*3,py+uy*3
        boundary=min(ex-left,right-ex,ey-top,bottom-ey)
        grid_gap=999.0
        for g in room.get("grid", []):
            if finite(g.get("collision"))>0:
                gx,gy=_xy(g.get("position"));grid_gap=min(grid_gap,math.hypot(gx-ex,gy-ey)-20)
        add(f"{MOVE_NAMES[index]}_threat_gap",nearest,100)
        add(f"{MOVE_NAMES[index]}_boundary_gap",boundary,100)
        add(f"{MOVE_NAMES[index]}_grid_gap",grid_gap,100)
        move_summaries.append([MOVE_NAMES[index],round(nearest,1),round(boundary,1),round(grid_gap,1)])
    compact_enemies=[]
    for slot in range(4):
        e=enemies[slot] if slot<len(enemies) else {}
        ex,ey=_xy(e.get("position"));evx,evy=_xy(e.get("velocity"))
        present=bool(e)
        for name,value,scale in [("present",present,1),("dx",ex-px if present else 0,320),
                ("dy",ey-py if present else 0,280),("vx",evx,10),("vy",evy,10),
                ("hp_ratio",finite(e.get("hp"))/max(1,finite(e.get("max_hp"))),1),
                ("state",e.get("state"),32),("vulnerable",bool(e.get("vulnerable")),1)]:add(f"enemy{slot}_{name}",value,scale)
        if present:compact_enemies.append({"type":e.get("type"),"variant":e.get("variant"),
            "dx":round(ex-px,1),"dy":round(ey-py,1),"vx":round(evx,2),"vy":round(evy,2),
            "hp":e.get("hp"),"state":e.get("state"),"animation":e.get("animation"),"vulnerable":e.get("vulnerable")})
    # Projectile IDs/seeds remain outside the policy. Sorting is geometric.
    bullets.sort(key=distance)
    compact_bullets = []
    for slot in range(PROJECTILE_LIMIT):
        e = bullets[slot] if slot < len(bullets) else {}
        bx, by = _xy(e.get("position")); bvx, bvy = _xy(e.get("velocity"))
        for name, value, scale in [("present", bool(e), 1), ("dx", bx-px if e else 0, 320),
                ("dy", by-py if e else 0, 280), ("vx", bvx, 10), ("vy", bvy, 10),
                ("size", e.get("size"), 20), ("height", e.get("height"), 40),
                ("falling_speed", e.get("falling_speed"), 10), ("falling_accel", e.get("falling_accel"), 5)]:
            add(f"projectile{slot}_{name}", value, scale)
        if e:
            compact_bullets.append({"type": e.get("type"), "variant": e.get("variant"),
                "dx": round(bx-px, 1), "dy": round(by-py, 1), "vx": round(bvx, 2), "vy": round(bvy, 2),
                "size": e.get("size"), "height": e.get("height"), "falling_speed": e.get("falling_speed"),
                "falling_accel": e.get("falling_accel"), "flags": e.get("projectile_flags")})
    previous_action, previous_active = controls(frame)
    for i in range(9):add(f"previous_move_{i}", int(previous_action//5 == i))
    for i in range(5):add(f"previous_shoot_{i}", int(previous_action%5 == i))
    add("previous_active",previous_active)
    for label in CONDITIONS:add(f"condition_{label}", int(condition == label))
    active_info={**active,"effect":ACTIVE_EFFECTS.get(int(finite(active.get("id"))),active.get("description","")),
                 "curated_effect":int(finite(active.get("id"))) in ACTIVE_EFFECTS}
    facts={"player":{"vx":vx,"vy":vy,"health":health,"stats":stats,"can_fly":p.get("can_fly")},
           "enemies":compact_enemies,"projectile_count":len(bullets),"projectiles":compact_bullets,
           "projectiles_omitted":max(0,len(bullets)-PROJECTILE_LIMIT),"move_estimates":move_summaries,
           "active_item":active_info,"previous_controls":{"move":MOVE_NAMES[previous_action//5],
               "shoot":SHOT_NAMES[previous_action%5],"use_active":previous_active},
           "history":history_summary(frame, history) if history_facts is None else history_facts,
           "unmodelled_hazards":sum(e.get("kind") in {"laser","bomb","effect"} for e in entities)}
    prompt=RULES+"\nCurrent state:\n"+json.dumps(facts,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    prompt += "\nDesired outcome condition: " + condition + ".\nChoose combat controls."
    return prompt,values,names

def contract():
    return {"feature_version":FEATURE_VERSION,"action_version":ACTION_VERSION,"rules_sha256":RULES_SHA256,
            "raw_capture_phase":"pre_update","movement_risk_horizon_frames":6,"candidate_endpoint_frames":3,
            "observation_to_action_frame_lag":1,"active_labels":"human_edge_and_confirmed_primary_item_use",
            "head_architecture":"factorized-move9-shoot5-active1-v1", "projectile_limit":PROJECTILE_LIMIT,
            "history_offsets_frames":list(HISTORY_OFFSETS), "outcome_version":"observed-damage-window-v1",
            "deployment_condition":DEPLOYMENT_CONDITION, "normalization":"fixed-physical-scales-v1"}
