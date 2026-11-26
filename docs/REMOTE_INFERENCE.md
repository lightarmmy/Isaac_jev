# L40S 常驻推理

2026-11-25：默认模型已切换为 **combat-v005 最佳第 2 轮权重**，由 `configs/remote_policy.json` 指定。服务器入口仍为 `/xufanghui/isaac-jev`，其中 `artifacts/combat-v005` 和 `datasets/combat-v005` 链接到独立训练工作区。V004 权重保留。电脑只运行游戏桥接和远程客户端，checkpoint 留在服务器。V005 构筑、蓄力和地形输入见 [训练报告](TRAINING_V005.md)；下方 v003/v004 的表格与诊断保留为历史结果。

新服务已预热，describe 确认为 V005、best_epoch=2、`column-tables-round2-v1`，32 个固定验证/测试样本的服务方式特征与训练缓存动作一致，最低余弦相似度 0.999887。dsh 的 isaac-play 已安装新版输入及蓄力交接代码，本地工具后台和游戏桥已重启，doctor 返回 `battle_ai.ready=true`、`backend.checkpoint=combat-v005`。开始本轮实机验证前，dsh 重新读取技能，用户回游戏取消暂停以恢复新帧。

本次经实际本机 SSH 通路的准备好输入总响应中位数为 57.95/85.17/103.67ms（1281/2075/2940 tokens，各 15 次）。模型部分约 51.42/77.21/93.02ms。短/中输入本次抽样均小于 200ms；长输入最大 323.32ms，混合输入最大 281.97ms，网络偶有长尾。超过现有 200ms 观测期限的请求仍被客户端丢弃，不延长陈旧动作的期限。这些时延不包含本地特征生成、游戏采样和输入执行；实机验证需记录过期/拒绝计数。报告见 `docs/benchmarks/remote-service-v005.json`。

## 为什么原来的 L40S 比 4060 慢

L40S 容器的 CPU 配额为 8 核，但 Torch 默认使用 56 个 intra-op 和 56 个 inter-op 线程。短序列和 batch=1 时，线程调度、CPU 限流以及大量小算子的调用成本明显影响延迟。限制 intra-op/inter-op 线程后，未加融合内核的主干前向从约 800 ms 降到 245/309/421 ms。不同线程配置输出差异为 0。

Qwen3.5 包含门控线性注意力；原环境缺少对应融合内核。项目虚拟环境新增 `flash-linear-attention==0.4.0`、`fla-core==0.4.0`、`einops==0.8.1` 后，主干前向约为 50–60 ms。Torch 2.6.0+cu124、Transformers 5.3 和模型权重均未升级。CPU 配额和 HAMI 资源控制没有修改。

目前没有安装 causal-conv1d，卷积仍使用 Torch；HF 的笼统 fast-path 警告仍可能出现，但门控线性注意力已使用 FLA。不会仅凭此警告判断整个模型未加速。

## 服务配置和最新测速

服务只监听服务器 `127.0.0.1:19528`，通过 SSH 转发到电脑 `127.0.0.1:19528`。BF16、SDPA、batch=1，主干冻结，move9/shoot5 分支，主动道具禁用，推理条件固定 positive。Torch intra-op/inter-op 均为 1。模型加载一次，并在报告 ready 前预热 512/1024/1536/2048/2560/3072 token 的右侧填充长度，使用最后一个真实 token 的特征。

填充及融合内核相对训练缓存抽查 32 个固定验证/测试样本，32/32 联合动作相同，最低特征余弦相似度 0.999895。该检查不能保证所有输入都逐位相同。

每个长度测试 25 次，另测 30 个混合长度输入；长度由本次本地文本字符排序选出，服务器返回实际 token 数，中等案例与历史 1352-token 案例略有不同。

| 输入 tokens | 模型中位数 | 经 SSH 总响应中位数 | 总响应 P95 |
| --- | --- | --- | --- |
| 826 | 49.07 ms | 57.95 ms | 61.80 ms |
| 1339 | 49.81 ms | 59.97 ms | 63.84 ms |
| 2417 | 71.81 ms | 82.85 ms | 87.44 ms |

30 个混合长度输入总响应中位数 59.59 ms，P95 84.05 ms，最大 84.09 ms。全部低于 200 ms。持久连接纯 RTT 中位数约 3.2–4.1 ms。基准包含准备好的文本/数值输入发送、服务器处理、模型和返回，未包含游戏采样、本地特征构建及按键执行调度；实机闭环频率还受这些部分影响。

完整报告保存在 `.local/remote-service-benchmark-v003.json`；便于 Git 审阅的副本为 `docs/benchmarks/remote-service-v003.json`。这是当前资源条件下的顺序单客户端结果，不代表 GPU 理论峰值。加速不改变当前策略主要延续上一动作的质量限制，实机清房仍需要验证。

## 本地使用

1. 双击项目根目录 `Start_Server_Tunnel.cmd`。在 SSH 窗口输入服务器密码，并一直保持此窗口打开。密码不写入脚本。脚本会检查服务是否已运行；未运行时启动，首次需要等待预热完成。
2. 使用已配置的 Sandboxie 实验环境启动游戏。桥接采集仍按 `Start_Recording.cmd` 的原流程工作；本次无需修改原版游戏、存档或重新安装模组。
3. 双击 `Start_Remote_Shadow.cmd`。客户端会等待服务 ready 和游戏未暂停的新帧，进入 shadow。只在战斗阶段计算动作，包括清房后的敌方残弹；无敌人和弹幕时等待规划层。
4. shadow 时仍由你控制角色。结束客户端用 Ctrl+C；F8 接管回 manual 也会使客户端停止。客户端结束或断线时恢复 manual，Lua 原有指令有效期继续保留。

准备让模型实际控制战斗时，先在 shadow 窗口按 Ctrl+C 结束客户端并关闭此窗口，保持 SSH 隧道窗口打开。双击项目根目录 `Start_Remote_AI.cmd`，再回游戏取消暂停。客户端在收到新帧后切换 ai，进入有敌人或残弹的房间时松开移动/射击按键，观察模型操作。开始接管后不要用暂停切换窗口；按 Esc 暂停或 F8 都会回人工并结束本次客户端。也可以在项目终端运行：

```powershell
.venv\Scripts\python.exe run.py remote-infer --mode ai --max-seconds 30
```

此命令只控制战斗，30 秒后回 manual。清房后不负责走门、拾取或踩按钮；DeepSeek 规划层仍待接入。主动道具和炸弹均没有模型输出。F8 可随时人工接管。

桥接模组 0.3.3 修复了过门即退出的问题：`Game:IsPaused()` 同样会在换房过渡、Boss 出场等状态下返回 true，旧版因此误切 manual。新版以 REPENTOGON `PauseMenu.GetState()` 区分真正的暂停菜单；过渡中清除动作而保留模式，新房间仍须用新观测生成动作。暂停菜单、F8、断线和游戏退出继续回 manual。帧增加 `mode_reason`/`control_revision`，模式变更原因以 `control_mode_changed` 事件保存，便于核对退出原因。这些诊断字段不加入 Qwen 输入，无需重新训练或重启 L40S 服务。

已有运行中的游戏更新此模组后，需要在调试控制台执行 `luamod isaac_jev_bridge` 热重载，再重新打开 AI/shadow 客户端。

客户端不加载 Torch/Transformers、Qwen 或本地 checkpoint。它从服务器 describe 取得配置并验证规则哈希、特征版本、数值排列，再用桥接短时历史构建 positive 条件输入。一次只保持一个预测在途，不堆积游戏的每一帧。返回必须对应原来的 run/room/frame，超过 200 ms 时丢弃；桥接和 Lua 再独立校验游戏逻辑帧有效期。超时会关闭 RPC 流，旧返回不会作为新状态的预测被读取。

游戏或客户端关闭后，服务器模型继续常驻；服务器容器重启后重新打开隧道脚本即可启动服务。关闭 SSH 窗口只关闭通路，不卸载服务器模型。

## 服务维护

服务器启动入口：

```bash
cd /xufanghui/isaac-jev
.venv/bin/python scripts/start_policy_service.py
tail -f .local/policy-service.log
```

状态文件 `.local/policy-service-ready.json`、PID 文件 `.local/policy-service.pid`。启动器核实项目目录和进程命令，避免重复加载。FLA/Triton 缓存位于项目 `.cache/triton`。

重新测速，无需 checkpoint 或本地 tokenizer：

```powershell
.venv\Scripts\python.exe scripts/benchmark_rpc.py --dataset .local/v004-review/benchmark --samples 25 --mixed-samples 30 --output .local/remote-service-benchmark-v004.json
```

需要停止模型服务以释放 GPU 时，通过仍打开的隧道发送 shutdown：

```powershell
.venv\Scripts\python.exe scripts/benchmark_rpc.py --network-only --shutdown
```

停止后如更换了模型或代码，重新打开隧道脚本会重新加载。当前无需修改输入或重新训练即可获得此轮加速。进一步可评估 causal-conv1d、CUDA Graph/编译，但 Qwen3.5 的混合注意力和动态状态不能直接照搬普通 LLM 的 KV 缓存；需要分别验证延迟及特征一致性。
