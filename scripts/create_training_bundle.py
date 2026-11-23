"""Package source, finalized demonstration sessions, and prepared data for SSH."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--dataset',default='datasets/combat-v002')
parser.add_argument('--output',default='.local/transfer/isaac-jev-training.zip')
args=parser.parse_args()
dataset_path=ROOT/args.dataset
destination=ROOT/args.output
destination.parent.mkdir(parents=True,exist_ok=True)
tracked=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
files={Path(name) for name in tracked if (ROOT/name).is_file()}
for metadata in (ROOT/'recordings').glob('*/session.json'):
    session=json.loads(metadata.read_text(encoding='utf-8'))
    if session.get('purpose')!='demonstration':continue
    if not session.get('closed_at_unix') or session.get('error') or session.get('dropped'):
        raise ValueError(f'Recording is not finalized and lossless: {metadata.parent.name}')
    files.add(metadata.relative_to(ROOT))
    files.add((metadata.parent/'frames.jsonl.gz').relative_to(ROOT))
for path in dataset_path.iterdir():
    if path.is_file():files.add(path.relative_to(ROOT))
dataset=json.loads((dataset_path/'dataset.json').read_text(encoding='utf-8'))
if not dataset['counts']['train'] or not dataset['counts']['validation']:
    raise ValueError('An independent training and validation split is required')
manifest={'format':'isaac-jev-training-bundle-v1','dataset':dataset,'files':{}}
with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
    for relative in sorted(files):
        data=(ROOT/relative).read_bytes()
        if relative.suffix=='.sh':data=data.replace(b'\r\n',b'\n')
        name=relative.as_posix()
        manifest['files'][name]=hashlib.sha256(data).hexdigest()
        archive.writestr(name,data)
    archive.writestr('training_bundle.json',json.dumps(manifest,ensure_ascii=False,indent=2))
digest=hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix('.sha256').write_text(digest+'\n',encoding='ascii')
print(json.dumps({'archive':str(destination),'bytes':destination.stat().st_size,'sha256':digest,
                  'files':len(files),'counts':dataset['counts']},indent=2))
