"""Exclude demonstrations that depend on controls absent from the combat policy."""
from .rooms import combat_active

UNSUPPORTED_ACTION_FILTER = "combat-bomb-room-v1"
ACTIVE_ITEM_FILTER = "active-input-or-use-room-v1"


def find_combat_bomb_rooms(messages,combat_fn=combat_active):
    """Scan full-rate data before sampling; exclude a physical room across visits/sessions."""
    excluded = set()
    triggers = 0
    preceding = None
    for message in messages:
        if message.get("type") == "event" and message.get("event") == "run_started":
            preceding = None
        if message.get("type") != "frame":continue
        inputs = message.get("human_input", {})
        manual = message.get("mode") == "manual"
        pressed = bool(inputs.get("bomb_trigger") or inputs.get("bomb_pressed"))
        adjacent = bool(preceding and preceding.get("_recording_session") == message.get("_recording_session")
                        and preceding["run_id"] == message["run_id"]
                        and preceding["room"]["id"] == message["room"]["id"]
                        and preceding["game_frame"]+1 == message["game_frame"]
                        and preceding.get("mode") == "manual")
        # A bomb can kill the last enemy before the same frame's snapshot.
        during_combat = combat_fn(message) or bool(adjacent and combat_fn(preceding))
        if manual and pressed and during_combat:
            excluded.add((message["run_id"], message["room"]["id"]))
            triggers += int(bool(inputs.get("bomb_trigger")))
        preceding = message
    return excluded, triggers


def find_active_item_rooms(messages, use_frames):
    """Exclude affected rooms, including teleport source/destination around real uses."""
    excluded=set();attempts=0
    for frame in messages:
        if frame.get("type")!="frame" or frame.get("mode")!="manual":continue
        inputs=frame.get("human_input",{})
        item=frame["player"].get("active",{}).get("id",0)
        attempted=bool(item and (inputs.get("active_trigger") or inputs.get("active_pressed")))
        key=(frame.get("_recording_session"),frame["run_id"],frame["game_frame"])
        next_key=(*key[:2],key[2]+1)
        if attempted or key in use_frames or next_key in use_frames:
            excluded.add((frame["run_id"],frame["room"]["id"]))
            attempts+=int(attempted and bool(inputs.get("active_trigger")))
    return excluded,attempts
