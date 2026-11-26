"""Inventory sealed human demonstrations before building a new policy dataset."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from isaac_jev.recording import iter_messages
from isaac_jev.rooms import combat_active

def audit(root):
    modes=Counter();items={};runs=defaultdict(Counter);effects=Counter();sessions=[];skipped=[]
    for metadata in sorted(Path(root).glob('*/session.json')):
        info=json.loads(metadata.read_text(encoding='utf-8'))
        if not info.get('closed_at_unix') or info.get('purpose')=='diagnostic' or info.get('error'):
            skipped.append(metadata.parent.name);continue
        sessions.append(metadata.parent.name)
        for f in iter_messages(metadata.parent):
            if f.get('type')!='frame':continue
            mode=f.get('mode','unknown');modes[mode]+=1;runs[f['run_id']][mode]+=1
            if mode!='manual':continue
            runs[f['run_id']]['human_combat']+=combat_active(f)
            runs[f['run_id']]['weapon_observed']+=bool(f['player'].get('weapons'))
            for item in f['player'].get('inventory',{}).get('collectibles',[]):
                key=str(item['id']);entry=items.setdefault(key,{**item,'human_frames':0,'runs':[]})
                entry['human_frames']+=1
                if f['run_id'] not in entry['runs']:entry['runs'].append(f['run_id'])
            for e in f.get('entities',[]):
                if e.get('kind')=='effect':effects[str(e.get('variant'))]+=1
        print(json.dumps({'audited_session':metadata.parent.name,'total_manual_frames':modes['manual']}),flush=True)
    return {'source_sessions':sessions,'skipped_sessions':skipped,'modes':dict(modes),
            'runs':dict(runs),'items':items,'effect_variants':dict(effects),
            'note':'File sessions are not independent runs. Exact weapon charge is absent in legacy recordings; do not invent it.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('recordings',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=audit(a.recordings);a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(a.output),'items':len(result['items']),'modes':result['modes'],'runs':len(result['runs'])}),flush=True)

if __name__=='__main__':main()
