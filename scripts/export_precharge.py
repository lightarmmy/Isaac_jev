"""Extract human door-entry charging examples, separate from combat policy labels."""
import argparse
from collections import deque
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from isaac_jev.recording import iter_messages
from isaac_jev.weapons import weapon_state
from isaac_jev.rooms import combat_active

def export(recordings,output):
    recent=deque(maxlen=180);count=0;previous=None;output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8') as out:
        for f in iter_messages(recordings,include_diagnostics=False,include_session=True,only_closed=True):
            if f.get('type')!='frame':continue
            adjacent=previous and f.get('mode')=='manual' and previous.get('mode')=='manual' and f['run_id']==previous['run_id'] and f.get('_recording_session')==previous.get('_recording_session') and f['game_frame']==previous['game_frame']+1
            if not adjacent:recent.clear()
            if adjacent and f['room']['id']!=previous['room']['id'] and not combat_active(previous):
                w=weapon_state(previous);shoot=previous.get('human_input',{}).get('shoot',{})
                if w['trigger'].startswith('charge') and (shoot.get('x') or shoot.get('y')):
                    same=[x for x in recent if x['room']['id']==previous['room']['id']]
                    out.write(json.dumps({'run_id':f['run_id'],'source_session':f.get('_recording_session'),'entry_frame':f['game_frame'],
                        'origin_room':previous['room']['id'],'destination_room':f['room']['id'],'weapon_family':w['name'],'charge_known':w['charge_known'],
                        'source':'human','sample_kind':'precharge_door_entry','frames':[{'frame':x['game_frame'],'position':x['player']['position'],
                        'shoot':x.get('human_input',{}).get('shoot'),'weapon':weapon_state(x)} for x in same[-90:]],
                        'note':'Door entry is the example label; destination facts are not combat input. Legacy exact charge remains unknown.'},ensure_ascii=False)+'\n');count+=1
            if f.get('mode')=='manual':recent.append(f)
            previous=f
    return {'precharge_examples':count,'output':str(output)}

def main():
    p=argparse.ArgumentParser();p.add_argument('recordings',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(export(a.recordings,a.output)))

if __name__=='__main__':main()
