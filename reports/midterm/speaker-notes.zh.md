## 01. Isaac Jev

这份报告介绍 Isaac Jev，一个面向《以撒的结合》的游戏智能体。玩家在房间之间战斗并收集资源。项目将较长期的规划与快速战斗决策分开，使用结构化游戏数据，以及本项目自行训练的战斗模型。

资料依据:

- [Project design](../../docs/isaac_jev_design.md)

## 02. 完整系统框架 高层规划与实时战斗

框架连接游戏采集、高层规划与实时战斗。游戏模组采集位置、生命、实体和房间几何。本地 Python 状态桥录制数据，并管理执行。

dsh 语言助手选择探索房间、拾取物品等目标。战斗模型运行在 L40S GPU 上，选择短时移动和射击动作。本地执行器负责导航、切换到战斗，并返回实际观察到的进展。

资料依据:

- [Local execution](../../src/isaac_jev/skill_runtime.py)
- [State bridge](../../src/isaac_jev/bridge.py)

## 03. Jev 输入文本设计 参考 TerraBlind 的事实组织方法

文本设计借鉴 TerraBlind 的状态事实、目标与候选动作描述。它的战斗链路调用 TypeSafe Jev；本项目自行训练基于 Qwen 的决策头。

当前 Qwen 输入已经很复杂，包含武器能力、道具效果、威胁几何、路线和实时反馈。把与行动相关的文本关系组织清楚，有助于模型比较选项。实机观察提示，这种改进可以帮助战斗决策，但其收益仍需与数据和训练变化分开验证。

这个任务的意义，是让预训练语言模型的语义能力进入快速战斗决策循环。脚本计算事实，模型利用语言表征比较有意义的动作。未知信息保持未知，未来结果只用于训练监督。

资料依据:

- [TerraBlind Jev request](https://github.com/Reisenbug/TerraBlind/blob/6b3b90ed419c91e9741690b551ca4093e2a9206e/Jev/Fight/Dodge.cs)
- [Verified text-method analysis](../../docs/TERRABLIND_ISAAC_TRANSFER.md)
- [Our input generator](../../src/isaac_jev/features_v010.py)
- [Semantic input design and evaluation plan](../../docs/COMBAT_SEMANTIC_INPUT_PROPOSAL.md)

## 04. 自训练 Jev 架构 冻结主干与独立动作决策塔

冻结的 Qwen 主干一次编码当前场景，得到 2,048 维文本特征。独立的移动和射击塔分别结合 1,018 维数值特征，输出九种移动与五种射击状态，覆盖 45 种组合。约一百万个决策头参数参与训练，其中包括仅训练使用的质量辅助头。

Jev 式架构的优势，是把丰富的语言表征直接接到动作选择上。如果每次都让生成式 LLM 逐 token 生成完整分析和动作说明，就会增加战斗循环的延迟。这里通过决策头直接输出控制动作。一条已测 V009 的 L40S 请求中，模型计算约 53 毫秒，不含网络与游戏执行。

与 BERT 这类较小的文本编码器相比，采用 Qwen 是为了探索更丰富语义知识与能力描述的迁移潜力。

资料依据:

- [Our model architecture](../../src/isaac_jev/model.py)
- [V010 configuration](../../artifacts/combat-v010-tactical/combat-v010-tactical-s01/policy_config.json)
- [One measured V009 request on L40S](../../docs/benchmarks/policy-link-recovery-v009.json)
- [BERT encoder reference](https://arxiv.org/abs/1810.04805)
- [Generalization questions and comparison plan](../../docs/COMBAT_RL_AND_GENERALIZATION_PROPOSAL.md)

## 05. 一层游戏 Demo

下面的短片展示一次第一层游戏过程，请关注规划、战斗和资源拾取之间的交接。

*播放约 40 秒录屏；录制模型按实际版本填写，不把旧录屏称为 V010。*

## 06. dsh skill 设计 行为协议、工具 Schema 与本地执行闭环

dsh skill 定义助手如何使用工具。目标是在推进通关的过程中管理生命、钥匙、金币和道具。它观察游戏，从工具生成的合法候选中选择并提交目标，再等待反馈。

十六个基础工具涵盖状态、知识、规划和会话控制。本地运行时负责执行，只有观察到预期的游戏状态变化，才判定任务成功。

资料依据:

- [Behavior protocol](../../skills/isaac-play/SKILL.md)
- [Tool registry](../../src/isaac_jev/skill_service.py)

## 07. 项目迭代时间轴 后续改进与通关目标

时间轴概括了项目的演进。V003 到 V005 建立文本与数值融合、动作时序对齐及能力上下文；V006 和 V007 比较标注、文本格式与决策头；V008 筛选优质示范；V009 增加执行和进展反馈；V010 增加稳定攻击路线与阶段信息。

后续有三个方向。第一，继续提高数据数量和质量，补充缺失的敌人、武器及攻击阶段。第二，继续完善 Jev 文本格式，让能力、候选动作后果和实时反馈更清楚。第三，继续完善 dsh skill 的资源运营、探索与异常恢复能力。最终期望仅凭借这套架构实现游戏完整通关。

资料依据:

- [Early combat foundations](../../docs/TRAINING_V004.md)
- [Controlled variants](../../docs/EXPERIMENTS_V006_RESULTS.md)
- [Quality demonstrations](../../docs/TRAINING_QUALITY_V008.md)
- [Execution feedback](../../docs/FEEDBACK_V009.md)
- [Stable routes and phase information](../../docs/FEEDBACK_V010.md)
- [Skill behavior protocol](../../skills/isaac-play/SKILL.md)

## 08. 感谢聆听

感谢聆听，欢迎提问与建议。

资料依据:

- [TerraBlind](https://github.com/Reisenbug/TerraBlind)
- [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)
- [REPENTOGON / Isaac API](https://repentogon.com/docs.html)
- [REPENTOGON / Isaac API](https://wofsauge.github.io/IsaacDocs/rep/)

## 09. 附录：V010 层设计与参数量

V010 动作两塔共有 929,806 个参数。文本投影每塔 266,624，数值投影 130,688，融合层 65,792。质量辅助头包含 45 × 16 的动作嵌入，以及 528 到 128、128 到 35 的两层网络，共 72,947 个参数，仅用于训练。相加得到 1,002,753。

资料依据:

- [V010 weight tensors](../../artifacts/combat-v010-tactical/combat-v010-tactical-s01/head.safetensors)
- [Layer definitions](../../src/isaac_jev/model.py)

## 10. 附录：早期九组实验结果

这些是早期 V006 的九组对照实验，比较标注、文本和决策头设计，每组训练三十轮。它们使用 2,881 条 positive 测试子集，与新版 3,079 条完整测试集不同，不能直接按表中准确率作跨阶段排名。TypeSafe 原生 Jev 的内部基座与冻结方式没有在所核对资料中确认，本报告的具体架构只描述本项目自行训练的模型。

资料依据:

- [Historical V006 results](../../docs/EXPERIMENTS_V006_RESULTS.md)
