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
local controlReason, controlRevision = "initial", 0
local floorCache, floorCacheAt, floorCacheKey = nil, -1000, ""
local inventoryCheckedAt = -1000
local inventoryFingerprint = ""
local appliedControls = nil
local EXECUTION_TIMING = "start-relative-v1"
local serverStatus, agentDisplay = {state="unknown"}, {agent_state="offline"}
local displayExpires, noticeExpires, notice, hudVisible = 0, 0, "", true
local HUD_X, HUD_Y = 10, 155
local shootingLatch, lastWeapons, lastShoot, shootHeldFrames = nil, {}, nil, 0

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
local function optionalVec(v)
    local x,y=read(v,"X",nil),read(v,"Y",nil)
    if type(x)~="number" or type(y)~="number" then return nil end
    return {x=x,y=y}
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
local function clearCommand(reason)
    if command then
        emit({type="event",event=command.started_frame and "action_ended" or "action_discarded",
              request_id=command.request_id,run_id=command.run_id,room_id=command.room_id,
              game_frame=math.floor(Game():GetFrameCount()),observation_frame=command.observation_frame,
              started_frame=command.started_frame,stop_frame=command.stop_frame,reason=reason})
    end
    command=nil
end
local function setControlMode(nextMode,reason)
    shootingLatch=nil
    clearCommand(reason or "mode_changed")
    local previous=mode
    mode=nextMode;controlReason=reason or "requested_mode";controlRevision=controlRevision+1
    if previous~=nextMode then
        emit({type="event",event="control_mode_changed",run_id=runId,
              game_frame=math.floor(Game():GetFrameCount()),room_id=lastRoom,
              previous_mode=previous,mode=mode,reason=controlReason,control_revision=controlRevision})
    end
end
local function clearControl(reason)
    shootingLatch=nil
    clearCommand(reason or "manual_reset")
    -- Repeated paused renders must not flood the raw event stream.
    if mode~="manual" then setControlMode("manual",reason or "manual_reset") end
end
local function humanPauseOpen()
    -- IsPaused also covers door transitions and boss intros. REPENTOGON exposes
    -- the actual pause menu: 0=CLOSED, 1=OPEN, 2=OPTIONS; use the documented dot call.
    if PauseMenu and type(PauseMenu.GetState)=="function" then
        local ok,state=pcall(PauseMenu.GetState)
        if ok and type(state)=="number" then return state~=0 end
    end
    return Game():IsPaused() -- Older/missing API: retain manual fallback.
end
local function disconnect(reason)
    if sock then pcall(function() sock:close() end) end
    sock=nil;rx="";tx="";clearControl("disconnected");recording=false;recordingError=false;lastError=reason or "disconnected"
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
    s:settimeout(0);pcall(function() s:setoption("tcp-nodelay",true) end)
    sock=s;rx="";tx="";clearControl("reconnected");lastError=""
    emit({type="hello",client="isaac_mod",mod_version="0.7.0",input_timing=EXECUTION_TIMING,capture_phase="pre_update",
          weapon_schema=1,charge_latch=true,combat_schema=3,
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
    if msg.type=="presentation_status" then
        serverStatus=type(msg.server)=="table" and msg.server or {state="unknown"}
        agentDisplay=type(msg.display)=="table" and msg.display or {agent_state="offline"}
        displayExpires=Isaac.GetTime()+math.min(5000,math.max(500,tonumber(msg.ttl_ms) or 3500));return
    end
    if msg.type=="management_result" then
        notice=msg.message or msg.error or (msg.ok and "Command accepted" or "Command failed")
        noticeExpires=Isaac.GetTime()+7000
        print("[IsaacJev] "..tostring(notice)) -- Replies never contain credentials.
        return
    end
    if msg.type~="command" then return end
    if msg.command=="set_mode" then
        if msg.mode~="manual" and msg.mode~="shadow" and msg.mode~="ai" then acknowledge(msg,false,"invalid mode");return end
        setControlMode(msg.mode,"requested_"..msg.mode);acknowledge(msg,true,"mode changed");return
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
    if msg.execution_timing~=nil and msg.execution_timing~=EXECUTION_TIMING then acknowledge(msg,false,"invalid execution timing");return end
    local hold=msg.hold_frames or (msg.valid_until_frame-msg.observation_frame)
    if type(hold)~="number" or hold~=math.floor(hold) or hold<1 or hold>6 then acknowledge(msg,false,"invalid hold duration");return end
    if msg.charge_hold~=nil and type(msg.charge_hold)~="boolean" then acknowledge(msg,false,"invalid charge hold");return end
    if msg.handoff_room_id~=nil then
        local allowed=false
        for slot=0,7 do
            local door=Game():GetRoom():GetDoor(slot)
            if door and door:IsOpen() and (runId~="") then
                local level=Game():GetLevel();local desc=call(level,"GetCurrentRoomDesc",nil)
                local neighbors=call(desc,"GetNeighboringRooms",{}) or {}
                local index=read(neighbors[slot],"SafeGridIndex",read(neighbors[slot],"GridIndex",read(door,"TargetRoomIndex",nil)))
                local target=tostring(level:GetStage())..":"..tostring(level:GetStageType())..":"..tostring(index)
                if target==msg.handoff_room_id then allowed=true end
            end
        end
        if not allowed then acknowledge(msg,false,"invalid charge handoff destination");return end
    end
    if not msg.charge_hold or (msg.shoot.x==0 and msg.shoot.y==0) then shootingLatch=nil end
    clearCommand("replaced")
    command=msg;command.hold_frames=hold;command.started_frame=nil;command.active_frame=nil;command.stop_frame=nil;lastRequest=msg.request_id
    acknowledge(msg,true,mode=="shadow" and "shadow only" or "accepted")
end

local flushTx, network
local function consoleArgs(text)
    local args, i = {}, 1
    while i<=#text do
        while i<=#text and text:sub(i,i):match("%s") do i=i+1 end
        if i>#text then break end
        local quote=text:sub(i,i)
        if quote~='"' and quote~="'" then quote=nil else i=i+1 end
        local value,closed="",not quote
        while i<=#text do
            local c=text:sub(i,i)
            if quote and c==quote then i=i+1;closed=true;break end
            if not quote and c:match("%s") then break end
            if quote and c=="\\" and (text:sub(i+1,i+1)==quote or text:sub(i+1,i+1)=="\\") then i=i+1;c=text:sub(i,i) end
            value=value..c;i=i+1
        end
        if not closed or #args>=4 then return nil end
        args[#args+1]=value
    end
    return args
end

mod:AddCallback(ModCallbacks.MC_EXECUTE_CMD,function(_,cmd,params)
    cmd=cmd:lower()
    if not ({jev_connect=true,jev_disconnect=true,jev_record=true,jev_status=true,jev_hud=true})[cmd] then return end
    local args=consoleArgs(params or "")
    if not args then print("[IsaacJev] Invalid arguments; quote passwords containing spaces");return end
    if cmd=="jev_hud" then
        if args[1]~="on" and args[1]~="off" then print("[IsaacJev] jev_hud on|off");return end
        hudVisible=args[1]=="on";return
    end
    network()
    if not sock then print("[IsaacJev] Start_Game_Link.cmd must be running on this PC");return end
    local request={type="management_request"}
    if cmd=="jev_connect" then
        local address=args[1] or "10.28.0.22"
        local host,port=address:match("^([^:]+):(%d+)$")
        request.operation="server_connect";request.host=host or address;request.port=tonumber(port) or 31203
        request.password=args[2];request.username=args[3] or "root"
        clearControl("console_connect")
    elseif cmd=="jev_disconnect" then request.operation="server_disconnect";clearControl("console_disconnect")
    elseif cmd=="jev_record" then
        local choice=args[1] or "toggle"
        if choice~="on" and choice~="off" and choice~="toggle" then print("[IsaacJev] jev_record on|off|toggle");return end
        request.operation="recording"
        if choice=="toggle" then request.enabled="toggle" else request.enabled=choice=="on" end
    else request.operation="server_status" end
    if emit(request) then flushTx();print("[IsaacJev] Request sent") else print("[IsaacJev] Bridge busy; retry") end
end)

if Console and Console.RegisterCommand then
    for name,help in pairs({jev_connect="jev_connect [IP[:SSH_PORT]] [PASSWORD] [USERNAME]; omit password for local password dialog",
        jev_disconnect="Close managed SSH tunnel; does not stop remote model",jev_record="jev_record on|off|toggle",
        jev_status="Show bridge/server state",jev_hud="jev_hud on|off"}) do
        Console.RegisterCommand(name,"Isaac Jev game link",help,false,AutocompleteType and AutocompleteType.NONE or 0)
    end
end
flushTx=function()
    if not sock or #tx==0 then return end
    local sent,err,partial=sock:send(tx)
    local count=sent or partial or 0
    if count>0 then tx=tx:sub(count+1) end
    if err=="closed" then disconnect("Python disconnected") end
end
network=function()
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
    flushTx()
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
    local fingerprint=tostring(p:GetActiveItem(0))..":"..tostring(p:GetTrinket(0))..":"..tostring(p:GetTrinket(1))..":"..tostring(p:GetCard(0))..":"..tostring(p:GetPill(0))
    if count~=inventoryCount or fingerprint~=inventoryFingerprint or frameNo()-inventoryCheckedAt>=30 then
        inventoryFingerprint=fingerprint
        inventoryCheckedAt=frameNo()
        inventoryCount=count;itemCache=arr()
        local configs=Isaac.GetItemConfig():GetCollectibles()
        for id=1,configs.Size-1 do
            local n=p:GetCollectibleNum(id,true)
            if n>0 then
                local item=Isaac.GetItemConfig():GetCollectible(id)
                itemCache[#itemCache+1]={id=id,count=n,name=itemText(read(item,"Name","")),description=itemText(read(item,"Description","")),
                                       quality=read(item,"Quality",nil),item_type=read(item,"Type",nil),
                                       name_key=read(item,"Name",""),description_key=read(item,"Description","")}
            end
        end
    end
    return {coins=p:GetNumCoins(),keys=p:GetNumKeys(),bombs=p:GetNumBombs(),
            trinkets=arr({p:GetTrinket(0),p:GetTrinket(1)}),collectibles=itemCache,
            card=p:GetCard(0),pill_color=p:GetPill(0)}
end
local function weaponData(w,slot)
    if not w then return nil end
    local charge,maxCharge=call(w,"GetCharge",nil),call(w,"GetMaxCharge",nil)
    return {slot=slot,type=call(w,"GetWeaponType",nil),charge=charge,max_charge=maxCharge,
        charge_ratio=type(charge)=="number" and type(maxCharge)=="number" and maxCharge>0 and math.max(0,math.min(1,charge/maxCharge)) or nil,
        fire_delay=call(w,"GetFireDelay",nil),max_fire_delay=call(w,"GetMaxFireDelay",nil),
        num_fired=call(w,"GetNumFired",nil),modifiers=call(w,"GetModifiers",nil),owner_type=read(call(w,"GetOwner",nil),"Type",nil)}
end
local function playerWeapons(p,held)
    local result=arr()
    for slot=1,2 do
        local state=weaponData(call(p,"GetWeapon",nil,slot),slot)
        if state then result[#result+1]=state end
    end
    lastWeapons=result
    local shoot=held.shoot
    if lastShoot and lastShoot.x==shoot.x and lastShoot.y==shoot.y and (shoot.x~=0 or shoot.y~=0) then shootHeldFrames=shootHeldFrames+1 else shootHeldFrames=0 end
    lastShoot={x=shoot.x,y=shoot.y}
    return result
end
local function gridKind(t)
    local enums=GridEntityType or {}
    for _,entry in ipairs({{"GRID_STAIRS","stairs"},{"GRID_TRAPDOOR","trapdoor"},{"GRID_TELEPORTER","teleporter"},
        {"GRID_PRESSURE_PLATE","button"},{"GRID_SPIKES","spikes"},{"GRID_SPIKES_ONOFF","spikes"},
        {"GRID_FIREPLACE","fire"},{"GRID_TNT","tnt"},{"GRID_PIT","pit"},{"GRID_POOP","poop"},
        {"GRID_WALL","wall"},{"GRID_DOOR","door"}}) do
        if enums[entry[1]]~=nil and enums[entry[1]]==t then return entry[2] end
    end
    return "grid"
end
local function floorData()
    local level=Game():GetLevel()
    local dimension=call(level,"GetDimension",0)
    local key=tostring(level:GetStage())..":"..tostring(level:GetStageType())..":"..tostring(dimension)
    if floorCache and floorCacheKey==key and frameNo()-floorCacheAt<30 then return floorCache end
    floorCacheKey=key;floorCacheAt=frameNo()
    local rooms=arr();local list=call(level,"GetRooms",nil)
    if list then
        for i=0,(read(list,"Size",0) or 0)-1 do
            local d=call(list,"Get",nil,i)
            if d then
                local idx=read(d,"SafeGridIndex",read(d,"GridIndex",nil))
                local dim=call(d,"GetDimension",dimension)
                local data=read(d,"Data",nil);local links=arr()
                local allowed=read(d,"AllowedDoors",0) or 0
                for slot,neighbor in pairs(call(d,"GetNeighboringRooms",{}) or {}) do
                    if type(slot)=="number" and (allowed & (1<<slot))~=0 then
                        links[#links+1]={slot=slot,target_index=read(neighbor,"SafeGridIndex",read(neighbor,"GridIndex",nil)),
                            dimension=call(neighbor,"GetDimension",dim)}
                    end
                end
                rooms[#rooms+1]={index=idx,dimension=dim,type=read(data,"Type",nil),shape=read(data,"Shape",nil),
                    display_flags=read(d,"DisplayFlags",0),visited=read(d,"VisitedCount",0),clear=read(d,"Clear",false),links=links}
            end
        end
    end
    floorCache={id=key,stage=level:GetStage(),stage_type=level:GetStageType(),dimension=dimension,
        curses=call(level,"GetCurses",0),rooms=rooms,refreshed_frame=frameNo()}
    return floorCache
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
    local familiar=call(e,"ToFamiliar",nil)
    local kind=(e.Type==(EntityType.ENTITY_SLOT or 6)) and "slot" or n and "enemy" or projectile and "projectile" or tear and "tear"
               or laser and "laser" or bomb and "bomb" or pickup and "pickup" or effect and "effect" or familiar and "familiar" or "other"
    local flags=call(e,"GetEntityFlags",0)
    local friendly=e:HasEntityFlags(EntityFlag.FLAG_FRIENDLY) or e:HasEntityFlags(EntityFlag.FLAG_CHARM)
    local config=pickup and e.Variant==100 and Isaac.GetItemConfig():GetCollectible(e.SubType) or nil
    local blind=call(pickup,"IsBlind",false)
    local curse=call(Game():GetLevel(),"GetCurses",0)
    if pickup and e.Variant==100 and LevelCurse and LevelCurse.CURSE_OF_BLIND then blind=blind or (curse & LevelCurse.CURSE_OF_BLIND)~=0 end
    local spawner=read(e,"SpawnerEntity",nil)
    local target=n and call(n,"GetPlayerTarget",nil) or nil
    return {uid=tostring(e.Index)..":"..tostring(e.InitSeed),kind=kind,type=e.Type,variant=e.Variant,subtype=e.SubType,
            position=vec(e.Position),velocity=vec(e.Velocity),size=e.Size,size_multi=vec(e.SizeMulti),
            hp=e.HitPoints,max_hp=e.MaxHitPoints,frame_count=e.FrameCount,
            collision_class=e.EntityCollisionClass,grid_collision_class=e.GridCollisionClass,
            flags=flags,friendly=friendly,visible=e.Visible,dead=e:IsDead(),
            vulnerable=call(e,"IsVulnerableEnemy",false),active_enemy=call(e,"IsActiveEnemy",false,false),
            animation=call(sprite,"GetAnimation",""),animation_frame=call(sprite,"GetFrame",0),
            overlay_animation=call(sprite,"GetOverlayAnimation",nil),overlay_frame=call(sprite,"GetOverlayFrame",nil),
            sprite_offset=optionalVec(read(e,"SpriteOffset",nil)),position_offset=optionalVec(read(e,"PositionOffset",nil)),
            npc_target_position=optionalVec(read(n,"TargetPosition",nil)),
            observed_target_position=optionalVec(read(target,"Position",nil)),observed_target_type=read(target,"Type",nil),
            state=read(n,"State",nil),state_frame=read(n,"StateFrame",nil),
            projectile_flags=flagData(read(projectile,"ProjectileFlags",nil)),tear_flags=flagData(read(tear,"TearFlags",nil)),height=read(projectile or tear,"Height",nil),
            falling_speed=read(projectile or tear,"FallingSpeed",nil),falling_accel=read(projectile or tear,"FallingAccel",nil),
            laser_angle=read(laser,"AngleDegrees",nil),laser_endpoint=laser and vec(read(laser,"EndPoint",nil)) or nil,
            laser_radius=read(laser,"Radius",nil),laser_timeout=read(laser,"Timeout",nil),
            bomb_countdown=call(bomb,"GetExplosionCountdown",read(bomb,"ExplosionCountdown",nil)),
            bomb_radius_multiplier=read(bomb,"RadiusMultiplier",nil),
            pickup_price=read(pickup,"Price",nil),pickup_shop=call(pickup,"IsShopItem",false),
            pickup_options=read(pickup,"OptionsPickupIndex",0),pickup_wait=read(pickup,"Wait",0),
            pickup_blind=blind,pickup_name=not blind and config and itemText(read(config,"Name","")) or nil,
            pickup_description=not blind and config and itemText(read(config,"Description","")) or nil,
            pickup_quality=not blind and read(config,"Quality",nil) or nil,pickup_item_type=not blind and read(config,"Type",nil) or nil,
            owner_type=read(spawner,"Type",read(read(familiar,"Player",nil),"Type",nil)),effect_timeout=read(effect,"Timeout",nil),
            effect_radius=read(effect,"Radius",nil),effect_scale=read(effect,"Scale",nil),
            familiar_owner_index=read(read(familiar,"Player",nil),"Index",nil),
            familiar_weapon=familiar and weaponData(call(familiar,"GetWeapon",nil),0) or nil,
            spawn_threat=n and e.FrameCount<3 and call(n,"IsActiveEnemy",false,false) or false}
end
local function roomData(p)
    local r=Game():GetRoom()
    local grid=arr()
    local cells=arr()
    for i=0,r:GetGridSize()-1 do
        local pos=r:GetGridPosition(i);cells[#cells+1]=arr({i,pos.X,pos.Y,r:GetGridCollision(i),call(r,"IsPositionInRoom",true,pos,0)})
        local g=r:GetGridEntity(i)
        if g then
            local desc=read(g,"Desc",nil)
            grid[#grid+1]={index=i,position=vec(r:GetGridPosition(i)),type=call(g,"GetType",nil),
                           collision=r:GetGridCollision(i),state=read(g,"State",nil),
                           variant=read(desc,"Variant",nil),kind=gridKind(call(g,"GetType",nil))}
        end
    end
    local doors=arr()
    local level=Game():GetLevel()
    local roomDesc=call(level,"GetCurrentRoomDesc",nil)
    local neighbors=call(roomDesc,"GetNeighboringRooms",{}) or {}
    for slot=0,7 do
        local d=r:GetDoor(slot)
        if d then doors[#doors+1]={slot=slot,position=vec(d.Position),open=d:IsOpen(),locked=d:IsLocked(),
                                  target_room_type=d.TargetRoomType,
                                  target_room_index=read(neighbors[slot],"SafeGridIndex",read(neighbors[slot],"GridIndex",read(d,"TargetRoomIndex",nil))),
                                  target_dimension=call(neighbors[slot],"GetDimension",call(level,"GetDimension",0))} end
    end
    return {id=roomId(),type=r:GetType(),shape=r:GetRoomShape(),clear=r:IsClear(),
            grid_width=r:GetGridWidth(),grid_size=r:GetGridSize(),navigation_cells=cells,
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
    if currentRoom~=lastRoom then
        clearCommand("room_changed")
        if shootingLatch and shootingLatch.handoff_room_id==currentRoom and shootingLatch.run_id==runId then
            shootingLatch.room_id=currentRoom;shootingLatch.handoff_room_id=nil
        elseif shootingLatch and shootingLatch.room_id~=currentRoom then shootingLatch=nil end
        lastRoom=currentRoom;floorCache=nil;inventoryCount=-1;appliedControls=nil
    end
    local weapons=playerWeapons(p,held)
    sequence=sequence+1
    return {type="frame",protocol_version=VERSION,run_id=runId,seq=sequence,game_frame=frameNo(),
            capture_phase="pre_update",game_time_ms=Isaac.GetTime(),mode=mode,
            mode_reason=controlReason,control_revision=controlRevision,room=roomData(p),floor=floorData(),planner_schema=1,
            player={position=vec(p.Position),velocity=vec(p.Velocity),size=p.Size,size_multi=vec(p.SizeMulti),
                    type=p:GetPlayerType(),dead=p:IsDead(),can_fly=p.CanFly,controls_enabled=p:AreControlsEnabled(),
                    health={red=p:GetHearts(),max_red=p:GetMaxHearts(),soul=p:GetSoulHearts(),
                            black_mask=p:GetBlackHearts(),bone=call(p,"GetBoneHearts",0),rotten=call(p,"GetRottenHearts",0)},
                    stats={damage=p.Damage,move_speed=p.MoveSpeed,shot_speed=p.ShotSpeed,
                           max_fire_delay=p.MaxFireDelay,tear_height=p.TearHeight,tear_range=read(p,"TearRange",nil),luck=read(p,"Luck",nil),tear_flags=flagData(p.TearFlags)},
                    weapons=weapons,weapon_type=call(p,"GetWeaponType",nil),shoot_direction=held.shoot,
                    fire_direction=call(p,"GetFireDirection",nil),
                    shoot_holding=held.shoot.x~=0 or held.shoot.y~=0,shoot_held_frames=shootHeldFrames,
                    active=activeItem(p),inventory=inventory(p)},
            planner_stats={luck=read(p,"Luck",nil),tear_range=read(p,"TearRange",nil)},
            human_input=held,entities=entities,execution=command or {},applied_controls=appliedControls or {},
            shooting_latch=shootingLatch or {},weapon_schema=1,combat_schema=3,
            bridge={dropped_frames=drops},supported_player_count=Game():GetNumPlayers()}
end

mod:AddCallback(ModCallbacks.MC_POST_GAME_STARTED,function(_,continued)
    runId=runKey()
    sequence=0;lastRoom=nil;inventoryCount=-1;inventoryCheckedAt=-1000;floorCache=nil;clearCommand("run_started");previousHeld={};appliedControls=nil
    shootingLatch=nil;lastShoot=nil;shootHeldFrames=0
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
        -- Send this observation now, rather than adding one full logic frame of age.
        flushTx()
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
    end
    if Game():IsPaused() then
        network();clearCommand("paused_transition") -- No action may survive a paused transition.
        if humanPauseOpen() then clearControl("pause_menu") end
    end
    if p and Input.IsButtonPressed(Keyboard.KEY_F8,p.ControllerIndex) then clearControl("f8") end
    local rec=recordingError and "REC ERROR" or recording and "REC" or "NOT REC"
    if not hudVisible then return end
    local function hudText(value) return tostring(value or ""):gsub("[%c]"," "):sub(1,90) end
    local display=Isaac.GetTime()<displayExpires and agentDisplay or {agent_state="offline"}
    local state=display.agent_state or "idle"
    local toolLine=state=="tool" and ("Tool: "..hudText(display.tool)) or state=="recent_tool" and ("Last tool: "..hudText(display.tool))
        or state=="thinking" and "Thinking..." or state=="offline" and "dsh service: offline" or "dsh: waiting"
    local phase=display.controller_phase=="combat" and "Jev combat" or display.controller_phase=="navigation" and "Navigation" or hudText(display.controller_phase)
    local serverLabel=sock and Isaac.GetTime()<displayExpires and serverStatus.state or "unknown"
    Isaac.RenderText("SERVER: "..hudText(serverLabel).." | "..phase,HUD_X,HUD_Y+14,0.6,0.85,1,1)
    Isaac.RenderText(toolLine,HUD_X,HUD_Y+26,0.95,0.8,1,1)
    local detail=display.goal and display.goal~="" and (hudText(display.goal).." | "..hudText(display.navigation_stage)) or hudText(display.summary)
    if detail~="" then Isaac.RenderText(detail,HUD_X,HUD_Y+38,0.9,0.9,0.9,1) end
    local message=Isaac.GetTime()<noticeExpires and notice or serverStatus.error
    if message then Isaac.RenderText(hudText(message),HUD_X,HUD_Y+50,1,0.7,0.4,1) end
    Isaac.RenderText("Isaac Jev: "..mode.." | "..(sock and "CONNECTED" or lastError).." | "..rec.." | F8: HUMAN",HUD_X,HUD_Y,1,1,1,1)
end)
local function latchInput(action,hook)
    local latch=shootingLatch
    if not latch or mode~="ai" or not sock or Game():IsPaused() then return nil end
    if frameNo()>=latch.expires_frame or Isaac.GetTime()>=latch.expires_time or latch.run_id~=runId or (latch.room_id~=roomId() and latch.handoff_room_id~=roomId()) then shootingLatch=nil;return nil end
    local active
    if action==ButtonAction.ACTION_SHOOTLEFT then active=latch.shoot.x<0
    elseif action==ButtonAction.ACTION_SHOOTRIGHT then active=latch.shoot.x>0
    elseif action==ButtonAction.ACTION_SHOOTUP then active=latch.shoot.y<0
    elseif action==ButtonAction.ACTION_SHOOTDOWN then active=latch.shoot.y>0
    else return nil end
    if hook~=InputHook.GET_ACTION_VALUE and hook~=InputHook.IS_ACTION_PRESSED and hook~=InputHook.IS_ACTION_TRIGGERED then return nil end
    local now=frameNo()
    if not appliedControls or appliedControls.game_frame~=now then appliedControls={game_frame=now,run_id=runId,room_id=roomId(),requests=arr()} end
    local record=appliedControls.requests[#appliedControls.requests]
    if not record or record.request_id~=latch.request_id then
        record={request_id=latch.request_id,action_id=latch.action_id,source="charge_latch",queried={},pressed={}}
        appliedControls.requests[#appliedControls.requests+1]=record
    end
    record.queried[tostring(action)]=true;record.pressed[tostring(action)]=active
    if hook==InputHook.GET_ACTION_VALUE then return active and 1.0 or 0.0 end
    return active
end
mod:AddCallback(ModCallbacks.MC_INPUT_ACTION,function(_,entity,hook,action)
    if mode~="ai" or not sock or Game():IsPaused() then return nil end
    local p=player0()
    if not p or not entity or entity.Type~=EntityType.ENTITY_PLAYER or entity.Index~=p.Index then return nil end
    if p:IsDead() then clearControl("player_dead");return nil end
    if not command then return latchInput(action,hook) end
    if command.run_id~=runId or command.room_id~=roomId() then clearCommand("run_room_changed");return latchInput(action,hook) end
    -- valid_until is the deadline to START a fresh action. A started v1 action
    -- has its own bounded stop frame. Legacy actions retain the original cutoff.
    if (not command.started_frame or command.execution_timing~=EXECUTION_TIMING) and frameNo()>=command.valid_until_frame then
        clearCommand("expired_before_start");return latchInput(action,hook)
    end
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
    if hook~=InputHook.GET_ACTION_VALUE and hook~=InputHook.IS_ACTION_PRESSED and hook~=InputHook.IS_ACTION_TRIGGERED then return nil end
    local now=frameNo()
    if not command.started_frame then
        command.started_frame=now;command.active_frame=now
        command.stop_frame=now+command.hold_frames
        if command.execution_timing~=EXECUTION_TIMING then command.stop_frame=math.min(command.stop_frame,command.valid_until_frame) end
        if command.charge_hold and (command.shoot.x~=0 or command.shoot.y~=0) then
            local primary=lastWeapons[1]
            if primary and type(primary.max_charge)=="number" and primary.max_charge>0 then
                shootingLatch={shoot=command.shoot,expires_frame=now+8,expires_time=Isaac.GetTime()+300,run_id=runId,room_id=roomId(),
                    handoff_room_id=command.handoff_room_id,request_id=command.request_id,action_id=command.action_id%5}
            end
        end
        emit({type="event",event="action_started",request_id=command.request_id,run_id=runId,
              room_id=command.room_id,game_frame=now,observation_frame=command.observation_frame,action_id=command.action_id,
              use_active=command.use_active,stop_frame=command.stop_frame})
    end
    if now>=command.stop_frame then clearCommand("hold_finished");return latchInput(action,hook) end
    if action==ButtonAction.ACTION_ITEM then active=active and now==command.active_frame end
    -- Record actual callback queries, including all-zero model actions. The frame
    -- key is explicit because the pre-update snapshot describes the preceding input.
    if not appliedControls or appliedControls.game_frame~=now then
        appliedControls={game_frame=now,run_id=runId,room_id=roomId(),requests=arr()}
    end
    local record=appliedControls.requests[#appliedControls.requests]
    if not record or record.request_id~=command.request_id then
        record={request_id=command.request_id,action_id=command.action_id,queried={},pressed={}}
        appliedControls.requests[#appliedControls.requests+1]=record
    end
    record.queried[tostring(action)]=true;record.pressed[tostring(action)]=active
    if hook==InputHook.GET_ACTION_VALUE then return active and 1.0 or 0.0 end
    if hook==InputHook.IS_ACTION_PRESSED or hook==InputHook.IS_ACTION_TRIGGERED then return active end
    return nil
end)
if ModCallbacks.MC_POST_NPC_INIT then
    mod:AddCallback(ModCallbacks.MC_POST_NPC_INIT,function(_,npc)
        if call(npc,"IsActiveEnemy",false,false) and not npc:HasEntityFlags(EntityFlag.FLAG_FRIENDLY) then
            emit({type="event",event="hostile_spawned",run_id=runId,room_id=roomId(),game_frame=frameNo(),entity_type=npc.Type})
            if mode=="ai" and command and command.controller_phase=="navigation" then clearCommand("hostile_spawned") end
            flushTx()
        end
    end)
end
mod:AddCallback(ModCallbacks.MC_ENTITY_TAKE_DMG,function(_,entity,amount,flags,source,countdown)
    emit({type="event",event="damage",run_id=runId,game_frame=frameNo(),entity_type=entity.Type,
          entity_uid=tostring(entity.Index)..":"..tostring(entity.InitSeed),amount=amount,flags=flags})
end)
mod:AddCallback(ModCallbacks.MC_USE_ITEM,function(_,id,rng,p,flags,slot,variableData)
    inventoryCount=-1
    emit({type="event",event="item_used",run_id=runId,game_frame=frameNo(),item_id=id,
          player_uid=p and (tostring(p.Index)..":"..tostring(p.InitSeed)) or nil,
          use_flags=flags,active_slot=slot,variable_data=variableData})
    -- nil return: observe use without cancelling or changing charge consumption.
end)
mod:AddCallback(ModCallbacks.MC_PRE_GAME_EXIT,function() clearControl("game_exit");rawFrame=nil end)
