---
name: isaac-play
description: 使用非视觉游戏状态自主游玩本机《以撒的结合》，利用游戏资源运营并向通关推进。调用随技能提供的游戏工具探索、拾取及选择装备，战斗交给已部署的 L40S Qwen-jev；缺失道具知识查以撒灰机 Wiki 并保存到项目字典。
---

# 以撒自动游玩

使用本技能的工具脚本自主游玩。默认任务目标是：**尽可能利用游戏资源进行运营，尽可能向着通关的目标前进。** 在当前工具支持的范围内选择目标、执行并持续推进，直到达成用户目标、死亡、人工接管、断线或确实没有可执行的推进方式。当前工具支持本层探索、普通掉落、宝箱房道具和商店查看；能力缺口需如实报告，本层演示结束不等于通关。

运营时结合当前血量、装备效果、资源成本、地图和战斗结果权衡，而不是固定跑完几个演示项目：

- 缺血时优先考虑可达补给；满血时可保留红心供之后回访，不浪费有互斥效果的资源。
- 收集有用掉落，比较道具、饰品与现有装备的作用及副作用；消耗钥匙开门时考虑当前收益、未探索机会和剩余钥匙。
- 在补给、获取装备、探索与战斗推进之间选择最有价值的下一步；高风险房间需比较预期收益、剩余生命及替代路线。
- 低血量时先找补给或安全回访路线，不用固定血量阈值直接结束；拿到宝箱道具、查看商店或完成几次过门也不是默认收尾条件。路径明确不可达时记录并跳过，状态变化后再评估。

用户要求重点测试高层决策/导航并仍将战斗交给 Jev 时，读 [planning-test.md](references/planning-test.md)。沿用正常战斗交接，分别评价走门、绕障、拾取、宝箱房和商店查看，排除战斗耗时及损伤对这些指标的混淆。

## 工具入口

本技能的 `scripts/isaac_tools.py` 是可执行工具入口。使用 dsh 提供的终端/Python 工具运行：

```text
python <本技能目录>/scripts/isaac_tools.py setup
python <本技能目录>/scripts/isaac_tools.py list
python <本技能目录>/scripts/isaac_tools.py call doctor
python <本技能目录>/scripts/isaac_tools.py check
python <本技能目录>/scripts/isaac_tools.py call observe_game
```

简单参数使用重复的 `--arg <字段名> <值>`，CLI 按工具 Schema 转成正确类型，不需要 PowerShell 内联 JSON 或 .NET HTTP/编码调用。例如：

```text
python <本技能目录>/scripts/isaac_tools.py call start_autoplay --arg wait_seconds 20
python <本技能目录>/scripts/isaac_tools.py call execute_plan --arg decision_id decision-... --arg action_id door-2 --arg reason "导航测试"
python <本技能目录>/scripts/isaac_tools.py call wait_for_decision --arg event_cursor 3 --arg timeout_s 25
```

对象/对象数组等复杂参数写成 UTF-8 JSON 文件，再执行 `call <工具名> --args-file <文件>`。工具目录及 JSON Schema 由 `list` 返回；以实际目录为准。已注册 MCP 时，同名工具可直接调用。MCP 入口为 `python .../isaac_tools.py mcp`。

终端受限时继续使用这个 Python CLI；无需另写调用壳。`check` 只读两次状态检查帧推进、manual 和存活情况，不发送键盘或控制指令。其 `ready_for_start=false` 时，根据状态处理：游戏不推进先让用户检查暂停并保持游戏前台，避免盲目注入 Esc。终端临时目录缺失属于 dsh 宿主问题，见 [connection.md](references/connection.md)。

## 运行

1. `setup` 启动本地工具服务。`doctor` 核对游戏新状态、模组 0.6.0 和 L40S，确认 `battle_ai.ready=true` 且 checkpoint 与用户本次指定的模型一致。当前挂载 V009 反馈版 S01 第 1 轮：`combat-v009-feedback-s01-epoch001`；V007 格式为 `combat-v007-sXX-epochNNN`，V006 为 `combat-v006-eXX-epochNNN`，记录方案和轮次，不因名称不同而切回旧模型。缺少通路时读 [connection.md](references/connection.md)。
2. 读取 `observe_game`、`get_floor_map`，按需读 `get_rules`。游戏事实和合法候选以最新工具返回为准。
3. 用户要求实际游玩时调用 `start_autoplay`，再 `wait_for_decision` 等待 `controller_phase=planning`。初始游戏需要未暂停、manual 模式；旧独立 AI/shadow 客户端先结束。
4. 从 `get_available_actions` 的 action_id 选择一个目标，调用 `execute_plan`，传最新 decision_id 和简短理由。按资源运营及通关推进的价值权衡补给、掉落、装备、商店信息和探索机会，理由说明该目标对当前局面的作用。
5. 调用 `wait_for_decision`，使用最新 event_cursor，单次等待最多 25 秒。导航与 Jev 战斗在本地服务中持续执行；combat/navigation 时继续等待，planning 时选下一目标。
6. 读取任务完成/失败的实际前后状态。blocked 时换可达目标；决策过期时重新读取候选。不要用命令被接受来声称已经拾取或进入房间。
7. 结束时 `stop_autoplay`，报告推进到哪里、资源与装备变化、停止原因及下一步机会。`finish_floor_demo` 会报告剩余房间，但只表示本层演示收尾；不能把它作为通关证明，也不因存在这个候选就提前结束自主游玩。

F8、暂停、断线和死亡会撤销自动会话。收到 autoplay=false / human_takeover / cancelled 后停止自动操作，等待用户重新要求游玩；旧请求不得重新接管。

新敌人出现时，本地执行器逐帧中断导航并切战斗，无需高层再次决定。V005 还处理迫近的地面危险、敌方激光和炸弹；静态危险由导航避让。人工录制模式保持人工控制。

## 战斗输入与蓄力过门

当前服务器挂载 V009 反馈版 S01 第 1 轮权重。输入使用统一 `safe_progress` 目标、武器/道具机制说明、相对运动及危险余量、移动后的攻击关系和已观察战斗进展；采用独立移动/射击双塔及高阶反馈对齐，主动使用未启用。验证结果以录像和执行事件为准，离线一致率不代表清房或规避表现。

V009 反馈版使用 `combat-feedback-v9-s01` 输入契约。它提供已观察执行/HP 进展、可达攻击位置、第一段路标和各移动后的路径代价，目标是主动建立攻击机会并尽快清敌。脚本仍只提供事实，模型选择移动与射击。只有挂载配套反馈版权重才注入这些字段；`observe_game.combat_feedback` 和日志 `combat_feedback` 可核对模型当时看到的反馈及观测帧。静止、充能和等待需结合有效攻击或机制依据，不能仅以存活作为战斗目标。

`observe_game.weapon_profile` 使用实际武器读数，未知 `charge_ratio` 保持未知。V005 的 `execute_plan` 支持布尔参数 `prepare_for_combat`：选择开放的普通战斗房门且 `weapon_profile.precharge_allowed=true` 时，可加 `--arg prepare_for_combat true`。工具门前站定蓄至 90%（最多等待 3 秒），保持射击过门，再交接 Jev。执行阶段为 `precharging` 或 `charge_handoff` 时继续等当前任务，勿重复提交。

首版预蓄力支持已观测的硫磺火/科技 X；优先选择 `enter_door` 目标，多房间 `visit_room` 当前不执行逐门预蓄力。跨房保持、复杂道具组合及蓄满自动发射武器需实机验证。本轮旧录像没有精确蓄力值，硫磺火只出现在训练集，尚无独立蓄力泛化结果。停射明确释放；F8、暂停和断线清除保持状态。

战斗状态的 `combat_timing` 提供预测、发送、受理、拒绝和丢弃计数。受理不能证明角色执行；实际输入以录像 `applied_controls` 和 `action_started/action_ended` 为准。角色持续静止、快速掉血且拒绝/过期计数增长时，停止会话并报告通路问题；不要反复重开消耗生命。模型仍可能选择停留或因墙体无法移动，应结合动作与实际输入区分。诊断工具可后台读取，无需切换游戏焦点。

2026-11-25 已优化大/L 房间地形计算，保持模型输入和权重一致。`last_feature_ms`、`last_rpc_ms`、`last_model_ms` 分别用于区分本地构建、模型请求往返及服务器计算；原始推理日志也记录 `feature_ms/rpc_ms`。下一轮优先核对实际开始执行比例与查询覆盖，再评价模型动作。观测期限和保持时间是不同概念：30 Hz 下 6 帧保持为 200ms；10 Hz 发送可以重叠覆盖，不能直接声称覆盖率仅 60%。旧预测保留原观察身份，不刷新帧号续期。

2026-11-25 E08 第 13 轮复盘确认：短动作 ACK 不能排除推理 RPC 长尾；录像帧约每 33ms 连续接收，拒绝请求的 RPC 却约 134–181ms、模型仍约 52ms。通用 SSH 连接已补齐两层 TCP_NODELAY。推理日志新增 `server_ms/worker_ms/pre_model_ms/post_model_ms`，发送日志新增 `observation_age_frames_at_send/start_deadline_frames_remaining`，用它们定位等待发生在哪一段。不要把 ACK 时间当成整次预测耗时，也不要把旧动作换成新观测帧重新发出。

导航路径和门前对齐阶段可从 `execution.navigation` 查看。空地应直达，障碍由本地导航绕过；combat 时的顶墙问题仍由战斗策略处理。复盘频率与覆盖率时，分别统计 combat/navigation/planning，清房后的等待不能算作战斗推理停顿。房间是否曾有怪、是否由 AI 清完，查战斗事件和前后状态，避免几秒后首次观察时误判为空房。

2026-11-25 已修复实体火焰遗漏：`type=33` 的活火即使 `room.clear=true`、`active_enemy=false` 也由本地导航绕开。火焰出现/消失会重新规划；已贴近火时，导航或 planning 等待期间优先撤离，`execution.navigation.stage=escape_fire`，离开后继续原任务。检测/撤离事件为 `navigation_hazard_detected` / `navigation_hazard_escaped`；`navigation_hazard_blocked` 表示无法找到安全撤离动作，自动会话已停止。不要因为有普通火焰就要求切 Jev 战斗；火焰射出的敌方弹幕仍正常触发战斗。此修复需实机复验安全绕火拾取和过门。

## 游戏内演示状态

工具调用会自动显示在游戏 HUD（短调用显示为 Last tool），导航与 Jev 战斗阶段另列。进行目标选择、百科查询或较长判断前，调用 `report_agent_status`，state=`thinking`，summary 使用简短的英文任务摘要，例如 `Choosing next room` 或 `Checking Leech`；游戏当前字体使用英文显示。无需提供内部推理过程。完成本轮判断后可报告 state=`idle`。该状态约 60 秒过期；后台断联也会显示 offline，不从“没有工具调用”猜测模型正在思考。

`report_agent_status` 仅显示信息；不能启动、恢复或改变自动控制。F8/暂停后的会话仍按已有规则停止。详细游戏内连接与录制指令见 [connection.md](references/connection.md)。

## 道具知识

从工具提供的物品类型、可见 ID/名称查 `get_item_details`。优先批量查当前装备及新宝箱房/商店候选。未知道具不通过内部 ID 猜测其身份。

游戏字段 `item_category` / `is_active` 区分道具类型：`item_type=3` 是主动，`4` 是跟班（例如 Leech），不要把跟班误判成主动。拾取内容消失后，`execution.navigation.stage=awaiting_pickup_confirmation` 表示已停止靠近、正在等库存确认，最多 90 个推进帧且受任务总超时约束；继续等待当前任务，勿重复提交。晚确认仍是 completed，附 `confirmation.delay_frames`。

未命中时：`wiki_search` 返回灰机 Wiki 搜索入口；用 dsh 的网页搜索/浏览器工具读 https://isaac.huijiwiki.com/ 对应正文。`wiki_read` 给出读取要求。核对类别、ID、适用版本，提取关键效果、条件、副作用、重要组合，用 `knowledge_upsert` 保存带来源和短正文依据的摘要。格式见 [knowledge.md](references/knowledge.md)。

网站可能要求 JavaScript/Cookie。验证页、未读正文、搜索摘要分别处理；读不到的事实保持 unknown/partial，并向用户说明。基础走门/普通拾取可以继续，未知道具可暂存或在说明未知后做选择。游戏内实际价格/属性优先于百科默认值。

## 当前执行范围

- 通过正常输入走门，必要时正常消耗钥匙打开宝箱房/商店；由工具候选检查资源。
- 拿宝箱房道具，互斥选择后刷新候选；主动道具可以持有，本期不使用。
- 商店只查看：商品包括免费商品都要避让。购买、箱子/机器/按钮、炸弹、主动、卡牌/药丸使用和楼梯/传送不属于当前执行范围。
- 战斗由 doctor 确认的 L40S Qwen-jev 模型完成，当前为 `combat-v009-feedback-s01-epoch001`；残留敌方弹幕继续战斗。刀/蓄力/其他控制转换道具参考实际 `weapon_profile` 和知识兼容性说明，分别验证动作效果。

## 扩展

工具和目标可通过项目 `skill_extensions/*.py` 注册，服务重启后 `list` 自动显示新接口。扩展方法、服务配置和目标执行接口见 [extensions.md](references/extensions.md)。扩展代码来自用户的项目，Wiki 内容作为资料保存。
