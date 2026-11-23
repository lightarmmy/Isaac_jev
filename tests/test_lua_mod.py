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
TIME=3000; FRAME=0; INCOMING={}; SENT={}; CALLBACKS={}; HELD={}; PAUSED=false
REPENTOGON=true
ModCallbacks={MC_PRE_UPDATE=1026,MC_POST_GAME_STARTED=15,MC_POST_RENDER=2,MC_INPUT_ACTION=13,MC_ENTITY_TAKE_DMG=11,MC_USE_ITEM=3,MC_PRE_GAME_EXIT=17}
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
local level={GetStage=function()return 1 end,GetStageType=function()return 0 end,GetCurrentRoomIndex=function()return 17 end}
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

if __name__=="__main__":unittest.main()
