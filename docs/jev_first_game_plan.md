# Jev 游戏项目：首个游戏与训练方案

资料核对日期：2026-11-23。

本项目采用 Qwen 主干加决策输出的路线：输入规则、可观察状态和合法动作，直接输出动作概率。目标是快速游玩游戏。

## 首个游戏

推荐《杀戮尖塔》（Slay the Spire）。首个 Demo 的范围是铁甲战士初始牌组的基础战斗挑战：完成一场战斗后自动开始下一场，展示真实客户端内的连续出牌和胜负结果。

这是对首个 Demo 范围的约束，不是声称原版整局很简单。原版的构筑、遗物、药水、商店和事件会显著增加规则规模，后续逐步接入。

建议固定如下场景：

- 铁甲战士，进阶 0。
- 初始牌组：5 张打击、4 张防御、1 张痛击。
- 第一批敌人使用邪教徒和大颚虫，先分别跑单敌人战斗。
- 血量、初始遗物等使用游戏实际设置，由 mod 导出，不在模型侧猜测。
- 第一版不加入新卡、升级、药水、地图和随机事件。
- 将战斗初始化和重置作为训练 mod 的功能；CommunicationMod 本身不保证提供任意战斗重置。

邪教徒适合验证攻击和防御取舍；大颚虫增加随当前意图改变策略的需求。是否达到稳定胜率，要用独立对局实测。

对备选游戏的判断：

| 游戏 | 接入条件 | 首版复杂度 | 判断 |
| --- | --- | --- | --- |
| 杀戮尖塔基础战斗 | CommunicationMod 已有状态和控制协议 | 在限定牌组与敌人后较低 | 首选 |
| Balatro 原版整局 | BalatroBot 已有 JSON-RPC HTTP API | 牌型组合、Joker、计分顺序和商店规划较复杂 | 适合后续项目 |
| 双人 UNO 开源实现 | RLCard 已有规则、合法动作和对局奖励 | 较低 | 适合训练管线验证；展示效果与商业客户端有区别 |

## 已核实的 mod 接口

[CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod) 依赖 ModTheSpire 和 BaseMod。它在游戏状态稳定时，通过标准输入输出向外部进程发送 JSON，并接受控制命令。

公开协议包含：

- `START PlayerClass [AscensionLevel] [Seed]`：开始一局。
- `PLAY CardIndex [TargetIndex]`：出牌，可带目标。
- `END`：结束回合。
- `CHOOSE ChoiceIndex|ChoiceName`：执行当前页面的选项。
- `POTION`、`PROCEED`、`RETURN` 等。

注意 `PLAY` 的卡牌索引是 1 起始，目标索引是 0 起始。模型只返回内部动作 ID，适配器负责转换。

当前公开状态样例包含手牌、费用、是否可打出、能量、血量、格挡、敌人意图、状态效果等。卡牌实际伤害、格挡数值、完整规则描述与所有目标合法性仍需要在实现时逐项核实，必要时补充导出字段。

[spirecomm](https://github.com/ForgottenArbiter/spirecomm) 提供 Python 通信封装和一个简单 AI，可作为接入参考。

CommunicationMod 提供真实客户端控制，不等于高速无界面模拟器。训练采样速度、动画等待和战斗重置是需要实际解决的工程问题。

## 上下文与动作协议

每次决策输入由四部分组成：

1. **完整模式规则**：战斗目标、能量、抽牌、弃牌、洗牌、回合流程、格挡、伤害计算、状态衰减、终局判定。
2. **当前对象规则**：三种基础牌、当前遗物、当前敌人以及所有相关状态效果。必须包含费用、数值、触发时机、持续时间、舍入方式和叠加规则。
3. **可观察状态**：玩家能量/血量/格挡、手牌、公开牌堆组成、敌人当前意图、已有状态、必要的短历史。
4. **合法动作集合**：适配器按游戏当前状态产生所有合法的“牌 + 目标”组合以及结束回合。

完整规则针对当前挑战模式定义。完整原版规则需要随着牌池、遗物、药水和页面类型扩大而扩充。

规则文本应是固定版本的输入前缀，并保存规则版本或哈希。规则必须在训练和推理中保持一致。运行时提示可以包含完整规则，但不应把每一步重复规则都存进训练数据；样本引用规则版本即可。

游戏内部信息需要按玩家可见范围过滤：不把未公开的抽牌顺序、随机数状态、敌人未来随机动作输入策略。

接口草案（示意，不是已经运行过的游戏状态）：

```json
{
  "protocol_version": 1,
  "rules_version": "ironclad-basic-combat-v1",
  "state_id": "battle-17-step-6",
  "rules_text": "完整挑战模式规则与当前对象规则",
  "observation": {
    "player": {"hp": 40, "block": 0, "energy": 2},
    "enemy": {"id": "enemy-0", "hp": 12, "visible_attack_damage": 9},
    "hand": [
      {"id": "card-0", "name": "打击", "cost": 1, "damage": 6},
      {"id": "card-1", "name": "防御", "cost": 1, "block": 5}
    ]
  },
  "legal_actions": [
    {"id": "a0", "type": "play", "card_id": "card-0", "target_id": "enemy-0"},
    {"id": "a1", "type": "play", "card_id": "card-1"},
    {"id": "a2", "type": "end_turn"}
  ]
}
```

模型返回动作 ID 和概率；执行器检查 `state_id`，确认状态没有改变后执行。自动推进页面、等待动画和重连由代码处理。

## 模型选择与 L40S

TypeSafe 的 Jev 是闭源服务。我查到的官方资料介绍了 RLCD，但没有找到可直接复现其完整训练过程的公开损失定义和训练代码。同类开源项目的实现并不都与 TypeSafe 一致。

L40S 有 48 GB 显存。下面是按公开参数规模、每参数约 2 字节估算的权重体积，单位为十进制 GB；不是总显存，也不是 L40S 实测。运行还需要激活、工作区和批处理内存。

| 项目 / 模型 | 规模 | FP16/BF16 权重约 | 用途与限制 |
| --- | --- | --- | --- |
| [Qwen-jev](https://github.com/AnubhabBanerjee/Qwen-jev) | Qwen2.5-Coder-1.5B | 3 GB | 冻结主干 + 二分类头教程；源代码是 PolyForm Noncommercial，Qwen 基座权重单独适用 Apache-2.0 |
| [Jev-Style v3](https://huggingface.co/chaoliangUNSW/Jev-Style-0.8B-Decision-v3) | 约 0.75B 文本主干 | 1.5 GB | 文本动态候选决策；Apache-2.0 |
| [Intern-Decision](https://github.com/internlm/Intern-Decision) | 0.8B / 2B / 4B 系列 | 1.7 / 4.4 / 9.1 GB | 文本和图像、多个决策字段；Apache-2.0；采用候选符号读出，保留语言模型输出机制的相关部分 |
| [Laya 经典模型](https://github.com/NandhaKishorM/laya) | 约 0.32B / 0.42B | 0.64 / 0.84 GB | 编码器决策模型，有公开 RLCD 训练实现；Apache-2.0；新版本另提供冻结 Qwen 等主干的引擎 |
| [Perplexity Decider Flash v1.3](https://huggingface.co/perplexity-ai/pplx-decider-flash-v1.3) | Qwen3.5-4B，实际约 4.54B | 9.1 GB | 独立 255 行决策读出，使用专用推理实现；Apache-2.0 |
| [Liquid d1-2B](https://huggingface.co/LiquidAI/d1-2B) | 约 1.92B | 3.8 GB | 单次前向决策，支持文本与视觉；LFM Open License v1.0，含商业营收条件 |
| [Open-Jev-9B](https://huggingface.co/ZefanCai/Open-Jev-9B) | 9B 系列 | 约 18–20 GB，需另加载基座 | 发布的是 LoRA + 决策头；代码 MIT，适配器/头 Apache-2.0；逐候选评分的计算量需要关注 |

上述规模在适当的上下文和批量下，显存上均可以单卡 L40S 推理。速度需要分别实测，不能把其他硬件或不同输入长度的数字移植过来。

首版建议自行使用 **Qwen3.5-0.8B 文本主干 + 动作决策头 + 训练用价值头**。如果优先复用教程的依赖和实现，可用 Qwen2.5-1.5B；Coder 基座不是游戏项目的必要条件。

固定动作类型可以使用固定分类头。针对手牌位置、敌人目标和后续牌池扩展，更适合对当前合法候选动作评分。优先共享一次状态编码，避免每个候选都重跑整个主干。

训练阶段先冻结主干验证流程，再用 LoRA 改善游戏策略表示；仅靠一个冻结主干上的线性头，不保证能学到充分的游戏策略。

0.8B–2B 的冻结头训练和短上下文 LoRA 训练适合从单卡 L40S 起步。4B 也可以考虑 LoRA，但全参数 Adam 训练的梯度和优化器状态会大幅增加显存占用。初期不需要先做 4-bit 量化。

## 数据与强化学习

[Qwen-jev 的 train.py](https://github.com/AnubhabBanerjee/Qwen-jev/blob/main/train.py) 使用交叉熵，只训练头。当前样例数据为 86 条函数名称匹配记录，打印的是训练集准确率。它没有游戏训练、强化学习或独立测试集。

[Laya 的公开训练入口](https://github.com/NandhaKishorM/laya/blob/main/laya/train.py) 实现了 `loss="rlcd"` 和 `loss="soft-ce"` 两种目标。其 RLCD 是带正确评分规则奖励的噪声 logit 采样 / GRPO 风格项，加软标签交叉熵；这是该项目的实现，不是 TypeSafe 算法的已验证复刻。源码同时记录了一个 typed-decisions 对照实验中额外项未带来收益。

这类校准训练需要标签、目标概率或可验证结果，不会消除数据需求。对于本项目，可以不依赖人工游戏数据，由真实游戏交互自动收集轨迹。

建议流程：

1. 接通真实游戏，先用规则机器人完成战斗、重置、状态采集和动作执行。
2. 由规则机器人或搜索策略生成起始样本，模仿训练决策头，建立可运行基线。
3. 使用实际战斗结果训练价值头，再采用带合法动作掩码的 PPO 改进策略。杀戮尖塔是单人环境，不需要自博弈对手。
4. 比较规则机器人、冻结主干、LoRA 和强化学习后的模型，在固定测试场景外使用新的随机初始化评估。

PPO 应直接对动作分布的 `log_prob`、熵和回报计算损失。面向文本生成的现成 GRPO/PPO trainer 通常不能不加改动地套到自定义分类头上。

奖励以战斗胜负为核心，并单独记录结束血量和回合数。若加入塑形奖励，需要检查其是否诱导拖回合或无意义刷奖励，不能只按“打出伤害”训练。

动作概率表示策略选择频率，不等于执行该动作后的获胜概率。若需要概率可信度，应另设结果预测任务，使用真实对局结果和独立校准集检验。

上下文学习可用于先放入规则和少量示例做零样本/少样本基线；它不改变模型权重，也不保证长期策略水平。示例过多还会增加延迟。

## 第一版验收

- 真实客户端连续完成战斗和重置。
- 模型只执行适配器给出的合法动作，单独记录无效动作率与执行失败率。
- 在独立对局上比较胜率、胜利后的血量、回合数，记录样本量和区间。
- 在 L40S 上实测规则完整输入下的 P50/P95 推理延迟，并单独统计 mod 往返和动画等待。
- 检查改变合法选项顺序、手牌排列、规则措辞后，策略是否发生异常变化。
- 展示画面叠加当前状态、候选动作概率、所选动作和延迟。

本文件是资料核对后的实施设计。目前尚未安装游戏 mod、运行闭环、训练模型或实测 L40S。

参考：

- [TypeSafe Jev 发布](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe RLCD 说明](https://docs.typesafe.ai/introduction/machine-learning-primer)
- [CommunicationMod](https://github.com/ForgottenArbiter/CommunicationMod)
- [BalatroBot](https://github.com/coder/balatrobot)
- [RLCard UNO 规则与动作编码](https://github.com/datamllab/rlcard/blob/master/docs/games.md#uno)
