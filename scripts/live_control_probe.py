"""Wait for a stationary player in a fresh clear room, then probe once."""
import asyncio
import json
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from isaac_jev.bridge import request_control
from isaac_jev.cli import probe


async def main():
    deadline=time.monotonic()+55
    while time.monotonic()<deadline:
        response=await request_control({'command':'get_frame'})
        frame=response.get('frame');status=response['status']
        version=(status.get('hello') or {}).get('mod_version')
        if frame and status['connected'] and version=='0.3.0' and (status['last_frame_age_ms'] or 0)<100:
            player=frame['player'];position=player['position'];velocity=player['velocity'];room=frame['room']
            left=room['top_left'];bottom=room['bottom_right']
            centered=left['x']+80<position['x']<bottom['x']-80 and left['y']+45<position['y']<bottom['y']-45
            clear_grid=not any(g.get('type') not in [0,1] and math.hypot(g['position']['x']-position['x'],g['position']['y']-position['y'])<45 for g in room['grid'])
            if centered and clear_grid and abs(velocity['x'])<0.1 and abs(velocity['y'])<0.1 and room['clear'] and frame['mode']=='manual':
                before={'game_frame':frame['game_frame'],'position':position,'run_id':frame['run_id'],'room_id':room['id']}
                await probe(SimpleNamespace(port=9527))
                await asyncio.sleep(0.3)
                after=await request_control({'command':'get_frame'})
                current=after['frame']
                result={'before':before,'after':{'game_frame':current['game_frame'],'position':current['player']['position'],'mode':current['mode']},'delta_x':current['player']['position']['x']-position['x'],'status':after['status']}
                (ROOT/'.local/isaac-lab/control-probe-v2.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
                print(json.dumps(result,ensure_ascii=False,indent=2))
                return
        await asyncio.sleep(0.2)
    print('Probe not sent: no fresh, stationary player in a clear central area with mod 0.3.0.')


if __name__=='__main__':
    asyncio.run(main())
