"""Select a completed experiment or an explicit retained epoch on the existing loopback service."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def rpc(command):
    with socket.create_connection(('127.0.0.1',19528),timeout=5) as sock:
        sock.sendall((json.dumps({'command':command})+'\n').encode())
        return json.loads(sock.makefile('rb').readline())


def deploy(experiment,epoch=None):
    if experiment not in {f'e{i:02d}' for i in range(1,10)}:raise ValueError('Experiment must be e01..e09')
    checkpoint=ROOT/'artifacts/combat-v006'/('combat-v006-'+experiment)
    if epoch is not None:
        if not 1<=epoch<=30:raise ValueError('Epoch must be 1..30')
        checkpoint=checkpoint/'checkpoints'/f'epoch-{epoch:03d}'
    cfg=json.loads((checkpoint/'policy_config.json').read_text())
    for filename,digest in json.loads((checkpoint/'manifest.json').read_text()).items():
        path=(checkpoint/filename).resolve()
        assert path.parent==checkpoint.resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    base=Path('/xufanghui/isaac-jev')
    settings=base/'configs/remote_policy.json';before=settings.read_bytes()
    old=rpc('describe');old_pid=int((base/'.local/policy-service.pid').read_text())
    proc=Path(f'/proc/{old_pid}')
    assert (proc/'cwd').resolve()==base
    assert any(x.endswith(b'scripts/policy_rpc.py') for x in (proc/'cmdline').read_bytes().split(b'\0'))
    backup=base/'.local'/('experiment-switch-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True,exist_ok=False)
    shutil.copytree(base/'src/isaac_jev',backup/'isaac_jev')
    shutil.copy2(base/'scripts/policy_rpc.py',backup/'policy_rpc.py')
    (backup/'remote_policy.json').write_bytes(before)
    def launch():subprocess.run([str(base/'.venv/bin/python'),'scripts/start_policy_service.py'],cwd=base,check=True)
    def stop_failed_candidate():
        pid=int((base/'.local/policy-service.pid').read_text());process=Path(f'/proc/{pid}')
        if not process.exists():return
        assert (process/'cwd').resolve()==base
        assert any(x.endswith(b'scripts/policy_rpc.py') for x in (process/'cmdline').read_bytes().split(b'\0'))
        try:rpc('shutdown')
        except OSError:os.kill(pid,15)
        deadline=time.monotonic()+30
        while process.exists() and time.monotonic()<deadline:time.sleep(.2)
        if process.exists():os.kill(pid,15)
        deadline=time.monotonic()+10
        while process.exists() and time.monotonic()<deadline:time.sleep(.2)
        assert not process.exists(),'Failed candidate process did not stop'
    def wait(expected):
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            try:
                r=rpc('describe')
                if r['backend']['checkpoint']==expected:return r
            except (OSError,ValueError,KeyError):pass
            time.sleep(1)
        raise RuntimeError('Selected service did not become ready')
    assert rpc('shutdown').get('shutdown')
    deadline=time.monotonic()+30
    while proc.exists() and time.monotonic()<deadline:time.sleep(.2)
    assert not proc.exists(),'Old policy process has not stopped'
    try:
        shutil.copytree(ROOT/'src/isaac_jev',base/'src/isaac_jev',dirs_exist_ok=True)
        shutil.copy2(ROOT/'scripts/policy_rpc.py',base/'scripts/policy_rpc.py')
        config=json.loads(before)
        config['checkpoint']=str(checkpoint)
        temporary=settings.with_suffix('.experiment.tmp');temporary.write_text(json.dumps(config,indent=2));temporary.replace(settings)
        launch()
        label=f"combat-v006-{experiment}-epoch{cfg['checkpoint_epoch']:03d}"
        result=wait(label)
        print(json.dumps({'ready':True,'backend':result['backend'],'checkpoint_epoch':cfg['checkpoint_epoch'],'rollback_backup':str(backup)}))
    except BaseException:
        stop_failed_candidate()
        shutil.copytree(backup/'isaac_jev',base/'src/isaac_jev',dirs_exist_ok=True)
        shutil.copy2(backup/'policy_rpc.py',base/'scripts/policy_rpc.py')
        settings.write_bytes(before);launch();wait(old['backend']['checkpoint'])
        raise


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--experiment',required=True);p.add_argument('--epoch',type=int)
    a=p.parse_args();deploy(a.experiment,a.epoch)
