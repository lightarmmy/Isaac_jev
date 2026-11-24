# isaac-play：dsh 技能与游戏工具

首期实现已打包为一个技能目录，安装在 `C:\Users\11691\.dsh\skills\isaac-play`。源码入口为 `skills/isaac-play/SKILL.md`；ZIP 为 `.local/isaac-play-skill.zip`。包包含技能、工具脚本、MCP 适配、标准库运行时、连接配置、模组资产及文件校验，没有 Qwen 权重。

## 使用

1. 保持游戏桥 `Start_Recording.cmd` 和 `Start_Server_Tunnel.cmd` 运行；关闭独立 shadow/30 秒 AI 客户端。
2. 实验游戏启用模组 0.4.2；已通过控制台重载并确认完整地图/导航字段及 start-relative 输入时序。F8 回人工，游戏中进入一局。
3. 在 dsh 新对话中请求：

```text
使用 isaac-play 技能，自动探索当前以撒楼层：连续走房间、战斗后捡掉落、拿宝箱房道具、查看商店。道具效果先查字典，缺失时查以撒灰机 Wiki 并补库。商店只查看。一直执行到完成、死亡或我接管。
```

4. 发出请求后回游戏取消暂停，保持游戏前台。start_autoplay 默认等待新游戏帧最多 20 秒。F8/Esc 会停止整个自动会话。

若 dsh 未自动识别新技能，让它读取 `C:\Users\11691\.dsh\skills\isaac-play\SKILL.md`。CLI 工具不要求另外注册插件；MCP 可配置当前 Python executable + 技能脚本绝对路径 + mcp 参数。

## 已实现

15 个工具：doctor、observe_game、get_floor_map、get_room_details、get_item_details、wiki_search、wiki_read、knowledge_upsert、get_rules、get_available_actions、start_autoplay、execute_plan、wait_for_decision、get_execution_status、stop_autoplay。

本地 HTTP 服务仅监听 127.0.0.1:9530。CLI 与 stdio MCP 使用同一工具目录与 JSON Schema。复杂参数推荐 `--args-file`，支持 UTF-8。MCP 初始化、工具枚举和工具调用已测试。

状态来自游戏模组：楼层房间/门连接、碰撞与房间形状网格、玩家/背包/掉落/价格、可见性和互斥组。规划器读取完整已知地图；盲道具 ID 与未发现地图数据在详细查询中同样过滤。

执行器按当前状态生成门/掉落/宝箱道具/商店查看/回访等目标，使用本地 A* 与位置反馈，通过正常输入执行。商店商品、未支持交互和非目标道具作为避让对象；完成以实际房间、库存和资源变化核对。

导航使用玩家尺寸扩张的矩形碰撞与完整移动段检查；无障碍走直达路段，有障碍合并安全路径。路径保持稳定，计入惯性和已发送输入的延迟，门前先对齐再向外走。详细路径与阶段位于 `execution.navigation`。实现、模拟验证和下一轮战斗地形输入见 [导航与地形](NAVIGATION_TERRAIN.md)。

战斗调用 L40S combat-v004，保持原有输入契约、原观测帧与 6 帧有效期。本地只运行标准库工具与导航，没有加载 Torch 或 Qwen。游戏桥的控制租约限制输入所有者，旧租约及过期/换房动作不能重标新帧重用。

知识库保存类型/ID/版本、效果、来源正文短依据和修订。Wiki HTTP 读取遇到验证页面，因此网页定位/读取交给 dsh 已有的网页工具；技能引导补库，不把验证页、空正文或未证实数值写成事实。实际在线补库需在 dsh 首次调用中核验。

## 验证与限制

已通过单元及模拟闭环测试：过门、等待战斗/残弹结束、反馈移动与道具拾取、商店商品避让、旧状态拒绝、F8/租约撤销、字典修订及扩展工具。实机只读核对已读取 12 个房间、当前三个门，三个门路径均可规划；L40S 实机状态请求约 72 ms，未注入游戏动作。

这些检查不等同于 dsh 实机整层完成。首轮需要观察路径误差、无法拾取/错误选择、战斗头的弱移动能力，以及 dsh 网页查询结果。无法达成的目标会返回 blocked/remaining，而非声称完成。服务日志 `.local/isaac-skill/service.log`，目标/交接/前后状态日志 `events.jsonl`。

## 更新与扩展

`Install_Dsh_Skill.cmd` 重新打包并安装本技能；`Start_Skill_Tools.cmd` 启动工具服务并显示连接检查。更新运行时代码/扩展后，脚本 shutdown 再 setup。服务关闭会释放本服务控制，采集桥与 L40S 模型继续运行。

`skill_extensions/*.py` 可以注册新查询工具与新目标 provider/navigator/completion handler，list/MCP 目录自动更新。接口见技能 references/extensions.md；新增购买、主动/炸弹等行为需同时扩展能力边界与完成判据。
