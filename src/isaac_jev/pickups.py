"""Separate pickup contents from persistent pedestal entities and verify acquisition."""

PICKUP_CONFIRMATION_FRAMES = 90
ITEM_CATEGORIES = {1:"passive",2:"trinket",3:"active",4:"familiar"}


def empty_pedestal(entity):
    return entity.get("kind")=="pickup" and entity.get("variant")==100 and entity.get("subtype")==0


def pickup_available(entity):
    return bool(entity and entity.get("kind")=="pickup" and not entity.get("dead") and not empty_pedestal(entity))


def collectible_count(player,item_id):
    return sum(e.get("count",1) for e in player.get("inventory",{}).get("collectibles",[]) if e.get("id")==item_id)


def acquisition_confirmed(target,before,after):
    pickup=target.get("pickup",{});kind=pickup.get("kind");item_id=pickup.get("item_id")
    old,new=before.get("inventory",{}),after.get("inventory",{})
    if kind=="collectible":
        if type(item_id) is int and item_id>0:
            passive=collectible_count(after,item_id)>collectible_count(before,item_id)
            active=after.get("active",{}).get("id")==item_id and before.get("active")!=after.get("active")
            return passive or active
        # Blind identities remain unknown until owned; consumption plus an owned
        # collectible/active change supplies evidence without leaking the hidden ID.
        before_ids={e.get("id"):e.get("count",1) for e in old.get("collectibles",[])}
        return any(e.get("count",1)>before_ids.get(e.get("id"),0) for e in new.get("collectibles",[])) or (
            after.get("active",{}).get("id",0)>0 and before.get("active",{}).get("id")!=after.get("active",{}).get("id"))
    resource={"coin":"coins","key":"keys","bomb":"bombs"}.get(kind)
    if resource:return new.get(resource,0)>old.get(resource,0)
    if kind=="heart":
        return any(after.get("health",{}).get(k,0)>before.get("health",{}).get(k,0) for k in ("red","soul","bone","rotten","max_red"))
    slot={"card":"card","pill":"pill_color","trinket":"trinkets"}.get(kind)
    return slot is not None and old.get(slot)!=new.get(slot)


def pickup_consumed(target,entity,before=None,after=None):
    if not pickup_available(entity):return True
    item_id=target.get("pickup",{}).get("item_id")
    # Taking an active item can leave the previous active on the same pedestal UID.
    if type(item_id) is int and item_id>0:
        return entity.get("subtype") is not None and entity.get("subtype")!=item_id
    if before and after and target.get("pickup",{}).get("kind")=="collectible":
        old_active=before.get("active",{}).get("id",0);new_active=after.get("active",{}).get("id",0)
        return old_active>0 and new_active>0 and old_active!=new_active and entity.get("subtype")==old_active
    return False
