"""Freeze only source-backed Wiki summaries into the training/deployment contract."""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,default=ROOT/'.local/knowledge_cache/items')
    p.add_argument('--output',type=Path,default=ROOT/'src/isaac_jev/rules/item_combat_v005.json');a=p.parse_args();entries={}
    for path in sorted(a.cache.glob('*.json')):
        e=json.loads(path.read_text(encoding='utf-8'))
        if e.get('item_kind')!='collectible' or e.get('status')!='ready' or not e.get('identity_checked'):continue
        summary=e.get('summary','').split('\n')[0]
        if len(summary)>200:summary=summary[:200]
        entries[str(e['item_id'])]={'name':(e.get('names') or [str(e['item_id'])])[0],'summary':summary,
            'sources':[s['url'] for s in e.get('sources',[])],'revision':e.get('knowledge_revision')}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'frozen_entries':len(entries),'output':str(a.output)}))

if __name__=='__main__':main()
