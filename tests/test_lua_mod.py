"""Offline mod checks with a Lua runtime and explicit fake game/network objects."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/".local"/"testdeps"))
try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime=None

MOCK_GAME=r'''
TIME=3000; FRAME=0; INCOMING={}; SENT={}; CALLBACKS={}; HELD={}; PAUSED=false; PAUSE_MENU_STATE=0; ROOM_INDEX=17
PauseMenu={GetState=function()return PAUSE_MENU_STATE end}
REPENTOGON=true
ModCallbacks={MC_PRE_UPDATE=1026,MC_POST_GAME_STARTED=15,MC_POST_RENDER=2,MC_INPUT_ACTION=13,MC_ENTITY_TAKE_DMG=11,MC_USE_ITEM=3,MC_PRE_GAME_EXIT=17,MC_EXECUTE_CMD=22}
ButtonAction={ACTION_LEFT=0,ACTION_RIGHT=1,ACTION_UP=2,ACTION_DOWN=3,ACTION_SHOOTLEFT=4,ACTION_SHOOTRIGHT=5,ACTION_SHOOTUP=6,ACTION_SHOOTDOWN=7,ACTION_ITEM=9,ACTION_BOMB=8,ACTION_PILLCARD=10}
InputHook={IS_ACTION_PRESSED=0,IS_ACTION_TRIGGERED=1,GET_ACTION_VALUE=2}
ActiveSlot={SLOT_PRIMARY=0};EntityType={ENTITY_PLAYER=1};EntityFlag={FLAG_FRIENDLY=1,FLAG_CHARM=2};Keyboard={KEY_F8=119}
Input={GetActionValue=function(a,c)return HELD[a] and 1 or 0 end,IsActionPressed=function(a,c)return HELD[a] or false end,IsButtonPressed=function(a,c)return HELD[a] or false end}
local mod={AddCallback=function(self,k,f)CALLBACKS[k]=f end}
function RegisterMod()return mod end
local item={Name="Yum Heart",Description="Heal",MaxCharges=4,ChargeType=0}
local itemConfig={GetCollectible=function()return item end,GetCollectibles=function()return {Size=1} end}
PLAYER={Position={X=200,Y=200},Velocity={X=0,Y=0},Size=10,SizeMulti={X=1,Y=1},Index=0,ControllerIndex=0,Type=1,
Damage=3.5,MoveSpeed=1,ShotSpeed=1,MaxFireDelay=10,TearHeight=-23,TearFlags=0,CanFly=false}
function PLAYER:GetActiveItem()return 45 end
function PLAYER:GetActiveCharge()return 4 end
function PLAYER:GetBatteryCharge()return 0 end
function PLAYER:GetCollectibleCount()return 0 end
function PLAYER:GetCollectibleNum()return 0 end
function PLAYER:GetNumCoins()return 0 end
function PLAYER:GetNumKeys()return 1 end
function PLAYER:GetNumBombs()return 1 end
function PLAYER:GetTrinket()return 0 end
function PLAYER:GetCard()return 0 end
function PLAYER:GetPill()return 0 end
function PLAYER:GetPlayerType()return 0 end
function PLAYER:IsDead()return false end
function PLAYER:AreControlsEnabled()return true end
function PLAYER:GetHearts()return 6 end
function PLAYER:GetMaxHearts()return 6 end
function PLAYER:GetSoulHearts()return 0 end
function PLAYER:GetBlackHearts()return 0 end
local room={GetGridSize=function()return 0 end,GetGridWidth=function()return 13 end,GetDoor=function()return nil end,
GetType=function()return 1 end,GetRoomShape=function()return 1 end,IsClear=function()return true end,
GetTopLeftPos=function()return {X=0,Y=0} end,GetBottomRightPos=function()return {X=640,Y=560} end}
local level={GetStage=function()return 1 end,GetStageType=function()return 0 end,GetCurrentRoomIndex=function()return ROOM_INDEX end}
local game={GetNumPlayers=function()return 1 end,GetFrameCount=function()return FRAME end,
GetLevel=function()return level end,GetRoom=function()return room end,IsPaused=function()return PAUSED end,
GetSeeds=function()return {GetStartSeed=function()return 123 end} end}
function Game()return game end
Isaac={GetPlayer=function()return PLAYER end,GetItemConfig=function()return itemConfig end,GetTime=function()return TIME end,
GetRoomEntities=function()return {} end,RenderText=function()end}
package.preload["json"]=function()return {encode=pyencode,decode=pydecode} end
package.preload["socket.core"]=function()return {tcp=function()return {
settimeout=function()end,connect=function()return 1 end,close=function()end,
receive=function()if #INCOMING==0 then return nil,"timeout","" end;return nil,"timeout",table.remove(INCOMING,1) end,
send=function(self,payload)table.insert(SENT,payload);return #payload end} end} end
function step()CALLBACKS[1026](); FRAME=FRAME+1;TIME=TIME+33 end
function drain()local s=table.concat(SENT,"");SENT={};return s end
'''

@unittest.skipIf(LuaRuntime is None,"install lupa for offline Lua mod tests")
class LuaModTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().pyencode=lambda x:json.dumps(x)
        self.lua.globals().pydecode=lambda s:self.lua.table_from(json.loads(s),recursive=True)
        self.lua.execute(MOCK_GAME)
        self.lua.execute((ROOT/"mods"/"isaac_jev_bridge"/"main.lua").read_text(encoding="utf-8"))
        self.lua.execute("CALLBACKS[15](nil,false)")

    def messages(self):
        text=self.lua.eval("drain()")
        return [json.loads(l) for l in text.splitlines() if l]

    def step(self):self.lua.eval("step()")

    def test_console_connect_and_record_commands_do_not_change_input(self):
        self.step();self.messages()
        self.lua.eval('CALLBACKS[22]')(None,'jev_connect','10.1.2.3:31203 "dummy password" root')
        messages=self.messages();request=next(m for m in messages if m['type']=='management_request')
        self.assertEqual(request['host'],'10.1.2.3');self.assertEqual(request['password'],'dummy password')
        self.assertEqual(request['port'],31203)
        self.assertFalse(any(m.get('type')=='frame' and 'password' in m for m in messages))
        self.lua.eval('CALLBACKS[22]')(None,'jev_record','off')
        request=next(m for m in self.messages() if m['type']=='management_request')
        self.assertIs(request['enabled'],False)
        self.assertIsNone(self.lua.eval('CALLBACKS[13](nil,PLAYER,0,1)'))

    def test_hud_reports_tools_thinking_combat_and_expiry(self):
        self.step();self.messages()
        self.lua.execute('HUD_LINES={};Isaac.RenderText=function(text)table.insert(HUD_LINES,text)end')
        for state,tool,expected in [('tool','get_floor_map','Tool: get_floor_map'),('thinking','','Thinking...'),
                                    ('recent_tool','observe_game','Last tool: observe_game')]:
            self.lua.globals().INCOMING[1]=json.dumps({'protocol_version':1,'type':'presentation_status','ttl_ms':3500,
                'server':{'state':'ready'},'display':{'agent_state':state,'tool':tool,'controller_phase':'combat'}})+'\n'
            self.step();self.lua.execute('HUD_LINES={};CALLBACKS[2]()')
            lines=list(self.lua.globals().HUD_LINES.values())
            self.assertIn(expected,lines);self.assertTrue(any('Jev combat' in line for line in lines))
        self.lua.execute('TIME=TIME+6000;HUD_LINES={};CALLBACKS[2]()')
        self.assertIn('dsh service: offline',list(self.lua.globals().HUD_LINES.values()))

    def send(self,command):
        command.update(protocol_version=1,type="command")
        self.lua.globals().INCOMING[1]=json.dumps(command)+"\n"
        self.step()

    def test_update_before_action_and_empty_arrays_encode_correctly(self):
        self.step();self.step()
        packets=self.messages()
        frame=next(p for p in packets if p["type"]=="frame")
        self.assertEqual(frame["capture_phase"],"pre_update")
        self.assertEqual(frame["entities"],[])
        self.assertEqual(frame["room"]["grid"],[])
        self.assertEqual(frame["mode"],"manual")

    def test_recording_indicator_tracks_server_status_without_changing_controls(self):
        self.step()
        self.lua.execute('Isaac.RenderText=function(text)HUD_TEXT=text end')
        for recording,error,expected in [(True,False," | REC |"),(False,False," | NOT REC |"),(True,True," | REC ERROR |")]:
            self.lua.globals().INCOMING[1]=json.dumps({"protocol_version":1,"type":"recording_status",
                "recording":recording,"recording_error":error})+"\n"
            self.step();self.lua.execute("CALLBACKS[2]()")
            self.assertIn(expected,self.lua.globals().HUD_TEXT)
            self.assertIn("manual",self.lua.globals().HUD_TEXT)

    def test_bomb_press_and_edge_are_saved_in_raw_input(self):
        self.step();self.step();self.messages()
        self.lua.execute("HELD[8]=true; CALLBACKS[2]()")
        self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertTrue(frame["human_input"]["bomb_trigger"])
        self.assertTrue(frame["human_input"]["bomb_pressed"])
        self.lua.execute("CALLBACKS[2]()");self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertFalse(frame["human_input"]["bomb_trigger"])
        self.assertTrue(frame["human_input"]["bomb_pressed"])

    def test_manual_shadow_and_expired_actions_never_inject(self):
        self.step();self.step();frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        action={"command":"action","request_id":"a1","action_id":20,"move":{"x":1,"y":0},"shoot":{"x":0,"y":0},
                "use_active":False,"observation_frame":1,"valid_until_frame":7,"run_id":frame["run_id"],"room_id":frame["room"]["id"]}
        self.send(action)
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.send({"command":"set_mode","mode":"shadow","request_id":"mode1"})
        action["request_id"]="a2";self.send(action)
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.send({"command":"set_mode","mode":"ai","request_id":"mode2"})
        action.update(request_id="a3",valid_until_frame=2)
        self.send(action);self.step()
        acks=[p for p in self.messages() if p["type"]=="command_ack"]
        self.assertTrue(any(p["request_id"]=="a3" and not p["ok"] for p in acks))

    def test_active_item_is_a_one_frame_edge_and_f8_releases_control(self):
        self.step();self.step();frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        current=int(self.lua.globals().FRAME)
        action={"command":"action","request_id":"active1","action_id":20,"move":{"x":1,"y":0},"shoot":{"x":0,"y":0},
                "use_active":True,"observation_frame":current,"valid_until_frame":current+6,
                "run_id":frame["run_id"],"room_id":frame["room"]["id"]}
        self.send(action)
        # step increments the mock clock; inspect the accepted frame and the following frame.
        self.lua.globals().FRAME=current
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,9)"))
        self.lua.globals().FRAME=current+1
        self.assertFalse(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,9)"))
        self.lua.execute("HELD[119]=true; CALLBACKS[2]()")
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))

    def test_128_bit_flags_keep_high_bits_and_localization_keys_are_resolved(self):
        self.lua.execute('PLAYER.TearFlags={l=1,h=-9223372036854775807-1}; Isaac.GetLocalizedString=function(category,key,language) assert(category=="Items" and language=="en"); return "translated:"..key end; Isaac.GetItemConfig().GetCollectible=function() return {Name="#BOOK_NAME",Description="#BOOK_DESCRIPTION",MaxCharges=4} end')
        self.step();self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(frame["player"]["stats"]["tear_flags"],{"encoding":"bitset128","low_hex":"0000000000000001","high_hex":"8000000000000000"})
        self.assertEqual(frame["player"]["active"]["name"],"translated:BOOK_NAME")
        self.assertEqual(frame["player"]["active"]["name_key"],"#BOOK_NAME")

    def test_actual_item_use_is_logged_without_overriding_game_behavior(self):
        self.step();self.messages()
        result=self.lua.eval('CALLBACKS[3](nil,45,nil,PLAYER,0,0,0)')
        self.assertIsNone(result)
        self.step()  # Event transport flushes on the following pre-update.
        event=next(p for p in self.messages() if p.get("event")=="item_used")
        self.assertEqual(event["item_id"],45)
        self.assertEqual(event["active_slot"],0)

    def test_continued_run_keeps_the_same_dataset_group(self):
        self.step();self.step()
        first=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.lua.execute('TIME=TIME+100000; CALLBACKS[15](nil,true)')
        self.step();self.step()
        continued=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(first["run_id"],continued["run_id"])

    def test_late_command_holds_two_actual_input_frames_and_active_fires_once(self):
        self.step();self.step();frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        action={"command":"action","request_id":"delayed","action_id":20,"move":{"x":1,"y":0},"shoot":{"x":0,"y":0},
                "use_active":True,"hold_frames":2,"observation_frame":1,"valid_until_frame":7,
                "run_id":frame["run_id"],"room_id":frame["room"]["id"]}
        self.send(action)
        # It arrived in frame 3; game input is first queried in frame 4.
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,9)"))
        self.lua.globals().FRAME=5
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.assertFalse(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,9)"))
        self.lua.globals().FRAME=6
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))

    def test_door_transition_preserves_ai_but_clears_old_room_action(self):
        self.step();self.step();frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        current=int(self.lua.globals().FRAME)
        self.send({"command":"action","request_id":"before-door","action_id":20,
            "move":{"x":1,"y":0},"shoot":{"x":0,"y":0},"use_active":False,"hold_frames":3,
            "observation_frame":current,"valid_until_frame":current+6,
            "run_id":frame["run_id"],"room_id":frame["room"]["id"]})
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.lua.execute("PAUSED=true; PAUSE_MENU_STATE=0; CALLBACKS[2]()")
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.lua.execute("PAUSED=false; ROOM_INDEX=18")
        self.step();self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(frame["mode"],"ai")
        self.assertEqual(frame["room"]["id"],"1:0:18")
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        current=int(self.lua.globals().FRAME)
        self.send({"command":"action","request_id":"after-door","action_id":20,
            "move":{"x":1,"y":0},"shoot":{"x":0,"y":0},"use_active":False,"hold_frames":3,
            "observation_frame":current,"valid_until_frame":current+6,
            "run_id":frame["run_id"],"room_id":frame["room"]["id"]})
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))

    def test_fresh_late_action_gets_full_hold_but_cannot_start_after_deadline(self):
        self.step();self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        source=int(self.lua.globals().FRAME)
        self.lua.globals().FRAME=source+4
        self.send({"command":"action","request_id":"late-full","action_id":20,
            "move":{"x":1,"y":0},"shoot":{"x":0,"y":0},"use_active":False,"hold_frames":6,
            "execution_timing":"start-relative-v1","observation_frame":source,"valid_until_frame":source+6,
            "run_id":"isaac-seed:123","room_id":"1:0:17"})
        # Accepted at age 4, starts at age 5, runs all six actual frames.
        for n in range(source+5,source+11):
            self.lua.globals().FRAME=n
            self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.lua.globals().FRAME=source+11
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.step();packets=self.messages()
        start=next(p for p in packets if p.get("event")=="action_started")
        self.assertEqual(start["stop_frame"]-start["game_frame"],6)
        self.assertTrue(any(p.get("event")=="action_ended" and p.get("reason")=="hold_finished" for p in packets))
        source=int(self.lua.globals().FRAME);self.lua.globals().FRAME=source+5
        self.send({"command":"action","request_id":"too-late-to-start","action_id":20,
            "move":{"x":1,"y":0},"shoot":{"x":0,"y":0},"use_active":False,"hold_frames":6,
            "execution_timing":"start-relative-v1","observation_frame":source,"valid_until_frame":source+6,
            "run_id":"isaac-seed:123","room_id":"1:0:17"})
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.step()
        self.assertTrue(any(p.get("event")=="action_discarded" and p.get("reason")=="expired_before_start" for p in self.messages()))

    def test_input_query_telemetry_and_replacement_show_actual_frames(self):
        self.step();self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        current=int(self.lua.globals().FRAME)
        action={"command":"action","request_id":"first","action_id":20,
            "move":{"x":1,"y":0},"shoot":{"x":0,"y":0},"use_active":False,"hold_frames":6,
            "execution_timing":"start-relative-v1","observation_frame":current,"valid_until_frame":current+6,
            "run_id":"isaac-seed:123","room_id":"1:0:17"}
        self.send(action);n=int(self.lua.globals().FRAME)
        self.assertTrue(self.lua.eval("CALLBACKS[13](nil,PLAYER,2,1)"))
        self.step();latest=[p for p in self.messages() if p.get("type")=="frame"][-1]
        self.assertEqual(latest["game_frame"],n) # Same-frame telemetry was already flushed.
        applied=latest["applied_controls"]
        self.assertEqual(applied["game_frame"],n);self.assertEqual(applied["requests"][0]["pressed"],{"1":True})
        current=int(self.lua.globals().FRAME)
        self.send({**action,"request_id":"second","action_id":0,"move":{"x":0,"y":0},
            "observation_frame":current,"valid_until_frame":current+6})
        self.assertFalse(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))
        self.step();packets=self.messages()
        self.assertTrue(any(p.get("event")=="action_ended" and p.get("reason")=="replaced" and p.get("request_id")=="first" for p in packets))
        latest=[p for p in packets if p.get("type")=="frame"][-1]
        self.assertEqual(latest["applied_controls"]["requests"][0]["action_id"],0)

    def test_delayed_continuous_commands_cover_input_without_unbounded_hold(self):
        self.step();self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        covered=[]
        for i in range(40):
            now=int(self.lua.globals().FRAME)
            if i%3==0:
                self.send({"command":"action","request_id":str(i),"action_id":20,
                    "move":{"x":1,"y":0},"shoot":{"x":0,"y":0},"use_active":False,"hold_frames":6,
                    "execution_timing":"start-relative-v1","observation_frame":now-4,"valid_until_frame":now+2,
                    "run_id":"isaac-seed:123","room_id":"1:0:17"})
            else:self.step()
            covered.append(bool(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)")))
        self.assertTrue(all(covered))
        self.lua.globals().FRAME=int(self.lua.globals().FRAME)+6
        self.assertIsNone(self.lua.eval("CALLBACKS[13](nil,PLAYER,0,1)"))

    def test_pause_menu_and_options_relinquish_ai_and_record_cause(self):
        self.step()
        for menu_state in (1,2):
            self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
            self.lua.globals().PAUSED=True;self.lua.globals().PAUSE_MENU_STATE=menu_state
            self.lua.execute("CALLBACKS[2](); CALLBACKS[2]()")
            self.lua.globals().PAUSED=False;self.lua.globals().PAUSE_MENU_STATE=0
            self.step();self.step()
            packets=self.messages();frame=[p for p in packets if p["type"]=="frame"][-1]
            self.assertEqual(frame["mode"],"manual")
            self.assertEqual(frame["mode_reason"],"pause_menu")
            changes=[p for p in packets if p.get("event")=="control_mode_changed" and p.get("reason")=="pause_menu"]
            self.assertEqual(len(changes),1)

    def test_f8_during_transition_still_relinquishes_ai(self):
        self.step();self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        self.lua.execute("PAUSED=true; PAUSE_MENU_STATE=0; HELD[119]=true; CALLBACKS[2](); HELD[119]=false; PAUSED=false")
        self.step();self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(frame["mode"],"manual")
        self.assertEqual(frame["mode_reason"],"f8")

    def test_missing_pause_menu_api_keeps_manual_fallback(self):
        self.step();self.send({"command":"set_mode","mode":"ai","request_id":"mode"})
        self.lua.execute("PauseMenu=nil; PAUSED=true; CALLBACKS[2](); PAUSED=false")
        self.step();self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(frame["mode"],"manual")

    def test_pending_mode_request_cannot_override_f8_during_transition(self):
        self.step()
        self.lua.globals().INCOMING[1]=json.dumps({"protocol_version":1,"type":"command",
            "command":"set_mode","mode":"ai","request_id":"pending"})+"\n"
        self.lua.execute("PAUSED=true; PAUSE_MENU_STATE=0; HELD[119]=true; CALLBACKS[2](); HELD[119]=false; PAUSED=false")
        self.step();self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(frame["mode"],"manual")
        self.assertEqual(frame["mode_reason"],"f8")

    def test_planner_exports_floor_links_and_navigation_cells(self):
        self.lua.execute('''
        local level=Game():GetLevel(); local room=Game():GetRoom()
        local neighbor={SafeGridIndex=16,GridIndex=16,DisplayFlags=0,VisitedCount=0,Clear=false,AllowedDoors=4,Data={Type=4,Shape=1},GetDimension=function()return 0 end,GetNeighboringRooms=function()return {} end}
        local current={SafeGridIndex=17,GridIndex=17,DisplayFlags=1,VisitedCount=1,Clear=true,AllowedDoors=1,Data={Type=1,Shape=1},GetDimension=function()return 0 end,GetNeighboringRooms=function()return {[0]=neighbor} end}
        level.GetRooms=function()return {Size=2,Get=function(self,i)return i==0 and current or neighbor end} end
        level.GetCurrentRoomDesc=function()return current end;level.GetDimension=function()return 0 end
        room.GetGridSize=function()return 9 end;room.GetGridWidth=function()return 3 end
        room.GetGridPosition=function(self,i)return {X=80+(i%3)*40,Y=80+math.floor(i/3)*40} end
        room.GetGridEntity=function()return nil end;room.GetGridCollision=function()return 0 end
        room.GetDoor=function(self,slot) if slot==0 then return {Position={X=60,Y=120},TargetRoomType=4,IsOpen=function()return true end,IsLocked=function()return false end} end end
        ''')
        self.step();self.step()
        frame=[p for p in self.messages() if p["type"]=="frame"][-1]
        self.assertEqual(frame["planner_schema"],1)
        self.assertEqual(len(frame["room"]["navigation_cells"]),9)
        self.assertEqual(frame["room"]["navigation_cells"][0],[0,80,80,0,True])
        self.assertEqual(frame["room"]["doors"][0]["target_room_index"],16)
        self.assertEqual(frame["floor"]["rooms"][0]["links"][0]["target_index"],16)

if __name__=="__main__":unittest.main()
