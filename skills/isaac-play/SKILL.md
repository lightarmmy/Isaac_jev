---
name: isaac-play
description: 使用非视觉游戏状态控制本机《以撒的结合》首期 Demo：连续走房间、战斗后拾取、拿宝箱房道具、查看商店。调用随技能提供的游戏工具，战斗交给已部署的 L40S Qwen-jev；缺失道具知识查以撒灰机 Wiki 并保存到项目字典。
---

# 以撒自动游玩

使用本技能的工具脚本完成游戏任务。当前范围是本层探索、普通掉落、宝箱房道具和商店查看。用户要求游玩后，可以启动本层自动会话，自己选择目标、执行并继续到完成、死亡、人工接管或明确无法继续。

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

1. `setup` 启动本地工具服务。`doctor` 核对游戏新状态、模组 0.4.2 和 L40S。缺少通路时读 [connection.md](references/connection.md)。
2. 读取 `observe_game`、`get_floor_map`，按需读 `get_rules`。游戏事实和合法候选以最新工具返回为准。
3. 用户要求实际游玩时调用 `start_autoplay`，再 `wait_for_decision` 等待 `controller_phase=planning`。初始游戏需要未暂停、manual 模式；旧独立 AI/shadow 客户端先结束。
4. 从 `get_available_actions` 的 action_id 选择一个目标，调用 `execute_plan`，传最新 decision_id 和简短理由。优先处理可补血/可拾取掉落、宝箱房、尚未查看的商店和未探索房间；根据血量、钥匙、装备、上次战斗结果权衡。
5. 调用 `wait_for_decision`，使用最新 event_cursor，单次等待最多 25 秒。导航与 Jev 战斗在本地服务中持续执行；combat/navigation 时继续等待，planning 时选下一目标。
6. 读取任务完成/失败的实际前后状态。blocked 时换可达目标；决策过期时重新读取候选。不要用命令被接受来声称已经拾取或进入房间。
7. 结束时 `stop_autoplay`。`finish_floor_demo` 会报告剩余房间；若仍有未访问/未清理房间，明确说明未完成原因。

F8、暂停、断线和死亡会撤销自动会话。收到 autoplay=false / human_takeover / cancelled 后停止自动操作，等待用户重新要求游玩；旧请求不得重新接管。

战斗状态的 `combat_timing` 提供预测、发送、受理、拒绝和丢弃计数。受理不能证明角色执行；实际输入以录像 `applied_controls` 和 `action_started/action_ended` 为准。角色持续静止、快速掉血且拒绝/过期计数增长时，停止会话并报告通路问题；不要反复重开消耗生命。模型仍可能选择停留或因墙体无法移动，应结合动作与实际输入区分。诊断工具可后台读取，无需切换游戏焦点。

导航路径和门前对齐阶段可从 `execution.navigation` 查看。空地应直达，障碍由本地导航绕过；combat 时的顶墙问题仍由战斗策略处理。复盘频率与覆盖率时，分别统计 combat/navigation/planning，清房后的等待不能算作战斗推理停顿。房间是否曾有怪、是否由 AI 清完，查战斗事件和前后状态，避免几秒后首次观察时误判为空房。

## 道具知识

从工具提供的物品类型、可见 ID/名称查 `get_item_details`。优先批量查当前装备及新宝箱房/商店候选。未知道具不通过内部 ID 猜测其身份。

未命中时：`wiki_search` 返回灰机 Wiki 搜索入口；用 dsh 的网页搜索/浏览器工具读 https://isaac.huijiwiki.com/ 对应正文。`wiki_read` 给出读取要求。核对类别、ID、适用版本，提取关键效果、条件、副作用、重要组合，用 `knowledge_upsert` 保存带来源和短正文依据的摘要。格式见 [knowledge.md](references/knowledge.md)。

网站可能要求 JavaScript/Cookie。验证页、未读正文、搜索摘要分别处理；读不到的事实保持 unknown/partial，并向用户说明。基础走门/普通拾取可以继续，未知道具可暂存或在说明未知后做选择。游戏内实际价格/属性优先于百科默认值。

## 当前执行范围

- 通过正常输入走门，必要时正常消耗钥匙打开宝箱房/商店；由工具候选检查资源。
- 拿宝箱房道具，互斥选择后刷新候选；主动道具可以持有，本期不使用。
- 商店只查看：商品包括免费商品都要避让。购买、箱子/机器/按钮、炸弹、主动、卡牌/药丸使用和楼梯/传送不属于当前执行范围。
- 战斗由 L40S v004 完成，残留敌方弹幕继续战斗。刀/蓄力/其他控制转换道具参考知识兼容性说明；不要假定当前战斗头支持。

## 扩展

工具和目标可通过项目 `skill_extensions/*.py` 注册，服务重启后 `list` 自动显示新接口。扩展方法、服务配置和目标执行接口见 [extensions.md](references/extensions.md)。扩展代码来自用户的项目，Wiki 内容作为资料保存。
