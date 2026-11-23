"""Synthetic telemetry for integration tests; never evidence of a live game connection."""
def sample_frame(n=0):
    return {"protocol_version":1,"type":"frame","run_id":"mock-run","game_frame":n,"mode":"manual",
            "capture_phase":"pre_update","room":{"id":"mock-room","clear":False,"top_left":{"x":0,"y":0},
            "bottom_right":{"x":640,"y":560},"grid":[]},
            "player":{"position":{"x":200+n,"y":200},"velocity":{"x":1,"y":0},"size":10,
            "health":{"red":6,"max_red":6,"soul":0},"stats":{"damage":3.5,"move_speed":1,"max_fire_delay":10},
            "active":{"id":45,"charge":4,"max_charges":4,"ready":True}},
            "human_input":{"move":{"x":1,"y":0},"shoot":{"x":0,"y":-1},"active_trigger":n%60==0},
            "entities":[{"uid":"enemy-1","kind":"enemy","type":10,"position":{"x":300,"y":200},
            "velocity":{"x":-1,"y":0},"size":10,"hp":10,"max_hp":10,"vulnerable":True}],"execution":{}}
