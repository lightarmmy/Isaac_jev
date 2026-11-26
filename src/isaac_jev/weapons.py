"""Observed weapon state and explicit legacy fallbacks, independent of key labels."""
import math

WEAPON_NAMES={1:'tears',2:'brimstone',3:'laser',4:'knife',5:'bombs',6:'rockets',7:'monstro_lung',8:'controlled_tear',9:'tech_x',10:'bone',11:'axe',12:'urn',13:'spirit_sword',14:'fetus',15:'whip'}
ITEM_WEAPONS=((395,9),(118,2),(114,4),(229,7),(329,8),(168,6),(52,5))

def weapon_state(frame):
    p=frame['player'];observed=p.get('weapons') or []
    items={e.get('id') for e in p.get('inventory',{}).get('collectibles',[])}
    native=next((w for w in observed if w.get('slot')==1),observed[0] if observed else {})
    kind=native.get('type') or next((weapon for item,weapon in ITEM_WEAPONS if item in items),p.get('weapon_type') or 1)
    name=WEAPON_NAMES.get(kind,'unknown')
    # Control semantics are conservative. A listed type is not evidence that
    # every synergy or cross-room charge behaviour has been verified.
    trigger='charge_release' if kind in {2,4,9} or 69 in items else 'charge_auto' if kind==7 else 'continuous'
    charge,max_charge=native.get('charge'),native.get('max_charge')
    known=isinstance(charge,(float,int)) and isinstance(max_charge,(float,int)) and max_charge>0
    ratio=max(0,min(1,charge/max_charge)) if known else None
    return {'type':kind,'name':name,'source':'observed' if native else 'inventory_inferred',
            'trigger':trigger,'charge':charge,'max_charge':max_charge,'charge_ratio':ratio,'charge_known':known,
            'num_fired':native.get('num_fired'),'fire_delay':native.get('fire_delay'),
            'modifiers':native.get('modifiers'),'shoot_direction':p.get('shoot_direction'),
            'held_frames':p.get('shoot_held_frames'),'holding':p.get('shoot_holding'),
            'precharge_allowed':trigger=='charge_release' and kind in {2,9} and known,
            'precharge_support':'requires_cross_room_validation' if trigger=='charge_release' else 'not_holdable_full_charge',
            'special_control':kind in {5,6,8,11,12,15}}

def release_event(before,after):
    a,b=weapon_state(before),weapon_state(after)
    if a['type']!=b['type']:return False
    return bool((a.get('holding') and not b.get('holding')) or
                (a.get('num_fired') is not None and b.get('num_fired') is not None and b['num_fired']>a['num_fired']))
