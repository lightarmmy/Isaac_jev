"""Start the project's L40S service once; keep it running after SSH disconnects."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
local=ROOT/".local";local.mkdir(exist_ok=True)
arguments=sys.argv[1:]
settings=ROOT/"configs/remote_policy.json"
if settings.exists() and not arguments:
    cfg=json.loads(settings.read_text())
    arguments=["--checkpoint",cfg["checkpoint"],"--warmup-dataset",cfg["warmup_dataset"],
               "--cpu-threads",str(cfg.get("cpu_threads",1)),"--pad-multiple",str(cfg.get("pad_multiple",512))]
with (local/"policy-service-start.lock").open("w") as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    pid_path=local/"policy-service.pid"
    if pid_path.exists():
        pid=int(pid_path.read_text())
        try:
            cmd=Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            cwd=Path(f"/proc/{pid}/cwd").resolve()
            if cwd==ROOT and any(arg.endswith(b"scripts/policy_rpc.py") for arg in cmd):
                print(json.dumps({"already_running":True,"pid":pid}));sys.exit(0)
        except (FileNotFoundError,PermissionError):pass
    (local/"policy-service-ready.json").unlink(missing_ok=True)
    with (local/"policy-service.log").open("ab",buffering=0) as log:
        process=subprocess.Popen([str(ROOT/".venv/bin/python"),"-u",str(ROOT/"scripts/policy_rpc.py"),*arguments],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    pid_path.write_text(str(process.pid))
    print(json.dumps({"started":True,"pid":process.pid,"log":str(local/"policy-service.log")}))
