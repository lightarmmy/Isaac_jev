"""Parallel HTTP-range download with exact spans and a required whole-file SHA256."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--seed", type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to(ROOT):raise ValueError("Output must be inside the project")
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256):raise ValueError("Expected SHA256 required")
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != args.sha256:raise ValueError("Existing file has wrong SHA256")
        print("Verified file already present", flush=True);return
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(args.url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        span = re.fullmatch(r"bytes 0-0/(\d+)", response.headers.get("Content-Range", ""))
        if response.status != 206 or not span:raise ValueError("Endpoint must support checked HTTP ranges")
        size = int(span[1])
    part = target.with_suffix(target.suffix+".partial")
    if not part.exists() and args.seed:
        base = args.seed.stat().st_size
        if base > size:raise ValueError("Seed exceeds final size")
        with args.seed.open("rb") as source, part.open("wb") as out:
            remaining = base
            while remaining:
                block = source.read(min(8*1024*1024, remaining))
                if not block:raise ValueError("Seed truncated during copy")
                out.write(block);remaining -= len(block)
    base = part.stat().st_size if part.exists() else 0
    if base > size:raise ValueError("Partial file exceeds final size")
    chunks = target.parent/(target.name+".chunks")
    chunks.mkdir(exist_ok=True)
    ranges = [(s, min(size-1, s+32*1024*1024-1)) for s in range(base, size, 32*1024*1024)]
    def fetch(pair):
        start, end = pair;path = chunks/f"{start}-{end}.part"
        if path.exists() and path.stat().st_size == end-start+1:return
        for attempt in range(4):
            try:
                req = urllib.request.Request(args.url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(req, timeout=60) as response:
                    expected = f"bytes {start}-{end}/{size}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise ValueError("Incorrect HTTP range")
                    with path.open("wb") as out:shutil.copyfileobj(response, out, 1024*1024)
                if path.stat().st_size != end-start+1:raise ValueError("Incomplete chunk")
                return
            except (OSError, ValueError):
                if attempt == 3:raise
                time.sleep(2**attempt)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {pool.submit(fetch, pair) for pair in ranges}
        while pending:
            done, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            for future in done:future.result()
            obtained = sum(min((chunks/f"{s}-{e}.part").stat().st_size, e-s+1)
                           for s,e in ranges if (chunks/f"{s}-{e}.part").exists())
            progress = {"bytes": base+obtained, "total": size, "percent": round(100*(base+obtained)/size,1),
                        "rate_mib_s": round(obtained/max(1,time.monotonic()-started)/2**20,2)}
            target.with_suffix(".progress.json").write_text(json.dumps(progress), encoding="utf-8")
            print(json.dumps(progress), flush=True)
    with part.open("ab") as out:
        for s,e in ranges:
            with (chunks/f"{s}-{e}.part").open("rb") as source:shutil.copyfileobj(source,out,8*1024*1024)
    digest = hashlib.sha256()
    with part.open("rb") as source:
        for block in iter(lambda:source.read(8*1024*1024),b""):digest.update(block)
    if part.stat().st_size != size or digest.hexdigest() != args.sha256:raise ValueError("Whole-file verification failed")
    part.rename(target)
    print("Download complete; official SHA256 verified", flush=True)


if __name__ == "__main__":main()
