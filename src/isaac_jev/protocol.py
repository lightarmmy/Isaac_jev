"""JSON-line protocol used by the mod, recorder, and local control clients."""
import json
import math

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MODES = {"manual", "shadow", "ai"}

def encode(message: dict) -> bytes:
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

def decode(line: bytes) -> dict:
    if len(line) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    def reject_constant(value):
        raise ValueError(f"non-finite JSON value: {value}")
    msg = json.loads(line, parse_constant=reject_constant)
    if not isinstance(msg, dict):
        raise ValueError("message must be a JSON object")
    if msg.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported protocol_version")
    return msg

def validate_frame(frame: dict) -> None:
    if frame.get("type") != "frame" or frame.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("not a supported frame")
    if not isinstance(frame.get("run_id"), str) or not frame["run_id"]:
        raise ValueError("missing run_id")
    if type(frame.get("game_frame")) is not int or frame["game_frame"] < 0:
        raise ValueError("invalid game_frame")
    if frame.get("mode") not in MODES:
        raise ValueError("invalid control mode")
    if not isinstance(frame.get("player"), dict) or not isinstance(frame.get("room"), dict):
        raise ValueError("missing player/room")
    if not isinstance(frame.get("entities"), list) or not isinstance(frame.get("human_input"), dict):
        raise ValueError("missing entities/human_input")
    if "id" not in frame["room"]:
        raise ValueError("missing room id")

def finite(value, default=0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default
