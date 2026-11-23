"""Resume the verified public checkpoint with parallel, checked HTTP ranges."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
REVISION='15852e8c16360a2fea060d615a32b45270f8a8fc'
MS_REVISION='5bc562c789648e2cd69b2377526939f5f72060e9'
NAME='model.safetensors-00001-of-00001.safetensors'
SIZE=4548221488
EXPECTED='aa33250c4fc64891ddfaba3a314fd9542ea371843c387178b425fbcc5ed680b1'
SNAPSHOT=ROOT/'.local/offline-hf/models--Qwen--Qwen3.5-2B/snapshots'/REVISION
PART=SNAPSHOT/(NAME+'.partial')
FINAL=SNAPSHOT/NAME
CHUNKS=ROOT/'.local/modelscope-chunks'
URL=f'https://modelscope.cn/api/v1/models/Qwen/Qwen3.5-2B/repo?Revision={MS_REVISION}&FilePath={NAME}'


def fetch(start,end,path):
    size=end-start+1
    if path.is_file() and path.stat().st_size==size:return
    for attempt in range(4):
        try:
            request=urllib.request.Request(URL,headers={'Range':f'bytes={start}-{end}','User-Agent':'IsaacJev/1.0 (ModelScope public checkpoint download)'})
            with urllib.request.urlopen(request,timeout=60) as response:
                span=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                if response.status!=206 or not span or tuple(map(int,span.groups()))!=(start,end,SIZE):
                    raise RuntimeError('Endpoint did not return the exact requested byte range')
                with path.open('wb') as output:
                    shutil.copyfileobj(response,output,length=1024*1024)
            if path.stat().st_size!=size:raise RuntimeError('Incomplete range response')
            return
        except urllib.error.HTTPError as error:
            if attempt==3:raise
            pause=int(error.headers.get('Retry-After','60')) if error.code==429 else 2**attempt
            time.sleep(pause)
        except (OSError,TimeoutError):
            if attempt==3:raise
            time.sleep(2**attempt)


def main():
    if FINAL.exists():print('Verified final checkpoint already present');return
    if not PART.resolve().is_relative_to(ROOT):raise ValueError('Download must remain inside this workspace')
    CHUNKS.mkdir(parents=True,exist_ok=True)
    base=PART.stat().st_size if PART.exists() else 0
    if base>SIZE:raise ValueError('Partial file is larger than the checkpoint')
    block=32*1024*1024
    ranges=[(start,min(SIZE-1,start+block-1),CHUNKS/f'{start}-{min(SIZE-1,start+block-1)}.part') for start in range(base,SIZE,block)]
    print(f'Parallel ModelScope resume: {base}/{SIZE} bytes, {len(ranges)} ranges, 8 workers',flush=True)
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as executor:
        pending={executor.submit(fetch,*item) for item in ranges}
        while pending:
            done,pending=wait(pending,timeout=5,return_when=FIRST_COMPLETED)
            for future in done:future.result()
            obtained=sum(min(path.stat().st_size,end-start+1) for start,end,path in ranges if path.exists())
            elapsed=max(1,time.monotonic()-started)
            progress={'bytes':base+obtained,'total':SIZE,'percent':round(100*(base+obtained)/SIZE,1),'rate_mib_s':round(obtained/elapsed/2**20,2)}
            (ROOT/'.local/model-download-progress.json').write_text(json.dumps(progress),encoding='utf-8')
            print(json.dumps(progress),flush=True)
    with PART.open('ab') as output:
        for _,_,path in ranges:
            with path.open('rb') as source:shutil.copyfileobj(source,output,length=8*1024*1024)
    if PART.stat().st_size!=SIZE:raise RuntimeError('Assembled checkpoint has incorrect length')
    digest=hashlib.sha256()
    with PART.open('rb') as source:
        for chunk in iter(lambda:source.read(8*1024*1024),b''):digest.update(chunk)
    if digest.hexdigest()!=EXPECTED:raise RuntimeError('Checkpoint SHA256 differs from official metadata')
    PART.rename(FINAL)
    refs=SNAPSHOT.parent.parent/'refs';refs.mkdir(exist_ok=True)
    (refs/'main').write_text(REVISION,encoding='ascii')
    note={'repo':'Qwen/Qwen3.5-2B','revision':REVISION,'snapshot':str(SNAPSHOT),'cache':str(SNAPSHOT.parent.parent.parent),'shard_sha256':EXPECTED,'weight_source':'ModelScope parallel ranges'}
    (ROOT/'.local/model-snapshot.json').write_text(json.dumps(note,indent=2),encoding='utf-8')
    print('Checkpoint complete; official SHA256 verified',flush=True)


if __name__=='__main__':main()
