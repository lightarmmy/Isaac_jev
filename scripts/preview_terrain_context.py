"""Preview future terrain features from a frame file or a recorded frame; no game input."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from isaac_jev.terrain import terrain_context
from audit_control_timing import read_records


def main():
    p=argparse.ArgumentParser();p.add_argument("source",type=Path);p.add_argument("--room-id");p.add_argument("--game-frame",type=int)
    p.add_argument("--output",type=Path);args=p.parse_args()
    if args.source.suffix==".json":
        frame=json.loads(args.source.read_text(encoding="utf-8-sig"));frame=frame.get("frame",frame)
    else:
        path=args.source/"frames.jsonl.gz" if args.source.is_dir() else args.source
        frame=None;state={}
        for row in read_records(path,state):
            if row.get("type")!="frame":continue
            if args.room_id and row["room"]["id"]!=args.room_id:continue
            if args.game_frame is not None and row["game_frame"]!=args.game_frame:continue
            frame=row;break
        if frame is None:raise ValueError("Requested frame not found")
    result={"room_id":frame["room"]["id"],"game_frame":frame["game_frame"],"terrain":terrain_context(frame)}
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+"\n",encoding="utf-8")
    print(text)


if __name__=="__main__":main()
