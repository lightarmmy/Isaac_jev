"""Deploy a retained V007 head, verify native GPU predictions, and roll back on failure."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/xufanghui/isaac-jev')
sys.path.insert(0, str(ROOT / 'src'))


def rpc(payload, timeout=10):
    with socket.create_connection(('127.0.0.1', 19528), timeout=timeout) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall((json.dumps(payload) + '\n').encode())
        reply = json.loads(sock.makefile('rb').readline())
    if not reply.get('ok'):
        raise RuntimeError(reply)
    return reply


def deploy(group, epoch=None):
    import numpy as np
    import torch
    from safetensors.torch import load_file
    from isaac_jev.model import head_from_config, decode_actions
    from isaac_jev.remote import validate_description, prediction_action
    if group not in ('s01', 's02'):
        raise ValueError('Group must be s01 or s02')
    if epoch is not None and not 1 <= epoch <= 30:
        raise ValueError('Epoch must be 1..30')
    checkpoint = ROOT / 'artifacts/combat-v007' / ('combat-v007-' + group)
    if epoch is not None:
        checkpoint = checkpoint / 'checkpoints' / f'epoch-{epoch:03d}'
    cfg = json.loads((checkpoint / 'policy_config.json').read_text())
    if cfg['feature_version'].startswith('combat-tactical-v10-'):
        from isaac_jev.features_v010 import observation
        history_key='tactical_history'
    elif cfg['feature_version'].startswith('combat-feedback-v9-'):
        from isaac_jev.features_v009 import observation
        history_key='feedback_history'
    else:
        from isaac_jev.features_v007 import observation
        history_key='semantic_history'
    assert cfg['semantic_experiment_id'] == group
    for name, expected in json.loads((checkpoint / 'manifest.json').read_text()).items():
        path = (checkpoint / name).resolve()
        assert path.parent == checkpoint.resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    validate_description({'config': cfg})
    torch.set_num_threads(1)
    head = head_from_config(cfg).eval()
    head.load_state_dict(load_file(str(checkpoint / 'head.safetensors')))
    norm = json.loads((checkpoint / 'normalization.json').read_text())
    mean, std = torch.tensor(norm['mean']), torch.tensor(norm['std'])
    dataset = ROOT / 'datasets/combat-v007'
    rows = [json.loads(line) for line in (dataset / 'samples.jsonl').open()]
    features = np.load(ROOT / 'artifacts/combat-v007/features/state.npy', mmap_mode='r')
    fixtures = [json.loads(line) for line in (dataset / 'runtime-fixtures.jsonl').open()]
    fixtures = [f for f in fixtures if rows[f['row_index']]['split'] == 'validation'][:32]
    assert len(fixtures) == 32
    expected_predictions = []
    for fixture in fixtures:
        index = fixture['row_index']
        prompt, numeric, names = observation(fixture['frame'], history_facts=fixture[history_key], profile=cfg['feature_version'])
        assert prompt == rows[index]['prompt'] and numeric == rows[index]['numeric'] and names == cfg['numeric_names']
        with torch.inference_mode():
            first, second, _ = head(torch.tensor(np.asarray(features[index:index + 1]).copy()),
                                    (torch.tensor(numeric) - mean).unsqueeze(0) / std)
            move, shoot = decode_actions(head, first, second)
        expected_predictions.append(int(move.item()) * 5 + int(shoot.item()))
    task = ROOT / '.local' / f'{group}-deployment'
    task.mkdir(exist_ok=True)
    def report(stage, **values):
        value = {'stage': stage, 'group': group, 'epoch': cfg['checkpoint_epoch'], **values}
        (task / 'status.json').write_text(json.dumps(value, indent=2))
        print(json.dumps(value), flush=True)
    settings = BASE / 'configs/remote_policy.json'
    before = settings.read_bytes()
    old = rpc({'command': 'describe'})
    label = cfg.get('checkpoint_label') or f"combat-v007-{group}-epoch{cfg['checkpoint_epoch']:03d}"
    backup = BASE / '.local' / ('semantic-switch-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'src/isaac_jev', backup / 'isaac_jev', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(BASE / 'scripts/policy_rpc.py', backup / 'policy_rpc.py')
    (backup / 'remote_policy.json').write_bytes(before)
    def stop():
        pid = int((BASE / '.local/policy-service.pid').read_text())
        process = Path(f'/proc/{pid}')
        if not process.exists():
            return
        assert (process / 'cwd').resolve() == BASE
        assert any(arg.endswith(b'scripts/policy_rpc.py') for arg in (process / 'cmdline').read_bytes().split(b'\0'))
        try:
            rpc({'command': 'shutdown'})
        except OSError:
            os.kill(pid, 15)
        deadline = time.monotonic() + 30
        while process.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        if process.exists():
            os.kill(pid, 15)
        deadline = time.monotonic() + 10
        while process.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        assert not process.exists(), 'Verified policy process did not stop'
    def launch():
        subprocess.run([str(BASE / '.venv/bin/python'), 'scripts/start_policy_service.py'], cwd=BASE, check=True)
    def wait(expected):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                reply = rpc({'command': 'describe'}, 3)
                if reply['backend']['checkpoint'] == expected:
                    return reply
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(1)
        raise RuntimeError('Policy startup timed out: ' + expected)
    try:
        report('switching', previous_checkpoint=old['backend']['checkpoint'], rollback_backup=str(backup))
        stop()
        shutil.copytree(ROOT / 'src/isaac_jev', BASE / 'src/isaac_jev', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(ROOT / 'scripts/policy_rpc.py', BASE / 'scripts/policy_rpc.py')
        updated = json.loads(before)
        updated.update(checkpoint=str(checkpoint), warmup_dataset=str(dataset))
        temporary = settings.with_suffix('.semantic.tmp')
        temporary.write_text(json.dumps(updated, indent=2))
        temporary.replace(settings)
        launch()
        report('warming_up', checkpoint=label)
        ready = wait(label)
        assert validate_description(ready) == cfg
        report('verifying_native_gpu', checkpoint=label)
        timings = []
        for fixture, expected in zip(fixtures, expected_predictions):
            row = rows[fixture['row_index']]
            frame = fixture['frame']
            identity = {'run_id': frame['run_id'], 'room_id': frame['room']['id'], 'game_frame': frame['game_frame']}
            payload = {'command': 'predict', 'prompt': row['prompt'], 'numeric': row['numeric'], 'observation': identity,
                       **{field: cfg[field] for field in ('feature_version', 'head_architecture', 'rules_sha256')}}
            started = time.perf_counter()
            reply = rpc(payload)
            assert reply['observation'] == identity
            action = prediction_action(reply, frame, cfg)
            assert action.action_id == expected, 'Native GPU prediction differs from retained head/cache'
            timings.append({'rpc_ms': round((time.perf_counter() - started) * 1000, 3),
                            **{field: reply[field] for field in ('model_ms', 'server_ms', 'tokens')}})
        result = {'ready': True, 'checkpoint': label, 'group': group, 'checkpoint_epoch': cfg['checkpoint_epoch'],
                  'manifest_valid': True, 'native_gpu_cached_action_matches': len(fixtures), 'samples': len(fixtures),
                  'feature_version': cfg['feature_version'], 'numeric_size': cfg['numeric_size'],
                  'backend': ready['backend'], 'rollback_backup': str(backup), 'timings': timings}
        (task / 'verification.json').write_text(json.dumps(result, indent=2))
        report('complete', checkpoint=label, verified_samples=len(fixtures))
        return result
    except BaseException:
        report('rolling_back', checkpoint=old['backend']['checkpoint'])
        stop()
        shutil.copytree(backup / 'isaac_jev', BASE / 'src/isaac_jev', dirs_exist_ok=True)
        shutil.copy2(backup / 'policy_rpc.py', BASE / 'scripts/policy_rpc.py')
        settings.write_bytes(before)
        launch()
        wait(old['backend']['checkpoint'])
        report('rollback_complete', checkpoint=old['backend']['checkpoint'])
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', required=True, choices=('s01', 's02'))
    parser.add_argument('--epoch', type=int)
    args = parser.parse_args()
    deploy(args.group, args.epoch)
