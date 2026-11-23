"""Local TCP telemetry hub with explicit manual/shadow/AI control."""
import asyncio
from collections import deque
import json
from pathlib import Path
import time
import uuid

from .actions import Action,MAX_OBSERVATION_AGE_FRAMES
from .protocol import MAX_MESSAGE_BYTES, PROTOCOL_VERSION, MODES, decode, encode, validate_frame
from .recording import SessionRecorder

class BridgeServer:
    def __init__(self, host="127.0.0.1", port=9527, record_root: Path | None = None, status_path: Path | None = None, recording_enabled=True, record_purpose="demonstration"):
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("this bridge binds to loopback only")
        self.host, self.port = host, port
        self.record_root = record_root
        self.status_path = status_path
        self.recording_enabled = bool(record_root and recording_enabled)
        self.record_purpose = record_purpose
        self.recorder = self._new_recorder() if self.recording_enabled else None
        self._recording_run = None
        self.latest = None
        self._recent_frames = deque(maxlen=19)
        self.last_received = None
        self.mod_writer = None
        self.hello = None
        self.frames = self.invalid = self.frame_gaps = self.received_events = 0
        self.last_error = None
        self._frame_times = deque(maxlen=120)
        self._last_key = None
        self._acks = {}
        self.server = None

    def _new_recorder(self):
        return SessionRecorder(self.record_root,{"purpose":self.record_purpose,"automatic_boundaries":True})

    async def _seal_recording(self, reason, reopen=False):
        old=self.recorder
        self.recorder=self._new_recorder() if reopen and self.recording_enabled else None
        self._recording_run=None
        if old:
            old.metadata["boundary_reason"]=reason
            await asyncio.to_thread(old.close)

    def recording_message(self):
        return {"protocol_version":PROTOCOL_VERSION,"type":"recording_status",
                "recording":bool(self.recorder and not self.recorder.error),
                "recording_error":bool(self.recorder and (self.recorder.error or self.recorder.dropped))}

    def status(self) -> dict:
        elapsed = self._frame_times[-1] - self._frame_times[0] if len(self._frame_times) > 1 else 0
        latest = self.latest or {}
        return {"connected": bool(self.mod_writer and not self.mod_writer.is_closing()),
                "hello": self.hello, "frames": self.frames, "invalid_messages": self.invalid,
                "frame_gaps": self.frame_gaps, "events": self.received_events,
                "fps": round((len(self._frame_times) - 1) / elapsed, 2) if elapsed else 0,
                "last_frame_age_ms": round((time.monotonic() - self.last_received) * 1000) if self.last_received else None,
                "game_frame": latest.get("game_frame"), "run_id": latest.get("run_id"),
                "mode": latest.get("mode"), "room_id": latest.get("room", {}).get("id"),
                "enemies": sum(e.get("kind") == "enemy" for e in latest.get("entities", [])),
                "recording": str(self.recorder.path.resolve()) if self.recorder else None,
                "recording_enabled":self.recording_enabled,
                "recording_written": self.recorder.written if self.recorder else 0,
                "recording_dropped": self.recorder.dropped if self.recorder else 0,
                "recording_error": self.recorder.error if self.recorder else None,
                "last_error": self.last_error}

    async def start(self):
        self.server = await asyncio.start_server(self._client, self.host, self.port, limit=MAX_MESSAGE_BYTES + 1)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _client(self, reader, writer):
        is_mod = False
        try:
            first = decode(await asyncio.wait_for(reader.readline(), 5))
            if first.get("type") == "control":
                result = await self.control(first.get("payload", {}))
                writer.write(encode({"protocol_version": PROTOCOL_VERSION, "type": "result", **result}))
                await writer.drain()
                return
            if first.get("type") != "hello" or first.get("client") != "isaac_mod":
                raise ValueError("expected mod hello or control request")
            replaced=bool(self.mod_writer and self.mod_writer is not writer)
            if replaced:
                self.mod_writer.close()
            self.mod_writer, self.hello, is_mod = writer, first, True
            self._last_key = None
            self._recent_frames.clear()
            if replaced:await self._seal_recording("connection_replaced",reopen=True)
            if self.recording_enabled and self.recorder is None:self.recorder=self._new_recorder()
            if self.recorder:
                self.recorder.append(first)
            writer.write(encode({**self.recording_message(),"type":"hello_ack","mode":"manual"}))
            await writer.drain()
            print("Isaac mod connected; control remains MANUAL.", flush=True)
            while line := await reader.readline():
                if self.mod_writer is not writer:break
                try:
                    msg = decode(line)
                    if msg.get("type") == "frame":
                        validate_frame(msg)
                        key = (msg["run_id"], msg["game_frame"])
                        if self.recorder:
                            if self._recording_run is not None and self._recording_run!=key[0]:
                                await self._seal_recording("run_changed",reopen=True)
                            self._recording_run=key[0]
                        if self._last_key and key[0] == self._last_key[0]:
                            if key[1] <= self._last_key[1]:
                                raise ValueError("duplicate or out-of-order frame")
                            self.frame_gaps += max(0, key[1] - self._last_key[1] - 1)
                        self._last_key = key
                        if (self.latest and (msg["run_id"] != self.latest["run_id"]
                                or msg["room"]["id"] != self.latest["room"]["id"]
                                or msg["mode"] != self.latest["mode"]
                                or msg["game_frame"] != self.latest["game_frame"] + 1)):
                            self._recent_frames.clear()
                        self._recent_frames.append(msg)
                        self.latest = msg
                        self.last_received = time.monotonic()
                        self._frame_times.append(self.last_received)
                        self.frames += 1
                    elif msg.get("type") == "command_ack":
                        future = self._acks.get(msg.get("request_id"))
                        if future and not future.done():
                            future.set_result(msg)
                    elif msg.get("type") == "event":
                        if msg.get("event")=="run_started":
                            if self.recorder and self._recording_run is not None:
                                await self._seal_recording("run_started",reopen=True)
                            self._recording_run=msg.get("run_id")
                            self._last_key=None;self._recent_frames.clear()
                        self.received_events += 1
                    else:
                        raise ValueError("unsupported message type")
                    msg["received_at_unix"] = time.time()
                    if self.recorder:
                        self.recorder.append(msg)
                except (ValueError, TypeError, KeyError) as exc:
                    self.invalid += 1
                    self.last_error = str(exc)
        except (ValueError, asyncio.TimeoutError, ConnectionError, asyncio.IncompleteReadError) as exc:
            self.last_error = str(exc)
        finally:
            if is_mod and self.mod_writer is writer:
                self.mod_writer = None
                self._recent_frames.clear()
                for future in self._acks.values():
                    if not future.done():
                        future.set_exception(ConnectionError("mod disconnected"))
                await self._seal_recording("game_disconnected")
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def control(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            return {"ok": False, "error": "control payload must be an object"}
        operation = payload.get("command")
        if operation in {"rotate_recording","stop_recording"}:
            purpose=payload.get("purpose","demonstration")
            completed_purpose=payload.get("completed_purpose")
            if purpose not in {"diagnostic","demonstration"} or completed_purpose not in {None,"diagnostic","demonstration"}:
                return {"ok":False,"error":"invalid recording purpose"}
            if operation=="rotate_recording" and not self.record_root:
                return {"ok":False,"error":"bridge was started without a recording root"}
            old=self.recorder
            # Swap before awaiting old-file drain, so incoming frames use the
            # new recorder immediately rather than dropping during rotation.
            self.recording_enabled=operation=="rotate_recording"
            self.record_purpose=purpose
            self.recorder=self._new_recorder() if self.recording_enabled else None
            self._recording_run=None
            if old:
                if completed_purpose:old.metadata["purpose"]=completed_purpose
                await asyncio.to_thread(old.close)
            return {"ok":True,"recording":str(self.recorder.path.resolve()) if self.recorder else None,
                    "completed_recording":str(old.path.resolve()) if old else None,
                    "completed_written":old.written if old else 0,"completed_dropped":old.dropped if old else 0,
                    "completed_error":old.error if old else None}
        if operation == "get_status":
            return {"ok": True, "status": self.status()}
        if operation == "get_frame":
            result = {"ok": self.latest is not None, "frame": self.latest, "status": self.status()}
            if payload.get("include_history") and self.latest:
                from .features import history_summary
                result["policy_history"] = history_summary(self.latest, list(self._recent_frames)[:-1])
            return result
        if operation not in {"set_mode", "action", "ping"}:
            return {"ok": False, "error": "unknown control command"}
        if not self.mod_writer or self.mod_writer.is_closing():
            return {"ok": False, "error": "Isaac mod not connected"}
        try:
            if operation == "set_mode":
                if payload.get("mode") not in MODES:
                    raise ValueError("invalid mode")
                command = {"command": "set_mode", "mode": payload["mode"]}
            elif operation == "action":
                if not self.latest or not self.last_received or time.monotonic() - self.last_received > 0.5:
                    raise ValueError("no fresh game state")
                # The policy must identify the state it actually observed. Never relabel an old action with a fresh frame.
                observed = payload.get("observation_frame")
                if type(observed) is not int:
                    raise ValueError("action requires observation_frame")
                if payload.get("run_id") != self.latest["run_id"] or payload.get("room_id") != self.latest["room"]["id"]:
                    raise ValueError("action run/room mismatch")
                action = Action(payload.get("action_id"), payload.get("use_active", False), payload.get("hold_frames", 3))
                if not 0 <= self.latest["game_frame"] - observed < MAX_OBSERVATION_AGE_FRAMES:
                    raise ValueError("action already expired or from future frame")
                command = action.as_command({"run_id": payload["run_id"], "room": {"id": payload["room_id"]}, "game_frame": observed})
            else:
                command = {"command": "ping"}
            request_id = uuid.uuid4().hex
            command.update({"protocol_version": PROTOCOL_VERSION, "type": "command", "request_id": request_id})
            future = asyncio.get_running_loop().create_future()
            self._acks[request_id] = future
            try:
                self.mod_writer.write(encode(command))
                await self.mod_writer.drain()
                if self.recorder:
                    self.recorder.append({**command, "sent_at_unix": time.time()})
                ack = await asyncio.wait_for(future, 2)
                return {"ok": bool(ack.get("ok")), "ack": ack}
            finally:
                self._acks.pop(request_id, None)
        except (ValueError, ConnectionError, asyncio.TimeoutError) as exc:
            return {"ok": False, "error": str(exc) or "command acknowledgement timed out"}

    async def publish_status(self):
        while True:
            data = self.status()
            if self.status_path:
                self.status_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.status_path.with_suffix(".tmp")
                temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(self.status_path)
            if self.mod_writer and not self.mod_writer.is_closing():
                try:
                    self.mod_writer.write(encode(self.recording_message()))
                    await self.mod_writer.drain()
                except ConnectionError:pass
            await asyncio.sleep(1)

    async def close(self):
        if self.mod_writer:
            self.mod_writer.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.recorder:
            await asyncio.to_thread(self.recorder.close)

async def request_control(payload: dict, host="127.0.0.1", port=9527) -> dict:
    reader, writer = await asyncio.open_connection(host, port, limit=MAX_MESSAGE_BYTES + 1)
    try:
        writer.write(encode({"protocol_version": PROTOCOL_VERSION, "type": "control", "payload": payload}))
        await writer.drain()
        return decode(await asyncio.wait_for(reader.readline(), 4))
    finally:
        writer.close()
        await writer.wait_closed()
