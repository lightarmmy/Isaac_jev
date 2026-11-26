## 01. Isaac Jev

This talk introduces Isaac Jev, an agent for The Binding of Isaac, a game where the player fights and collects resources room by room. The project separates longer-term planning from fast combat decisions. It uses structured game data and a combat model trained within this project.

Sources:

- [Project design](../../docs/isaac_jev_design.md)

## 02. System framework Planning and real-time combat

The framework links game sensing, high-level planning and real-time combat. A game mod collects positions, health, entities and room geometry. A local Python bridge records the data and manages execution.

The dsh language assistant chooses goals, such as exploring a room or collecting an item. The combat model runs on an L40S GPU and selects short movement and shooting actions. The local executor handles navigation, switches into combat, and returns observed progress.

Sources:

- [Local execution](../../src/isaac_jev/skill_runtime.py)
- [State bridge](../../src/isaac_jev/bridge.py)

## 03. Jev text input design Facts inspired by TerraBlind

TerraBlind inspired our use of state facts, goals and candidate consequences. Its combat pipeline calls TypeSafe’s Jev. Here, we train our own Qwen-based decision heads.

Our Qwen input is already complex: weapon abilities, item effects, threat geometry, routes and feedback. Clear, action-relevant text helps the model compare these relationships. Playtest observations suggest this can improve combat decisions, although its effect still needs to be isolated from data and training changes.

The aim is to bring pretrained semantic knowledge into a fast combat loop. Scripts calculate facts, while the model compares meaningful action choices. Unknown facts stay unknown, and future outcomes provide training supervision only.

Sources:

- [TerraBlind Jev request](https://github.com/Reisenbug/TerraBlind/blob/6b3b90ed419c91e9741690b551ca4093e2a9206e/Jev/Fight/Dodge.cs)
- [Verified text-method analysis](../../docs/TERRABLIND_ISAAC_TRANSFER.md)
- [Our input generator](../../src/isaac_jev/features_v010.py)
- [Semantic input design and evaluation plan](../../docs/COMBAT_SEMANTIC_INPUT_PROPOSAL.md)

## 04. Our Jev-style architecture Frozen backbone and trained action towers

The frozen Qwen backbone encodes the situation once into 2,048 features. Independent movement and shooting towers combine these with 1,018 numeric features and select 45 action combinations. About one million head parameters are trained, including a training-only quality auxiliary.

This is the Jev-style advantage: rich language features feed direct action selection. Generating a full reasoning trace and action description token by token would add latency to every combat update. Our head selects controls without generating that text. One measured V009 request on L40S used about 53 milliseconds for model computation, excluding network and game execution.

Compared with a smaller text encoder such as BERT, using Qwen aims to transfer richer semantic and capability knowledge.

Sources:

- [Our model architecture](../../src/isaac_jev/model.py)
- [V010 configuration](../../artifacts/combat-v010-tactical/combat-v010-tactical-s01/policy_config.json)
- [One measured V009 request on L40S](../../docs/benchmarks/policy-link-recovery-v009.json)
- [BERT encoder reference](https://arxiv.org/abs/1810.04805)
- [Generalization questions and comparison plan](../../docs/COMBAT_RL_AND_GENERALIZATION_PROPOSAL.md)

## 05. First-floor game demo

This short clip illustrates one first-floor run. Look for the handoffs between planning, combat and resource collection.

*Play about 40 seconds. Identify the recording’s actual model; do not label an older recording as V010.*

## 06. dsh skill design Behavior protocol, tool schemas and execution

The dsh skill defines how the assistant uses tools. Its goal is to manage health, keys, coins and items while progressing toward completing the run. It observes the game, chooses a valid tool-generated goal, submits that goal, and waits for feedback.

Sixteen base tools cover state, knowledge, planning and session control. The local runtime handles execution. A task succeeds when the expected game-state change is observed.

Sources:

- [Behavior protocol](../../skills/isaac-play/SKILL.md)
- [Tool registry](../../src/isaac_jev/skill_service.py)

## 07. Development timeline Next steps toward a complete run

The timeline summarizes the development. V003 to V005 established text-and-number fusion, control alignment and ability context. V006 and V007 compared labeling, text formats and decision heads. V008 selected higher-quality demonstrations. V009 added execution and progress feedback. V010 added stable attack routes and phase information.

Three directions come next. First, increase both the amount and quality of data, including missing enemies, weapons and attack phases. Second, improve Jev’s text format so abilities, candidate consequences and feedback are clearer. Third, expand the dsh skill’s resource management, exploration and recovery capabilities. The goal is to complete a full game run using this architecture.

Sources:

- [Early combat foundations](../../docs/TRAINING_V004.md)
- [Controlled variants](../../docs/EXPERIMENTS_V006_RESULTS.md)
- [Quality demonstrations](../../docs/TRAINING_QUALITY_V008.md)
- [Execution feedback](../../docs/FEEDBACK_V009.md)
- [Stable routes and phase information](../../docs/FEEDBACK_V010.md)
- [Skill behavior protocol](../../skills/isaac-play/SKILL.md)

## 08. Thank you

Thank you for listening. Questions and suggestions are welcome.

Sources:

- [TerraBlind](https://github.com/Reisenbug/TerraBlind)
- [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)
- [REPENTOGON / Isaac API](https://repentogon.com/docs.html)
- [REPENTOGON / Isaac API](https://wofsauge.github.io/IsaacDocs/rep/)

## 09. Appendix: V010 layers and parameters

V010’s two action towers contain 929,806 parameters. Each text projection has 266,624, numeric projection 130,688 and fusion 65,792. The quality auxiliary combines a 45-by-16 action embedding with 528-to-128 and 128-to-35 linear layers, adding 72,947 training-only parameters. The total is 1,002,753.

Sources:

- [V010 weight tensors](../../artifacts/combat-v010-tactical/combat-v010-tactical-s01/head.safetensors)
- [Layer definitions](../../src/isaac_jev/model.py)

## 10. Appendix: the earlier nine experiments

These earlier V006 experiments compare annotations, text and decision heads, with thirty epochs each. They use a 2,881-sample positive test subset, which differs from the recent full 3,079-sample test set. The percentages do not support a direct cross-stage ranking. TypeSafe’s internal base model and freezing policy have not been verified. The concrete architecture in this talk describes this project’s own trained model.

Sources:

- [Historical V006 results](../../docs/EXPERIMENTS_V006_RESULTS.md)
