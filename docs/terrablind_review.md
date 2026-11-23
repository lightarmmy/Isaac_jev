# TerraBlind 实现核对与以撒项目的对应方案

核对日期：2026-11-23。依据用户确认的视频及仓库当前 main 分支；视频拍摄时的代码版本未单独定位。

- [用户所指视频](https://www.bilibili.com/video/BV1Boe26JEh4/)
- [TerraBlind](https://github.com/Reisenbug/TerraBlind)

## 已核实的实现

TerraBlind 使用 tModLoader 获取真实 Terraria 状态。它的总体结构是 LLM 规划、Jev 现场判断、游戏执行层持续操作。

Boss 战实际接口在 [Dodge.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Fight/Dodge.cs)：发送到 `https://api.typesafe.ai/v1/systemone`，模型字段为 `jev-latest`。一次 Choice 在完整躲避候选中选择一个动作。

这条战斗链路调用现成 TypeSafe API，并通过上下文提供策略；它没有在调用期间训练 Qwen 主干或替换并训练本地决策头。TypeSafe 模型规模不能由这个视频反推出。

### 状态

[DecisionFacts.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Port/DecisionFacts.cs) 将游戏事实转换为相对坐标、统一速度单位和摘要，包括：

- 玩家血量比例、持续掉血、水平和垂直速度。
- 二段跳、飞行、钩爪、坐骑等移动能力。
- Boss 与配件的相对位置、速度、血量和接触威胁。
- 预计接触时间、当前速度与近期最高速度。
- 四侧地形与世界边界空间。
- 来袭投射物摘要，当前实现筛选并最多提供 12 个。
- 当前 Boss 的打法和 Agent 空间/行为约束。

碰撞时间和路径摘要是代码提供的近似预测，不是视觉模型推断。它们仍需针对加速、转向等行为验证。

### 候选动作

[GameMovement.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Port/GameMovement.cs) 中基础组合是：

- 水平 5 种：无分量、左 Small、左 Medium、右 Small、右 Medium。
- 垂直 6 种：无分量、上 Small、上 Medium、上 Large、下 Small、下 Large。

包含原地在内，组合上限为 5 × 6 = 30。随后按地形、边界、钩点、装备等条件过滤，实际数量变化。

Small/Medium/Large 对应已实现的移动原语，例如普通移动、跳跃、二段跳、钩爪和坐骑。候选描述还提供粗略位移、Boss 间距趋势和弹幕走廊风险。

模型选择完整方向与幅度；代码将其落实为游戏控制输入，处理钩爪取消、坐骑切换和可用性检查。

### 上下文里的 Boss 知识

[BossBook.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Fight/BossBook.cs) 为支持的 Boss 提供具体打法文字，例如攻击阶段、应躲避的方向、距离和优先目标。它们随当前 Boss 放入 state。

这属于上下文提供知识，不是模型自动在战斗中更新权重。代码支持关闭这些知识做对照。部分条目含为展示场地编写的前提，移植时不能当成适用于任意场地的完整规则。

### 时钟与攻击

模型调用在后台异步执行，游戏主线程继续推进。新回答回来后切换动作；等待期间继续上一动作。当前 Boss 控制代码中意图有效期为 1500 ms，过期或不属于当前执行过程的回答会被丢弃。

源码中的 300 ms 常量用于随机对照组。不能据此宣称 Jev 稳定每 300 ms 回答一次；实际延迟由请求测量。

[ProjectileAim.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Port/ProjectileAim.cs)、[Intercept.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Port/Intercept.cs) 与 [Combat.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Fight/Combat.cs) 实现攻击目标与预测瞄准等功能。瞄准和持续攻击并非全部由 Jev 决策承担。

[JevPrior.cs](https://github.com/Reisenbug/TerraBlind/blob/main/Jev/Fight/JevPrior.cs) 记录选择频率，用于随机对照采样，不是强化学习更新。

README 的早期 code-only 肉山流程列出了无敌、关闭刷怪和倍速等辅助；它是另一个演示，不应把这些设置未经核实地归到用户发的 Jev Boss 战视频，也不能将早期流程视为模型逐步规划和战斗的证据。

## 其他游戏演示的边界

[Jev Pong](https://github.com/ably-labs/jev-pong) 从数值状态选择上/下/停，但球每收到一次模型决策才推进一步。它展示输出形式和调用延迟；这个时钟不能直接证明模型能跟上以撒固定 30 Hz 逻辑更新。

[Jev plays Balatro](https://github.com/IgorWarzocha/jev-plays-balatro) 从结构化状态与选项进行全局规划和战术选择。该项目保存了失败结果，README 明确没有展示获胜整局。它适合参考状态/候选协议，任务类型和实时压力与以撒不同。

## DeepSeek 玩以撒：目前能确认与不能确认的内容

用户尚未提供对应 DeepSeek 视频或代码。公开检索发现了问答、生成 mod、选择道具和已有代码战斗机器人等不同项目，目前不能确认具体视频使用哪种方法。

例如 [isaac-agent-bridge](https://github.com/xianyuons/isaac-agent-bridge) 可以配置 DeepSeek，但其职责是选择生成的道具，而且 README 标注尚未实机验证。这不等于 DeepSeek 逐帧移动、瞄准与躲弹。

[BotCoop](https://github.com/Joaquin-Galle-ui/BotCoop-TheBindingofIsaac) 的说明明确把战斗/移动交给 Lua，将 LLM 用于交流和高层命令。它是另一种架构示例，不能代替对用户所见 DeepSeek 视频的核实。

若复现基于 DeepSeek 的控制，需确认以下四项：

1. 模型输入是截图还是 mod 导出的结构化数据。
2. 模型输出是按键、短动作、坐标目标还是高层命令。
3. 游戏是否在等待模型期间暂停/减速，或由既有控制器继续战斗。
4. 模型是否真的训练过，还是现成 API 加规则/提示与工具。

## 以撒的现成桥接参考

[SocketBridge](https://github.com/EmptyEmeraldTablet/SocketBridge/tree/refactor/v2) 的公开代码实现了 Lua 与 Python 的 TCP 通信、状态订阅、录制、序列号和移动/射击输入注入。

其 `main.lua` 使用 `socket.core`，README 要求 `--luadebug`；在 Repentance+ 与所选 REPENTOGON 版本上仍需实机探针。它提供可复用的架构和源码，不意味着已在当前工作区验证。

状态包括玩家、敌人、投射物/激光、地形和拾取物等；Python Facade 提供 `send_input(move=..., shoot=...)`。可以在此基础上补原始人类按键采集、动作生效回执与有限持有时间，进行示范学习。

## 2B 与 8B 的选择

L40S 具有 48 GB 显存。按 FP16/BF16 每参数约 2 字节估算，2B 权重约 4 GB，8B 约 16 GB，均可在合理上下文和批量下推理、冻结主干训练小头。具体模型实际参数数与结构会改变数字。

这只是权重体积，不是总显存。工作区、当前状态激活、批处理、CUDA graph 和其他驻留模型需要额外显存。

冻结主干减少反向传播和主干优化器状态，不会免除每次新状态的主干前向计算。不能只凭剩余显存认定 8B 对实时控制更合适。

建议以 Qwen 2B 级主干为首个训练对象，对同一批示范和同一测试场景比较 8B。主要指标是清房成功率、受伤、执行延迟和状态年龄，而不是只看离线标签准确率。

如果 8B 效果更好但难以满足微操作频率，可使用明确分工的两级系统：

- 较低频的冻结大主干读取规则、敌人类别、道具效果与战术背景。
- 高频 MLP/GRU 控制头读取最新坐标、速度、投射物、危险摘要和上一动作，完成短时控制。

这里高频头必须获取新状态，不能把较低频旧主干输出当成当前弹幕事实。训练应复现相同更新时序，并记录主干特征年龄。

另一种方案是类似 TerraBlind，把瞄准/操作时序交给规则执行器，让 2B/8B 只选战术或躲避意图。它能更快形成 Demo，但与学习用户全部移动和射击的 45 类控制任务是不同的能力目标。

## 对本项目的建议

用户之前提出的示范学习路线可以保留：

1. Repentance+ + REPENTOGON，先实机验证 SocketBridge 风格的状态与输入桥。
2. 30 Hz 采集你实际游玩时的原始状态、移动/射击按键、上一动作和时间戳。
3. 冻结 2B 主干，训练 MLP 联合动作头，初版是 9 种移动 × 5 种射击 = 45 类。
4. 与冻结 8B 使用相同示范、按整局/房间划分的数据和实机场景做对照。
5. 保留规则、危险摘要和合法候选的生成代码；若加入自动瞄准或规则躲避，单独记录与评价其贡献。
6. 先影子预测，再闭环控制；你接管纠正模型遇到的失误状态，补充数据后继续训练。

冻结主干可预计算训练特征，多轮训练头不必重跑 Qwen。它能降低训练成本，但部署时仍需按所选架构计算当前输入。

具体状态、45 类动作协议、采集与验收方案见 [isaac_jev_design.md](isaac_jev_design.md)。截至本次核对，没有安装游戏桥、训练模型或实测 L40S。
