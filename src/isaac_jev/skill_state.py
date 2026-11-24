"""Known-map memory and stable high-level decisions built from full mod telemetry."""
from collections import deque
import copy
import hashlib
import json
from .navigation import is_shop_item
from .rooms import combat_active
from .pickups import pickup_available, ITEM_CATEGORIES

ROOM_TYPES={1:"normal",2:"shop",3:"error",4:"treasure",5:"boss",6:"miniboss",7:"secret",8:"super_secret",9:"arcade",10:"curse",11:"challenge",
    12:"library",13:"sacrifice",14:"devil",15:"angel",16:"dungeon",17:"boss_rush",18:"isaacs",19:"barren",20:"chest",21:"dice",
    22:"black_market",23:"greed_exit",24:"planetarium",25:"teleporter",26:"teleporter_exit",27:"secret_exit",28:"blue",29:"ultra_secret"}
PICKUPS={10:"heart",20:"coin",30:"key",40:"bomb",70:"pill",90:"battery",100:"collectible",300:"card",350:"trinket"}


class GameMemory:
    def __init__(self):
        self.frame=None;self.reply=None;self.rooms={};self.run_id=None;self.floor_id=None
        self.ignored=set();self.shops_seen=set();self.instance=0;self.events=deque(maxlen=30);self.previous_frame=None
        self.bridge_epoch=None

    def update(self,reply):
        frame=reply.get("frame");epoch=reply.get("status",{}).get("control_epoch")
        if not frame:self.reply=reply;return False
        if epoch is not None and self.bridge_epoch is not None:
            if epoch<self.bridge_epoch:return False
            if frame["run_id"]==self.run_id and self.previous_frame is not None and frame["game_frame"]<self.previous_frame and epoch==self.bridge_epoch:return False
        self.reply=reply;self.bridge_epoch=epoch
        if self.run_id!=frame["run_id"] or (self.previous_frame is not None and frame["game_frame"]<self.previous_frame):
            self.rooms={};self.ignored.clear();self.shops_seen.clear();self.instance+=1
        self.run_id=frame["run_id"];self.previous_frame=frame["game_frame"]
        floor=frame.get("floor",{});fid=floor.get("id",":".join(frame["room"]["id"].split(":")[:2]))
        if self.floor_id!=fid:
            self.rooms={};self.ignored.clear();self.shops_seen.clear()
        self.floor_id=fid;self.frame=frame
        for node in floor.get("rooms",[]):
            index=node.get("index");dim=node.get("dimension",floor.get("dimension",0))
            if index is None or dim!=floor.get("dimension",0):continue
            key=str(index)
            if not (node.get("visited",0)>0 or int(node.get("display_flags",0))&3 or key in self.rooms):continue
            old=self.rooms.get(key,{})
            known_type=ROOM_TYPES.get(node.get("type"),str(node.get("type","unknown"))) if node.get("visited",0)>0 or int(node.get("display_flags",0))&2 else old.get("type","unknown")
            self.rooms[key]={**old,"index":index,"type":known_type,
                "shape":node.get("shape"),"visited":bool(node.get("visited") or old.get("visited")),
                "clear":bool(node.get("clear") or old.get("clear")),"links":copy.deepcopy(node.get("links",[]))}
        current=frame["room"];index=current["id"].split(":")[-1]
        previous=self.rooms.get(index,{})
        self.rooms[index]={**previous,"index":int(index),"type":ROOM_TYPES.get(current.get("type"),str(current.get("type","unknown"))),
            "shape":current.get("shape"),"visited":True,"clear":bool(current.get("clear")),"last_seen_frame":frame["game_frame"],
            "room_id":current["id"],"last_state":copy.deepcopy(frame),
            "links":[{"slot":d["slot"],"target_index":d.get("target_room_index"),"dimension":d.get("target_dimension",floor.get("dimension",0))} for d in current.get("doors",[])]}
        for d in current.get("doors",[]):
            target=d.get("target_room_index")
            if target is not None and target>=0 and d.get("target_dimension",floor.get("dimension",0))==floor.get("dimension",0):
                self.rooms.setdefault(str(target),{"index":target,"type":"unknown","visited":False,"clear":False,"links":[]})
        return True

    def pickups(self):
        if not self.frame:return []
        result=[]
        for e in self.frame.get("entities",[]):
            if not pickup_available(e):continue
            kind=PICKUPS.get(e.get("variant"),"unsupported")
            result.append({"id":e["uid"],"kind":kind,"variant":e.get("variant"),
                "item_id":None if e.get("pickup_blind") else e.get("subtype"),
                "name":e.get("pickup_name") or ("unknown collectible" if kind=="collectible" else kind),
                "short_description":e.get("pickup_description"),"quality":e.get("pickup_quality"),"item_type":e.get("pickup_item_type"),
                "item_category":ITEM_CATEGORIES.get(e.get("pickup_item_type")),
                "is_active":e.get("pickup_item_type")==3 if e.get("pickup_item_type") is not None else None,
                "blind":bool(e.get("pickup_blind")),"shop_item":is_shop_item(e),"price":e.get("pickup_price"),
                "options_group":e.get("pickup_options",0),"position":e["position"],"wait":e.get("pickup_wait",0)})
        return result

    def decision_id(self):
        if not self.frame:return None
        f=self.frame
        facts={"run":self.run_id,"instance":self.instance,"floor":self.floor_id,"room":f["room"]["id"],
            "inventory":f["player"].get("inventory"),"health":f["player"].get("health"),"active":f["player"].get("active"),
            "doors":f["room"].get("doors"),"pickups":self.pickups(),"ignored":sorted(self.ignored),
            "phase":"combat" if combat_active(f) else "planning"}
        # Pickup drift and per-frame counters must not invalidate a several-second high-level decision.
        for e in facts["pickups"]:e.pop("position",None);e.pop("wait",None)
        return "decision-"+hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()[:20]

    def route(self,target):
        if not self.frame:return []
        source=self.frame["room"]["id"].split(":")[-1];goal=str(target)
        queue=deque([source]);parent={source:None}
        while queue:
            cur=queue.popleft()
            if cur==goal:break
            if cur!=source and not self.rooms.get(cur,{}).get("clear"):continue
            for link in self.rooms.get(cur,{}).get("links",[]):
                nxt=str(link.get("target_index"))
                if nxt in self.rooms and nxt not in parent:parent[nxt]=cur;queue.append(nxt)
        if goal not in parent:return []
        path=[goal]
        while parent[path[-1]] is not None:path.append(parent[path[-1]])
        return list(reversed(path))

    def actions(self):
        if not self.frame or combat_active(self.frame):return []
        f=self.frame;inventory=f["player"].get("inventory",{});result=[]
        for d in f["room"].get("doors",[]):
            target=d.get("target_room_index")
            if target is None or target<0 or d.get("target_dimension",0)!=f.get("floor",{}).get("dimension",0):continue
            if not d.get("open") and not d.get("locked"):continue
            if d.get("locked") and inventory.get("keys",0)<1:continue
            kind="inspect_shop" if d.get("target_room_type")==2 else "enter_door"
            result.append({"action_id":"door-"+str(d["slot"]),"kind":kind,"target_id":"door-"+str(d["slot"]),
                "destination_index":target,"destination_type":ROOM_TYPES.get(d.get("target_room_type"),"unknown"),
                "cost":{"keys":int(bool(d.get("locked")))},"visited":self.rooms.get(str(target),{}).get("visited",False)})
        for e in self.pickups():
            if e["shop_item"] or e["id"] in self.ignored:continue
            if e["kind"] not in {"heart","coin","key","bomb","pill","card","trinket","collectible"}:continue
            if e["kind"]=="collectible" and f["room"].get("type")!=4:continue
            h=f["player"].get("health",{})
            if e["kind"]=="heart" and e.get("item_id") in {1,2,5,9} and h.get("red",0)>=h.get("max_red",0):continue
            result.append({"action_id":"pickup-"+e["id"],"kind":"collect_treasure_item" if e["kind"]=="collectible" else "collect_pickup",
                "target_id":e["id"],"pickup":e})
            result.append({"action_id":"skip-"+e["id"],"kind":"skip_pickup","target_id":e["id"]})
        if f["room"].get("type")==2:result.append({"action_id":"inspect-current-shop","kind":"inspect_shop","target_id":"current-room"})
        source=f["room"]["id"].split(":")[-1]
        for index,node in self.rooms.items():
            if index==source:continue
            route=self.route(index)
            if len(route)>2:result.append({"action_id":"visit-"+index,"kind":"visit_room","target_id":index,"route":route,"visited":node.get("visited",False),"destination_type":node.get("type")})
        result.append({"action_id":"finish-floor-demo","kind":"finish_floor_demo","target_id":"current-floor"})
        return result

    def map(self):
        nodes=[{k:v for k,v in n.items() if k not in {"last_state"}} for n in self.rooms.values()]
        current=self.frame["room"]["id"].split(":")[-1] if self.frame else None
        lines=["Room graph (@ current, ? unvisited; engine grid index shown):"]
        for n in sorted(nodes,key=lambda x:x["index"]):
            links=",".join(str(l.get("target_index")) for l in n.get("links",[]) if str(l.get("target_index")) in self.rooms)
            lines.append(f"{'@' if str(n['index'])==current else ' '}{n['index']}: {n['type']} {'visited' if n.get('visited') else '?'} clear={n.get('clear')} -> [{links}]")
        return {"floor_id":self.floor_id,"current_index":current,"rooms":nodes,"text":"\n".join(lines)}

    def snapshot(self,detail="summary"):
        if not self.frame:return {"connected":False,"frame":None,"decision_id":None}
        f=self.frame;status=self.reply.get("status",{});age=status.get("last_frame_age_ms")
        phase="paused_or_stale" if age is None or age>200 else "combat" if combat_active(f) else "planning"
        result={"decision_id":self.decision_id(),"run_id":self.run_id,"run_instance":self.instance,"floor_id":self.floor_id,
            "room_id":f["room"]["id"],"game_frame":f["game_frame"],"phase":phase,"last_frame_age_ms":age,
            "connected":status.get("connected",False),"planner_schema":f.get("planner_schema"),
            "player":copy.deepcopy(f["player"]),"additional_stats":f.get("planner_stats",{}),"floor_map":self.map(),
            "room":{"type":ROOM_TYPES.get(f["room"].get("type"),"unknown"),"clear":f["room"].get("clear"),"doors":f["room"].get("doors",[])},
            "pickups":self.pickups(),"actions":self.actions() if phase=="planning" else [],"recent_events":list(self.events),
            "scope":"walk rooms, collect ordinary drops, take treasure items, inspect shops only"}
        if detail=="full":result["raw_frame"]=self.known_frame(f)
        return result

    def known_frame(self,frame):
        result=copy.deepcopy(frame)
        # Full room detail remains non-visual, but does not leak generated hidden-map contents.
        result.pop("floor",None)
        for entity in result.get("entities",[]):
            if entity.get("pickup_blind"):
                for key in ("subtype","pickup_name","pickup_description","pickup_quality","pickup_item_type"):entity[key]=None
        return result
