"""Loss-accounted, background gzip JSONL recording."""
from pathlib import Path
import gzip
import json
import queue
import threading
import time
import uuid

class SessionRecorder:
    def __init__(self, root: Path, metadata: dict | None = None):
        created_ns=time.time_ns()
        self.path = root / (time.strftime("%Y%m%d-%H%M%S",time.localtime(created_ns/1_000_000_000)) + f"-{created_ns%1_000_000_000:09d}-" + uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True, exist_ok=False)
        self.metadata = {"format": "isaac-jev-jsonl-v1", "created_at_unix": time.time(), **(metadata or {})}
        self.accepted = self.written = self.dropped = 0
        self.error: str | None = None
        self._closed = False
        self._queue = queue.Queue(maxsize=1024)
        self._thread = threading.Thread(target=self._write, name="isaac-recorder", daemon=True)
        self._save_metadata()
        self._thread.start()

    def _save_metadata(self):
        (self.path / "session.json").write_text(json.dumps(self.metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def append(self, message: dict) -> bool:
        if self._closed or self.error:
            self.dropped += 1
            return False
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        try:
            self._queue.put_nowait(line)
            self.accepted += 1
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def _write(self):
        try:
            with gzip.open(self.path / "frames.jsonl.gz", "wt", encoding="utf-8", compresslevel=1) as out:
                while True:
                    line = self._queue.get()
                    if line is None:
                        break
                    out.write(line)
                    self.written += 1
                    if self.written % 30 == 0:
                        out.flush()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def close(self):
        if self._closed:
            return
        self._closed = True
        while self._thread.is_alive():
            try:
                self._queue.put(None, timeout=0.2)
                break
            except queue.Full:
                continue
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            self.error = "recorder did not drain within 10 seconds"
        self.metadata.update({"closed_at_unix": time.time(), "accepted": self.accepted,
                              "written": self.written, "dropped": self.dropped, "error": self.error})
        self._save_metadata()

def iter_messages(path: Path, include_diagnostics=True, include_session=False, only_closed=False):
    files = sorted(path.glob("*/frames.jsonl.gz")) if path.is_dir() and not (path / "frames.jsonl.gz").exists() else [path / "frames.jsonl.gz" if path.is_dir() else path]
    for file in files:
        metadata_path=file.parent/"session.json"
        if only_closed and metadata_path.is_file():
            metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
            if not metadata.get('closed_at_unix') or metadata.get('error'):continue
        if not include_diagnostics and metadata_path.is_file():
            metadata=json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("purpose")=="diagnostic":continue
        opener = gzip.open if file.suffix == ".gz" else open
        with opener(file, "rt", encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                if line.strip():
                    try:
                        message = json.loads(line)
                        if include_session:message["_recording_session"] = str(file.resolve())
                        yield message
                    except ValueError as exc:
                        raise ValueError(f"{file}:{number}: {exc}") from exc
