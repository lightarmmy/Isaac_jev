"""Exclude demonstrations that depend on controls absent from the combat policy."""
from .rooms import combat_active

UNSUPPORTED_ACTION_FILTER = "combat-bomb-room-v1"


def find_combat_bomb_rooms(messages):
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
        during_combat = combat_active(message) or bool(adjacent and combat_active(preceding))
        if manual and pressed and during_combat:
            excluded.add((message["run_id"], message["room"]["id"]))
            triggers += int(bool(inputs.get("bomb_trigger")))
        preceding = message
    return excluded, triggers
