"""Token and weapon coverage checks before spending GPU time on V005."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from isaac_jev.features_v005 import KNOWLEDGE

def main():
    p=argparse.ArgumentParser();p.add_argument('dataset',type=Path);a=p.parse_args()
    from transformers import AutoTokenizer
    token=AutoTokenizer.from_pretrained('Qwen/Qwen3.5-2B',revision='15852e8c16360a2fea060d615a32b45270f8a8fc',local_files_only=True)
    maximum=0;counts=Counter();missing=Counter();charge=Counter();too_long=[]
    for line in (a.dataset/'samples.jsonl').open(encoding='utf-8'):
        r=json.loads(line);length=len(token.encode(r['deployment_prompt']));maximum=max(maximum,length)
        if length>3072:too_long.append([r['run_id'],r['game_frame'],length])
        counts[(r['split'],r.get('weapon_family','unknown'))]+=1
        charge[r.get('weapon_family','unknown')]+=r.get('weapon_charge_known',False)
        facts=json.loads(r['deployment_prompt'].split('Current state:\n')[1].split('\nDesired outcome')[0])
        missing.update(str(item[0]) for item in facts['build'].get('other_items',[]) if str(item[0]) not in KNOWLEDGE)
    result={'max_tokens':maximum,'too_long':too_long[:20],'too_long_count':len(too_long),
            'weapon_split_samples':{str(k):v for k,v in counts.items()},'observed_charge_samples':dict(charge),'missing_knowledge_samples':dict(missing)}
    (a.dataset/'preflight.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
    if too_long:raise SystemExit('V005 prompts exceed the 3072 token budget; shorten features before training')
    if missing:raise SystemExit('Complete source-backed item knowledge before formal V005 feature extraction')

if __name__=='__main__':main()
