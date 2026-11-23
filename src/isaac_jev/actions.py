"""Stable movement/shooting IDs and an independent active-item trigger."""
from dataclasses import dataclass
import math

MOVES = ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, -1), (-1, 1), (1, 1))
MOVE_NAMES = ("stay", "up", "down", "left", "right", "up_left", "up_right", "down_left", "down_right")
SHOTS = ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0))
SHOT_NAMES = ("none", "up", "down", "left", "right")
ACTION_COUNT = len(MOVES) * len(SHOTS)
ACTION_VERSION = "move9-shoot5-active-edge-v1"

def _sign(value: float) -> int:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("input must be finite")
    return 1 if value > 0.25 else -1 if value < -0.25 else 0

def label_from_input(inputs: dict) -> tuple[int, bool]:
    move, shoot = inputs.get("move", {}), inputs.get("shoot", {})
    m = (_sign(move.get("x", 0)), _sign(move.get("y", 0)))
    sx, sy = float(shoot.get("x", 0)), float(shoot.get("y", 0))
    if not math.isfinite(sx) or not math.isfinite(sy):
        raise ValueError("shoot input must be finite")
    s = (_sign(sx), 0) if abs(sx) >= abs(sy) else (0, _sign(sy))
    return MOVES.index(m) * len(SHOTS) + SHOTS.index(s), bool(inputs.get("active_trigger", False))

@dataclass(frozen=True)
class Action:
    action_id: int
    use_active: bool = False
    hold_frames: int = 3

    def __post_init__(self):
        if type(self.action_id) is not int or not 0 <= self.action_id < ACTION_COUNT:
            raise ValueError("action_id must be an integer in [0, 44]")
        if type(self.use_active) is not bool:
            raise ValueError("use_active must be boolean")
        if type(self.hold_frames) is not int or not 1 <= self.hold_frames <= 6:
            raise ValueError("hold_frames must be in [1, 6]")

    def as_command(self, frame: dict) -> dict:
        move_id, shot_id = divmod(self.action_id, len(SHOTS))
        return {"command": "action", "action_id": self.action_id,
                "move": {"x": MOVES[move_id][0], "y": MOVES[move_id][1]},
                "shoot": {"x": SHOTS[shot_id][0], "y": SHOTS[shot_id][1]},
                "use_active": self.use_active, "hold_frames": self.hold_frames,
                "run_id": frame["run_id"], "room_id": frame["room"]["id"],
                "observation_frame": frame["game_frame"],
                "valid_until_frame": frame["game_frame"] + self.hold_frames}

def action_map() -> list[dict]:
    return [{"id": i, "move": MOVE_NAMES[i // len(SHOTS)], "shoot": SHOT_NAMES[i % len(SHOTS)]}
            for i in range(ACTION_COUNT)]
