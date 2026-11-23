-- Original project bridge. Network/input patterns reference the Isaac and REPENTOGON API docs.
-- Manual by default; never skip game updates, alter positions, damage, saves, or achievements.
local mod = RegisterMod("Isaac Jev Bridge", 1)
local json = require("json")
local VERSION, HOST, PORT = 1, "127.0.0.1", 9527
local mode, sock, rx, tx = "manual", nil, "", ""
local retryAt, runId, sequence = 0, "", 0
local command, lastRequest, lastRoom = nil, nil, nil
local pendingEvents, inputEdges = {}, {active_trigger=false, bomb_trigger=false, card_trigger=false}
local previousHeld, itemCache, inventoryCount = {}, {}, -1
local rawFrame, lastError, drops = nil, "", 0
local recording, recordingError = false, false

local function arr(t) return setmetatable(t or {}, {__json_array=true}) end
local function read(o, k, default)
    local ok, v = pcall(function() return o[k] end)
    if ok and v ~= nil then return v end
    return default
end
local function call(o, k, default, ...)
    local args = {...}
    local ok, v = pcall(function() return o[k](o, table.unpack(args)) end)
    if ok and v ~= nil then return v end
    return default
end
local function vec(v)
    return {x=read(v,"X",0), y=read(v,"Y",0)}
end
local function flagData(flags)
    if type(flags)=="number" or type(flags)=="string" then return flags end
    local low,high=read(flags,"l",nil),read(flags,"h",nil)
    if type(low)=="number" and type(high)=="number" then
        -- Hex preserves all 128 bits, including signed 64-bit halves, without
        -- conversion through a JSON consumer's floating-point number type.
        return {encoding="bitset128",low_hex=string.format("%016x",low),high_hex=string.format("%016x",high)}
    end
    return nil
end
local function itemText(raw)
    if type(raw)~="string" or raw:sub(1,1)~="#" then return raw or "" end
    if Isaac.GetLocalizedString then
        local ok,text=pcall(Isaac.GetLocalizedString,"Items",raw:sub(2),"en")
        if ok and type(text)=="string" and text~="" then return text end
    end
    return raw
end
local function encode(v)
    local kind=type(v)
    if kind=="nil" then return "null" end
    if kind=="boolean" then return v and "true" or "false" end
    if kind=="number" then
        if v~=v or v==math.huge or v==-math.huge then return "null" end
        return tostring(v)
    end
    if kind=="string" then return json.encode(v) end
    if kind~="table" then return "null" end
    local out={}
    if getmetatable(v) and getmetatable(v).__json_array then
        for i=1,#v do out[#out+1]=encode(v[i]) end
        return "["..table.concat(out,",").."]"
    end
    for k,value in pairs(v) do out[#out+1]=json.encode(tostring(k))..":"..encode(value) end
    return "{"..table.concat(out,",").."}"
end
local function emit(msg)
    if not sock then return false end
    msg.protocol_version=VERSION
    local line=encode(msg).."\n"
    if #tx+#line>4*1024*1024 then drops=drops+1;return false end
    tx=tx..line
    return true
end
local function clearControl() command=nil;mode="manual" end
local function disconnect(reason)
    if sock then pcall(function() sock:close() end) end
    sock=nil;rx="";tx="";clearControl();recording=false;recordingError=false;lastError=reason or "disconnected"
end
local function frameNo() return math.floor(Game():GetFrameCount()) end
-- Keep continued games and reloaded mods in the same training split. Replays
-- of the same seed are also grouped, so validation cannot reuse their rooms.
local function runKey() return "isaac-seed:"..tostring(Game():GetSeeds():GetStartSeed()) end
local function roomId()
    local l=Game():GetLevel()
    return tostring(l:GetStage())..":"..tostring(l:GetStageType())..":"..tostring(l:GetCurrentRoomIndex())
end
local function connect()
    if sock or Isaac.GetTime()<retryAt then return end
    retryAt=Isaac.GetTime()+2000
    local ok,core=pcall(require,"socket.core")
    if not ok then lastError="socket.core unavailable: enable --luadebug";return end
    local s=core.tcp();s:settimeout(0.01)
    local connected,err=s:connect(HOST,PORT)
    if not connected then s:close();lastError=tostring(err);return end
    s:settimeout(0);sock=s;rx="";tx="";clearControl();lastError=""
    emit({type="hello",client="isaac_mod",mod_version="0.3.2",capture_phase="pre_update",
          repentogon=REPENTOGON~=nil,run_id=runId,logic_hz=30})
    print("[IsaacJev] connected to Python; MANUAL mode")
end
local function acknowledge(msg,ok,reason)
    emit({type="command_ack",request_id=msg.request_id,ok=ok,reason=reason,
          mode=mode,game_frame=frameNo(),room_id=roomId()})
end
local function direction(v)
    return type(v)=="table" and (v.x==-1 or v.x==0 or v.x==1) and (v.y==-1 or v.y==0 or v.y==1)
end
local function handle(msg)
    if msg.protocol_version~=VERSION then return end
    if msg.type=="hello_ack" or msg.type=="recording_status" then
        recording=msg.recording==true;recordingError=msg.recording_error==true;return
    end
    if msg.type~="command" then return end
    if msg.command=="set_mode" then
        if msg.mode~="manual" and msg.mode~="shadow" and msg.mode~="ai" then acknowledge(msg,false,"invalid mode");return end
        command=nil;mode=msg.mode;acknowledge(msg,true,"mode changed");return
    end
    if msg.command=="ping" then acknowledge(msg,true,"pong");return end
    if msg.command~="action" then acknowledge(msg,false,"unknown command");return end
    if msg.request_id==lastRequest then acknowledge(msg,false,"duplicate request");return end
    local now=frameNo()
    if msg.run_id~=runId or msg.room_id~=roomId() then acknowledge(msg,false,"run/room mismatch");return end
    if type(msg.observation_frame)~="number" or type(msg.valid_until_frame)~="number"
       or msg.observation_frame>now or msg.valid_until_frame<=now
       or msg.valid_until_frame-msg.observation_frame<1 or msg.valid_until_frame-msg.observation_frame>6 then
        acknowledge(msg,false,"expired/future command");return
    end
    if not direction(msg.move) or not direction(msg.shoot) or (msg.shoot.x~=0 and msg.shoot.y~=0)
       or type(msg.action_id)~="number" or msg.action_id<0 or msg.action_id>=45
       or type(msg.use_active)~="boolean" then acknowledge(msg,false,"invalid action");return end
    if mode=="manual" then acknowledge(msg,false,"manual mode");return end
    local hold=msg.hold_frames or (msg.valid_until_frame-msg.observation_frame)
    if type(hold)~="number" or hold~=math.floor(hold) or hold<1 or hold>6 then acknowledge(msg,false,"invalid hold duration");return end
    command=msg;command.hold_frames=hold;command.started_frame=nil;command.active_frame=nil;command.stop_frame=nil;lastRequest=msg.request_id
    acknowledge(msg,true,mode=="shadow" and "shadow only" or "accepted")
end
local function network()
    connect()
    if not sock then return end
    for _=1,16 do
        local data,err,partial=sock:receive(8192)
        rx=rx..(data or partial or "")
        if #rx>2*1024*1024 then disconnect("receive buffer overflow");return end
        if err=="closed" then disconnect("Python disconnected");return end
        if not data then break end
    end
    for _=1,32 do
        local at=rx:find("\n",1,true)
        if not at then break end
        local line=rx:sub(1,at-1);rx=rx:sub(at+1)
        local ok,msg=pcall(json.decode,line)
        if ok and type(msg)=="table" then handle(msg) else lastError="invalid control JSON" end
    end
    if #tx>0 then
        local sent,err,partial=sock:send(tx)
        local count=sent or partial or 0
        if count>0 then tx=tx:sub(count+1) end
        if err=="closed" then disconnect("Python disconnected") end
    end
end
local function player0()
    if Game():GetNumPlayers()<1 then return nil end
    return Isaac.GetPlayer(0)
end
local function humanInput(p)
    local c=p.ControllerIndex
    local function value(a) return Input.GetActionValue(a,c) end
    return {move={x=value(ButtonAction.ACTION_RIGHT)-value(ButtonAction.ACTION_LEFT),
                  y=value(ButtonAction.ACTION_DOWN)-value(ButtonAction.ACTION_UP)},
            shoot={x=value(ButtonAction.ACTION_SHOOTRIGHT)-value(ButtonAction.ACTION_SHOOTLEFT),
                   y=value(ButtonAction.ACTION_SHOOTDOWN)-value(ButtonAction.ACTION_SHOOTUP)},
            active_pressed=Input.IsActionPressed(ButtonAction.ACTION_ITEM,c),
            active_trigger=inputEdges.active_trigger, bomb_trigger=inputEdges.bomb_trigger,
            bomb_pressed=Input.IsActionPressed(ButtonAction.ACTION_BOMB,c),
            card_trigger=inputEdges.card_trigger}
end
local function activeItem(p)
    local slot=ActiveSlot.SLOT_PRIMARY
    local id=p:GetActiveItem(slot)
    local config=id>0 and Isaac.GetItemConfig():GetCollectible(id) or nil
    local max=read(config,"MaxCharges",0)
    local charge=p:GetActiveCharge(slot)
    return {id=id,charge=charge,battery=call(p,"GetBatteryCharge",0,slot),max_charges=max,
            name=itemText(read(config,"Name","")),description=itemText(read(config,"Description","")),
            name_key=read(config,"Name",""),description_key=read(config,"Description",""),
            charge_type=read(config,"ChargeType",nil),
            ready=id>0 and (max==0 or charge>=max)}
end
local function inventory(p)
    local count=p:GetCollectibleCount()
    if count~=inventoryCount then
        inventoryCount=count;itemCache=arr()
        local configs=Isaac.GetItemConfig():GetCollectibles()
        for id=1,configs.Size-1 do
            local n=p:GetCollectibleNum(id,true)
            if n>0 then
                local item=Isaac.GetItemConfig():GetCollectible(id)
                itemCache[#itemCache+1]={id=id,count=n,name=itemText(read(item,"Name","")),description=itemText(read(item,"Description","")),
                                       name_key=read(item,"Name",""),description_key=read(item,"Description","")}
            end
        end
    end
    return {coins=p:GetNumCoins(),keys=p:GetNumKeys(),bombs=p:GetNumBombs(),
            trinkets=arr({p:GetTrinket(0),p:GetTrinket(1)}),collectibles=itemCache,
            card=p:GetCard(0),pill_color=p:GetPill(0)}
end
local function entityData(e)
    local sprite=call(e,"GetSprite",nil)
    local n=call(e,"ToNPC",nil)
    local projectile=call(e,"ToProjectile",nil)
    local tear=call(e,"ToTear",nil)
    local laser=call(e,"ToLaser",nil)
    local bomb=call(e,"ToBomb",nil)
    local pickup=call(e,"ToPickup",nil)
    local effect=call(e,"ToEffect",nil)
    local kind=n and "enemy" or projectile and "projectile" or tear and "tear"
               or laser and "laser" or bomb and "bomb" or pickup and "pickup" or effect and "effect" or "other"
    local flags=call(e,"GetEntityFlags",0)
    local friendly=e:HasEntityFlags(EntityFlag.FLAG_FRIENDLY) or e:HasEntityFlags(EntityFlag.FLAG_CHARM)
    return {uid=tostring(e.Index)..":"..tostring(e.InitSeed),kind=kind,type=e.Type,variant=e.Variant,subtype=e.SubType,
            position=vec(e.Position),velocity=vec(e.Velocity),size=e.Size,size_multi=vec(e.SizeMulti),
            hp=e.HitPoints,max_hp=e.MaxHitPoints,frame_count=e.FrameCount,
            collision_class=e.EntityCollisionClass,grid_collision_class=e.GridCollisionClass,
            flags=flags,friendly=friendly,visible=e.Visible,dead=e:IsDead(),
            vulnerable=call(e,"IsVulnerableEnemy",false),active_enemy=call(e,"IsActiveEnemy",false,false),
            animation=call(sprite,"GetAnimation",""),animation_frame=call(sprite,"GetFrame",0),
            state=read(n,"State",nil),state_frame=read(n,"StateFrame",nil),
            projectile_flags=flagData(read(projectile,"ProjectileFlags",nil)),tear_flags=flagData(read(tear,"TearFlags",nil)),height=read(projectile or tear,"Height",nil),
            falling_speed=read(projectile or tear,"FallingSpeed",nil),falling_accel=read(projectile or tear,"FallingAccel",nil),
            laser_angle=read(laser,"AngleDegrees",nil),laser_endpoint=laser and vec(read(laser,"EndPoint",nil)) or nil,
            laser_radius=read(laser,"Radius",nil),laser_timeout=read(laser,"Timeout",nil),
            bomb_countdown=call(bomb,"GetExplosionCountdown",read(bomb,"ExplosionCountdown",nil)),
            bomb_radius_multiplier=read(bomb,"RadiusMultiplier",nil),
            pickup_price=read(pickup,"Price",nil),effect_timeout=read(effect,"Timeout",nil)}
end
local function roomData(p)
    local r=Game():GetRoom()
    local grid=arr()
    for i=0,r:GetGridSize()-1 do
        local g=r:GetGridEntity(i)
        if g then
            local desc=read(g,"Desc",nil)
            grid[#grid+1]={index=i,position=vec(r:GetGridPosition(i)),type=call(g,"GetType",nil),
                           collision=r:GetGridCollision(i),state=read(g,"State",nil),
                           variant=read(desc,"Variant",nil)}
        end
    end
    local doors=arr()
    for slot=0,7 do
        local d=r:GetDoor(slot)
        if d then doors[#doors+1]={slot=slot,position=vec(d.Position),open=d:IsOpen(),locked=d:IsLocked(),
                                  target_room_type=d.TargetRoomType} end
    end
    return {id=roomId(),type=r:GetType(),shape=r:GetRoomShape(),clear=r:IsClear(),
            grid_width=r:GetGridWidth(),grid_size=r:GetGridSize(),
            top_left=vec(r:GetTopLeftPos()),bottom_right=vec(r:GetBottomRightPos()),grid=grid,doors=doors}
end
local function snapshot()
    local p=player0()
    if not p then return nil end
    local entities=arr()
    for _,e in ipairs(Isaac.GetRoomEntities()) do
        if e.Type~=EntityType.ENTITY_PLAYER then entities[#entities+1]=entityData(e) end
    end
    local held=humanInput(p)
    local currentRoom=roomId()
    if currentRoom~=lastRoom then command=nil;lastRoom=currentRoom end
    sequence=sequence+1
    return {type="frame",protocol_version=VERSION,run_id=runId,seq=sequence,game_frame=frameNo(),
            capture_phase="pre_update",game_time_ms=Isaac.GetTime(),mode=mode,room=roomData(p),
            player={position=vec(p.Position),velocity=vec(p.Velocity),size=p.Size,size_multi=vec(p.SizeMulti),
                    type=p:GetPlayerType(),dead=p:IsDead(),can_fly=p.CanFly,controls_enabled=p:AreControlsEnabled(),
                    health={red=p:GetHearts(),max_red=p:GetMaxHearts(),soul=p:GetSoulHearts(),
                            black_mask=p:GetBlackHearts(),bone=call(p,"GetBoneHearts",0),rotten=call(p,"GetRottenHearts",0)},
                    stats={damage=p.Damage,move_speed=p.MoveSpeed,shot_speed=p.ShotSpeed,
                           max_fire_delay=p.MaxFireDelay,tear_height=p.TearHeight,tear_flags=flagData(p.TearFlags)},
                    active=activeItem(p),inventory=inventory(p)},
            human_input=held,entities=entities,execution=command or {},
            bridge={dropped_frames=drops},supported_player_count=Game():GetNumPlayers()}
end

mod:AddCallback(ModCallbacks.MC_POST_GAME_STARTED,function(_,continued)
    runId=runKey()
    sequence=0;lastRoom=nil;inventoryCount=-1;command=nil;previousHeld={}
    emit({type="event",event="run_started",run_id=runId,continued=continued,game_frame=frameNo()})
end)
if ModCallbacks.MC_PRE_UPDATE then
    mod:AddCallback(ModCallbacks.MC_PRE_UPDATE,function()
        network()
        if runId=="" then runId=runKey() end
        local ok,frame=pcall(snapshot)
        if ok and frame then
            rawFrame=frame
            emit(frame)
            inputEdges={active_trigger=false,bomb_trigger=false,card_trigger=false}
        elseif not ok then lastError=tostring(frame);print("[IsaacJev] capture error: "..lastError) end
        -- nil return: do not cancel or pause the game update.
    end)
else
    print("[IsaacJev] REPENTOGON MC_PRE_UPDATE unavailable; recording disabled")
    lastError="REPENTOGON MC_PRE_UPDATE unavailable"
end
mod:AddCallback(ModCallbacks.MC_POST_RENDER,function()
    local p=player0()
    if p then
        for key,action in pairs({active_trigger=ButtonAction.ACTION_ITEM,bomb_trigger=ButtonAction.ACTION_BOMB,card_trigger=ButtonAction.ACTION_PILLCARD}) do
            local held=Input.IsActionPressed(action,p.ControllerIndex)
            if held and not previousHeld[key] then inputEdges[key]=true end
            previousHeld[key]=held
        end
        if Input.IsButtonPressed(Keyboard.KEY_F8,p.ControllerIndex) then clearControl() end
    end
    if Game():IsPaused() then clearControl();network() end
    local rec=recordingError and "REC ERROR" or recording and "REC" or "NOT REC"
    Isaac.RenderText("Isaac Jev: "..mode.." | "..(sock and "CONNECTED" or lastError).." | "..rec.." | F8: HUMAN",10,55,1,1,1,1)
end)
mod:AddCallback(ModCallbacks.MC_INPUT_ACTION,function(_,entity,hook,action)
    if mode~="ai" or not command or not sock or Game():IsPaused() then return nil end
    local p=player0()
    if not p or not entity or entity.Type~=EntityType.ENTITY_PLAYER or entity.Index~=p.Index then return nil end
    if command.run_id~=runId or command.room_id~=roomId() or frameNo()>=command.valid_until_frame then command=nil;return nil end
    local active
    if action==ButtonAction.ACTION_LEFT then active=command.move.x<0
    elseif action==ButtonAction.ACTION_RIGHT then active=command.move.x>0
    elseif action==ButtonAction.ACTION_UP then active=command.move.y<0
    elseif action==ButtonAction.ACTION_DOWN then active=command.move.y>0
    elseif action==ButtonAction.ACTION_SHOOTLEFT then active=command.shoot.x<0
    elseif action==ButtonAction.ACTION_SHOOTRIGHT then active=command.shoot.x>0
    elseif action==ButtonAction.ACTION_SHOOTUP then active=command.shoot.y<0
    elseif action==ButtonAction.ACTION_SHOOTDOWN then active=command.shoot.y>0
    elseif action==ButtonAction.ACTION_ITEM then active=command.use_active
    else return nil end
    local now=frameNo()
    if not command.started_frame then
        command.started_frame=now;command.active_frame=now
        command.stop_frame=math.min(now+command.hold_frames,command.valid_until_frame)
        emit({type="event",event="action_started",request_id=command.request_id,run_id=runId,
              game_frame=now,observation_frame=command.observation_frame,action_id=command.action_id,
              use_active=command.use_active,stop_frame=command.stop_frame})
    end
    if now>=command.stop_frame then command=nil;return nil end
    if action==ButtonAction.ACTION_ITEM then active=active and now==command.active_frame end
    if hook==InputHook.GET_ACTION_VALUE then return active and 1.0 or 0.0 end
    if hook==InputHook.IS_ACTION_PRESSED or hook==InputHook.IS_ACTION_TRIGGERED then return active end
    return nil
end)
mod:AddCallback(ModCallbacks.MC_ENTITY_TAKE_DMG,function(_,entity,amount,flags,source,countdown)
    emit({type="event",event="damage",run_id=runId,game_frame=frameNo(),entity_type=entity.Type,
          entity_uid=tostring(entity.Index)..":"..tostring(entity.InitSeed),amount=amount,flags=flags})
end)
mod:AddCallback(ModCallbacks.MC_USE_ITEM,function(_,id,rng,p,flags,slot,variableData)
    emit({type="event",event="item_used",run_id=runId,game_frame=frameNo(),item_id=id,
          player_uid=p and (tostring(p.Index)..":"..tostring(p.InitSeed)) or nil,
          use_flags=flags,active_slot=slot,variable_data=variableData})
    -- nil return: observe use without cancelling or changing charge consumption.
end)
mod:AddCallback(ModCallbacks.MC_PRE_GAME_EXIT,function() clearControl();rawFrame=nil end)
