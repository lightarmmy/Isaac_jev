# 中英对照讲稿 / Bilingual speaker script

主稿约 5 分钟，含约 40 秒 Demo。英文 528 词，适合约 125–135 词/分钟，并留出看图与换页的停顿。演讲任选一种语言，附录仅用于问答。

The main talk targets five minutes including a 40-second demo. Read one language, with short pauses for diagrams and slide transitions. The appendices are for questions.

## P1. Isaac Jev

Isaac Jev · 20 s

**English 1**

This talk introduces Isaac Jev, an agent for The Binding of Isaac, a game where the player fights and collects resources room by room. The project separates longer-term planning from fast combat decisions. It uses structured game data and a combat model trained within this project.

**中文 1**

这份报告介绍 Isaac Jev，一个面向《以撒的结合》的游戏智能体。玩家在房间之间战斗并收集资源。项目将较长期的规划与快速战斗决策分开，使用结构化游戏数据，以及本项目自行训练的战斗模型。

## P2. System framework Planning and real-time combat

完整系统框架 高层规划与实时战斗 · 35 s

**English 1**

The framework links game sensing, high-level planning and real-time combat. A game mod collects positions, health, entities and room geometry. A local Python bridge records the data and manages execution.

**中文 1**

框架连接游戏采集、高层规划与实时战斗。游戏模组采集位置、生命、实体和房间几何。本地 Python 状态桥录制数据，并管理执行。

**English 2**

The dsh language assistant chooses goals, such as exploring a room or collecting an item. The combat model runs on an L40S GPU and selects short movement and shooting actions. The local executor handles navigation, switches into combat, and returns observed progress.

**中文 2**

dsh 语言助手选择探索房间、拾取物品等目标。战斗模型运行在 L40S GPU 上，选择短时移动和射击动作。本地执行器负责导航、切换到战斗，并返回实际观察到的进展。

## P3. Jev text input design Facts inspired by TerraBlind

Jev 输入文本设计 参考 TerraBlind 的事实组织方法 · 45 s

**English 1**

TerraBlind inspired our use of state facts, goals and candidate consequences. Its combat pipeline calls TypeSafe’s Jev. Here, we train our own Qwen-based decision heads.

**中文 1**

文本设计借鉴 TerraBlind 的状态事实、目标与候选动作描述。它的战斗链路调用 TypeSafe Jev；本项目自行训练基于 Qwen 的决策头。

**English 2**

Our Qwen input is already complex: weapon abilities, item effects, threat geometry, routes and feedback. Clear, action-relevant text helps the model compare these relationships. Playtest observations suggest this can improve combat decisions, although its effect still needs to be isolated from data and training changes.

**中文 2**

当前 Qwen 输入已经很复杂，包含武器能力、道具效果、威胁几何、路线和实时反馈。把与行动相关的文本关系组织清楚，有助于模型比较选项。实机观察提示，这种改进可以帮助战斗决策，但其收益仍需与数据和训练变化分开验证。

**English 3**

The aim is to bring pretrained semantic knowledge into a fast combat loop. Scripts calculate facts, while the model compares meaningful action choices. Unknown facts stay unknown, and future outcomes provide training supervision only.

**中文 3**

这个任务的意义，是让预训练语言模型的语义能力进入快速战斗决策循环。脚本计算事实，模型利用语言表征比较有意义的动作。未知信息保持未知，未来结果只用于训练监督。

## P4. Our Jev-style architecture Frozen backbone and trained action towers

自训练 Jev 架构 冻结主干与独立动作决策塔 · 60 s

**English 1**

The frozen Qwen backbone encodes the situation once into 2,048 features. Independent movement and shooting towers combine these with 1,018 numeric features and select 45 action combinations. About one million head parameters are trained, including a training-only quality auxiliary.

**中文 1**

冻结的 Qwen 主干一次编码当前场景，得到 2,048 维文本特征。独立的移动和射击塔分别结合 1,018 维数值特征，输出九种移动与五种射击状态，覆盖 45 种组合。约一百万个决策头参数参与训练，其中包括仅训练使用的质量辅助头。

**English 2**

This is the Jev-style advantage: rich language features feed direct action selection. Generating a full reasoning trace and action description token by token would add latency to every combat update. Our head selects controls without generating that text. One measured V009 request on L40S used about 53 milliseconds for model computation, excluding network and game execution.

**中文 2**

Jev 式架构的优势，是把丰富的语言表征直接接到动作选择上。如果每次都让生成式 LLM 逐 token 生成完整分析和动作说明，就会增加战斗循环的延迟。这里通过决策头直接输出控制动作。一条已测 V009 的 L40S 请求中，模型计算约 53 毫秒，不含网络与游戏执行。

**English 3**

Compared with a smaller text encoder such as BERT, using Qwen aims to transfer richer semantic and capability knowledge.

**中文 3**

与 BERT 这类较小的文本编码器相比，采用 Qwen 是为了探索更丰富语义知识与能力描述的迁移潜力。

## P5. First-floor game demo

一层游戏 Demo · 45 s

**English 1**

This short clip illustrates one first-floor run. Look for the handoffs between planning, combat and resource collection.

**中文 1**

下面的短片展示一次第一层游戏过程，请关注规划、战斗和资源拾取之间的交接。

*Play about 40 seconds. Identify the recording’s actual model; do not label an older recording as V010.*

*播放约 40 秒录屏；录制模型按实际版本填写，不把旧录屏称为 V010。*

## P6. dsh skill design Behavior protocol, tool schemas and execution

dsh skill 设计 行为协议、工具 Schema 与本地执行闭环 · 40 s

**English 1**

The dsh skill defines how the assistant uses tools. Its goal is to manage health, keys, coins and items while progressing toward completing the run. It observes the game, chooses a valid tool-generated goal, submits that goal, and waits for feedback.

**中文 1**

dsh skill 定义助手如何使用工具。目标是在推进通关的过程中管理生命、钥匙、金币和道具。它观察游戏，从工具生成的合法候选中选择并提交目标，再等待反馈。

**English 2**

Sixteen base tools cover state, knowledge, planning and session control. The local runtime handles execution. A task succeeds when the expected game-state change is observed.

**中文 2**

十六个基础工具涵盖状态、知识、规划和会话控制。本地运行时负责执行，只有观察到预期的游戏状态变化，才判定任务成功。

## P7. Development timeline Next steps toward a complete run

项目迭代时间轴 后续改进与通关目标 · 50 s

**English 1**

The timeline summarizes the development. V003 to V005 established text-and-number fusion, control alignment and ability context. V006 and V007 compared labeling, text formats and decision heads. V008 selected higher-quality demonstrations. V009 added execution and progress feedback. V010 added stable attack routes and phase information.

**中文 1**

时间轴概括了项目的演进。V003 到 V005 建立文本与数值融合、动作时序对齐及能力上下文；V006 和 V007 比较标注、文本格式与决策头；V008 筛选优质示范；V009 增加执行和进展反馈；V010 增加稳定攻击路线与阶段信息。

**English 2**

Three directions come next. First, increase both the amount and quality of data, including missing enemies, weapons and attack phases. Second, improve Jev’s text format so abilities, candidate consequences and feedback are clearer. Third, expand the dsh skill’s resource management, exploration and recovery capabilities. The goal is to complete a full game run using this architecture.

**中文 2**

后续有三个方向。第一，继续提高数据数量和质量，补充缺失的敌人、武器及攻击阶段。第二，继续完善 Jev 文本格式，让能力、候选动作后果和实时反馈更清楚。第三，继续完善 dsh skill 的资源运营、探索与异常恢复能力。最终期望仅凭借这套架构实现游戏完整通关。

## P8. Thank you

感谢聆听 · 5 s

**English 1**

Thank you for listening. Questions and suggestions are welcome.

**中文 1**

感谢聆听，欢迎提问与建议。
