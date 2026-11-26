"""Apply the live V005 text serializer to cached rows; validate before replacement."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from isaac_jev.features import contract_for
from isaac_jev.features_v005 import VERSION, compact_prompt

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('dataset',type=Path)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args();root=args.dataset
    meta=json.loads((root/'dataset.json').read_text(encoding='utf-8'))
    if meta.get('feature_version')!=VERSION:raise ValueError('Only V005 cached rows can be compacted')
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('Qwen/Qwen3.5-2B',revision='15852e8c16360a2fea060d615a32b45270f8a8fc',local_files_only=True)
    source=root/'samples.jsonl';staged=root/'samples.compact-staged.jsonl'
    maximum=total_tokens=count=0;too_long=[];splits=Counter();unchanged_digest=hashlib.sha256()
    with source.open(encoding='utf-8') as inp,staged.open('w',encoding='utf-8') as out:
        for line in inp:
            row=json.loads(line)
            preserved={k:v for k,v in row.items() if k not in ('prompt','deployment_prompt')}
            for key in ('prompt','deployment_prompt'):row[key]=compact_prompt(row[key])
            lengths=[len(tokenizer.encode(row['prompt']))]
            if row['deployment_prompt']!=row['prompt']:lengths.append(len(tokenizer.encode(row['deployment_prompt'])))
            length=max(lengths);maximum=max(maximum,length);total_tokens+=length
            if length>3072:too_long.append([row['run_id'],row['game_frame'],length])
            assert preserved=={k:v for k,v in row.items() if k not in ('prompt','deployment_prompt')}
            unchanged_digest.update(json.dumps(preserved,sort_keys=True,separators=(',',':')).encode())
            out.write(json.dumps(row,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
            count+=1;splits[row['split']]+=1
            if count%2048==0:print(f'Compact V005: {count} rows; maximum={maximum} tokens; over_budget={len(too_long)}',flush=True)
    result={'samples':count,'max_tokens':maximum,'mean_tokens':round(total_tokens/max(count,1),1),
            'too_long_count':len(too_long),'too_long':too_long[:20],'splits':dict(splits),
            'non_text_fields_sha256':unchanged_digest.hexdigest(),'both_prompt_conditions_checked':True,
            'source_bytes':source.stat().st_size,'compact_bytes':staged.stat().st_size,'dry_run':args.dry_run}
    (root/'compression_report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    if too_long:raise SystemExit('Compact prompts still exceed 3072 tokens; original dataset remains intact')
    if args.dry_run:return
    if dict(splits)!=meta['counts']:raise ValueError('Cached sample counts changed')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    os.link(source,root/f'samples.before-compact-{stamp}.jsonl')
    os.link(root/'dataset.json',root/f'dataset.before-compact-{stamp}.json')
    staged.replace(source)
    meta.update(contract_for(meta))
    staged_meta=root/'dataset.compact-staged.json'
    staged_meta.write_text(json.dumps(meta,indent=2),encoding='utf-8')
    staged_meta.replace(root/'dataset.json')
    print('Cached prompts and dataset contract updated; labels and numeric features preserved',flush=True)

if __name__=='__main__':main()
