"""Read-only fingerprints of the host save, game EXE, and Isaac Steam data."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / '.local/isaac-lab/host-before-sandbox.json'


def fingerprint():
    lab = json.loads((ROOT / '.local/isaac-lab/lab.json').read_text(encoding='utf-8-sig'))
    sandbox = json.loads((ROOT / '.local/isaac-lab/sandbox-info.json').read_text(encoding='utf-8-sig'))
    save = Path(sandbox['host_save_directory'])
    steam = Path('C:/Program Files (x86)/Steam/userdata')
    files = list(save.rglob('*'))
    for account in steam.iterdir():
        if account.is_dir():
            files.extend((account / '250900').rglob('*'))
            config = account / 'config/localconfig.vdf'
            if config.is_file():
                files.append(config)
    files.append(Path(lab['original_game']) / 'isaac-ng.exe')
    result = {}
    for path in files:
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            result[str(path)] = digest.hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['capture', 'verify'])
    args = parser.parse_args()
    current = fingerprint()
    if args.operation == 'capture':
        if BASELINE.exists():
            raise FileExistsError('A host baseline already exists; retain it for verification')
        BASELINE.write_text(json.dumps(current, indent=2), encoding='utf-8')
        print(f'Host baseline captured: {len(current)} files')
        return
    before = json.loads(BASELINE.read_text(encoding='utf-8-sig'))
    differences = [path for path in before.keys() | current.keys() if before.get(path) != current.get(path)]
    print(json.dumps({'host_files': len(current), 'changed_files': differences}, ensure_ascii=False, indent=2))
    if differences:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
