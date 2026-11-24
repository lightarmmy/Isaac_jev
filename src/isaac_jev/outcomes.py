"""Conservative retrospective labels, never observable policy facts or causal proof."""
from collections import defaultdict
from .rooms import combat_active

DEFAULT_OUTCOME_WINDOW = 30
RECENT_DAMAGE_FRAMES = 45


def segment_frames(messages):
    """Bound memory by room visit; never stitch sessions, gaps, mode changes or reloads."""
    segment = []
    for frame in messages:
        if frame.get("type") != "frame":
            continue
        eligible = (frame.get("mode") == "manual" and frame.get("capture_phase") == "pre_update"
                    and frame.get("supported_player_count", 1) == 1)
        same = bool(segment and eligible and frame.get("_recording_session") == segment[-1].get("_recording_session")
                    and frame["run_id"] == segment[-1]["run_id"]
                    and frame["room"]["id"] == segment[-1]["room"]["id"]
                    and frame["game_frame"] == segment[-1]["game_frame"] + 1)
        if segment and not same:
            yield segment
            segment = []
        if eligible:
            segment.append(frame)
    if segment:
        yield segment


def event_key(message):
    return (message.get("_recording_session"), message["run_id"], message["game_frame"])


def collect_player_damage(messages):
    damage = defaultdict(list)
    for message in messages:
        if (message.get("type") == "event" and message.get("event") == "damage"
                and message.get("entity_type") == 1 and message.get("amount", 0) > 0):
            damage[event_key(message)].append(message)
    return damage


def health_loss(before, after):
    a, b = before["player"].get("health", {}), after["player"].get("health", {})
    if not all(k in a and k in b for k in ("red", "soul")):
        return None
    if a.get("max_red") != b.get("max_red"):
        return None  # Capacity changes / health conversions are ambiguous.
    return max(0, a["red"] + a["soul"] - b["red"] - b["soul"]) + max(0, a.get("bone", 0)-b.get("bone", 0))


def damage_evidence(segment, callbacks):
    evidence = {}
    for i in range(1, len(segment)):
        before, after = segment[i-1], segment[i]
        events = callbacks.get(event_key(after), [])
        loss = health_loss(before, after)
        died = bool(after["player"].get("dead") and not before["player"].get("dead"))
        if events and (loss or died) and all(e.get("flags") == 0 for e in events):
            evidence[i] = "confirmed_player_damage"
        elif events or loss or died or loss is None:
            evidence[i] = "ambiguous_health_or_damage_event"
    return evidence


def label_outcome(segment, observation_index, evidence, window_frames=DEFAULT_OUTCOME_WINDOW, action_lag_frames=1):
    """Assess a trajectory after the aligned action, without blaming a single keypress."""
    if not 1 <= window_frames <= 150:
        raise ValueError("Outcome window must be 1..150 logic frames")
    if type(action_lag_frames) is not int or not 1<=action_lag_frames<=6:raise ValueError("Action lag must be 1..6 frames")
    action_index = observation_index + action_lag_frames
    label = {"label": "unknown", "weight": 0.25, "window_frames": window_frames,
             "damage_after_action_frames": None, "reason": "incomplete_future_window",
             "causal_claim": False}
    # Same-frame callbacks can precede input sampling; do not assign them to this action.
    end = min(len(segment)-1, action_index + window_frames)
    combat_ended_at = next((i for i in range(action_index, end+1) if not combat_active(segment[i])), None)
    if combat_ended_at is not None:end=combat_ended_at
    ambiguous = action_index in evidence
    for i in range(action_index+1, end+1):
        if evidence.get(i) == "confirmed_player_damage":
            lag = i - action_index
            return {**label, "label": "negative", "weight": round(0.25+0.75*(1-lag/window_frames), 4),
                    "damage_after_action_frames": lag, "reason": "confirmed_damage_in_future_trajectory"}
        ambiguous |= i in evidence
    if end < action_index + window_frames and combat_ended_at is None:
        return label
    if ambiguous:
        return {**label, "reason": "ambiguous_health_or_damage_event"}
    if any(i in evidence for i in range(max(1, observation_index-RECENT_DAMAGE_FRAMES), action_index+1)):
        return {**label, "reason": "recent_damage_possible_invulnerability"}
    observation = segment[observation_index]
    threatened = any(e.get("kind") in {"enemy", "projectile", "laser", "bomb"}
                     and not e.get("friendly") and not e.get("dead") for e in observation.get("entities", []))
    if not threatened:
        return {**label, "reason": "no_observed_threat"}
    if any(not f["player"].get("controls_enabled", True) for f in segment[action_index:end+1]):
        return {**label, "reason": "controls_disabled_in_window"}
    return {**label, "label": "positive", "weight": 1.0,
            "reason": "no_observed_damage_through_combat_end" if combat_ended_at is not None else "complete_window_no_observed_damage_under_threat"}
