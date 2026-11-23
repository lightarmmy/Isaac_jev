"""Room boundaries inferred from sampled state, usable on old raw recordings."""
from pathlib import Path
import json
from .protocol import PROTOCOL_VERSION
from .recording import iter_messages

TRAINING_PHASE = "hostile-enemy-or-projectile-pair-v1"


def combat_state(frame):
    entities = frame.get("entities", [])
    hostile = [e for e in entities if not e.get("friendly") and not e.get("dead")]
    enemies = sum(e.get("kind") == "enemy" and e.get("active_enemy") is not False for e in hostile)
    projectiles = sum(e.get("kind") == "projectile" for e in hostile)
    return {"phase": "combat" if enemies or projectiles else "planning",
            "next_controller": "combat_policy" if enemies or projectiles else "planner",
            "hostile_enemy_count": enemies, "hostile_projectile_count": projectiles}


def combat_active(frame):
    return combat_state(frame)["phase"] == "combat"


class RoomTimeline:
    def __init__(self):
        self.previous = None
        self.visit = 0

    def reset(self):
        self.previous = None
        self.visit = 0

    def observe(self, frame):
        previous = self.previous
        same_run = bool(previous and previous["run_id"] == frame["run_id"]
                        and previous["game_frame"] < frame["game_frame"])
        same_room = bool(same_run and previous["room"]["id"] == frame["room"]["id"])
        base = {"protocol_version": PROTOCOL_VERSION, "type": "event", "run_id": frame["run_id"],
                "room_id": frame["room"]["id"], "game_frame": frame["game_frame"],
                "game_time_ms": frame.get("game_time_ms"), "received_at_unix": frame.get("received_at_unix"),
                "source": "observed_frame_transition", "capture_phase": frame.get("capture_phase"),
                "room_clear": bool(frame["room"].get("clear")),
                "previous_observed_frame": previous["game_frame"] if same_run else None,
                "consecutive_frames": bool(same_run and frame["game_frame"] == previous["game_frame"]+1),
                **combat_state(frame)}
        events = []
        if not same_room:
            self.visit += 1
            events.append({**base, "event": "room_entered" if same_run else "room_observed",
                           "visit": self.visit, "previous_room_id": previous["room"]["id"] if same_run else None})
        elif not previous["room"].get("clear") and frame["room"].get("clear"):
            events.append({**base, "event": "room_cleared", "visit": self.visit})
        if same_room and combat_active(previous) != combat_active(frame):
            events.append({**base, "event": "combat_started" if combat_active(frame) else "combat_ended",
                           "visit": self.visit})
        self.previous = frame
        return events


def export_room_timeline(recordings, output):
    recordings, output = Path(recordings), Path(output)
    files = sorted(recordings.glob("*/frames.jsonl.gz")) if not (recordings/"frames.jsonl.gz").exists() else [recordings/"frames.jsonl.gz"]
    output.parent.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    with output.open("w", encoding="utf-8") as destination:
        for file in files:
            metadata_path = file.parent/"session.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if not metadata.get("closed_at_unix"):
                    skipped += 1;continue
                if metadata.get("purpose") == "diagnostic":continue
            timeline = RoomTimeline()
            for message in iter_messages(file):
                if message.get("type") == "event" and message.get("event") == "run_started":timeline.reset()
                if message.get("type") != "frame":continue
                for event in timeline.observe(message):
                    destination.write(json.dumps({**event,"session":file.parent.name},ensure_ascii=False)+"\n")
                    written += 1
    return {"events": written, "open_sessions_skipped": skipped, "output": str(output.resolve())}
