# Isaac Jev

非视觉以撒游戏桥、个人示范采集和冻结 Qwen3.5-2B 的战斗决策头。

高层路线/道具决策由 dsh 的 `isaac-play` 技能通过本地游戏工具执行，战斗交给 L40S 上的 Qwen-jev。首期支持连续走房间、拾取普通掉落、拿宝箱房道具和查看商店；技能安装与使用见 [DSH_SKILL.md](docs/DSH_SKILL.md)。

当前训练版使用最近 8 个弹幕 + 0.1/0.3/0.6 秒历史、文本/数值分别投影融合，以及独立移动/射击输出。本轮移除主动道具上下文、使用房间及主动输出参数。训练使用 positive/negative/unknown，部署固定 positive；不同头版本的权重不兼容。最新实现和结果见 [v003 训练与测速报告](docs/TRAINING_V003.md)，输入/标签边界见 [结果条件说明](docs/CONDITIONED_POLICY.md)。

当前状态：**实机采集、自动输入、L40S 示范训练及两端 GPU/网络测速已完成**。v003 使用 8119/732/300 条训练/验证/测试样本，来自 3/1/1 个独立种子；第 13 轮提前停止，导出第 5 轮。固定 positive 下验证动作一致率 76.37%，测试 76.00%，接近继续上一动作基线，动作转换预测仍很弱，尚未验证实机清房。4060 短/中/长输入约 343/528/900 ms，L40S 约 805/818/878 ms，服务器总响应约 812–816 ms，均超过 200 ms 指令预算。网络 RTT 为数毫秒，瓶颈在当前推理实现。产物已取回并校验；采集仍使用 REPENTOGON 1.1.3a 的独立离线实验环境。见 [训练与测速报告](docs/TRAINING_V003.md)、[接入指南](docs/CONNECT_ISAAC.md)与[批量采集指南](docs/COLLECT_DEMONSTRATIONS.md)。

## 已实现

- 自写 Lua mod：REPENTOGON `MC_PRE_UPDATE` 更新前采集、原始实体/地形/属性/道具/人工输入、TCP 双向通信。
- 本地 Python TCP hub：状态检查、gzip JSONL 录制、丢帧计数、命令回执。
- 默认手动控制，影子模式和 AI 模式分离，F8 返回人工控制，命令有房间与时限。
- 45 类移动/射击动作，独立主动道具二分类触发；主动使用可以与移动射击同时发生。
- 原始录制到示范数据的转换、按整局划分、冻结 Qwen 文本主干特征、MLP 训练与版本包导出。
- 本地影子推理/控制入口与人工接管。
- 只写项目实验副本的 mod 安装脚本。

实际游戏连接、训练和 GPU 推理需要在对应环境验证；mock 客户端不会被当作实机结果。

## 无需模型依赖的桥接命令

在项目目录打开 PowerShell：

```powershell
py -3.11 -X utf8 run.py doctor
py -3.11 -X utf8 run.py bridge --record recordings
```

另一个终端：

```powershell
py -3.11 -X utf8 run.py status
py -3.11 -X utf8 run.py frame
py -3.11 -X utf8 run.py mode manual
```

mock 仅用于离线链路测试：

```powershell
py -3.11 -X utf8 run.py mock-client --frames 90
```

## 采集与 L40S 训练

批量采集双击项目根目录的 `Start_Recording.cmd` 即可，之后新局和重开实验游戏自动录制、封存、重连；新版模组的 REC 标记显示写入状态。结束采集或关机前双击 `Stop_Recording.cmd`。重启电脑后需要再次启动采集程序。以下命令仍可用于手动操作。

```powershell
# Start a clean personal demonstration session; finish the preceding tests as diagnostic.
py -3.11 -X utf8 run.py record rotate --purpose demonstration --completed-purpose diagnostic
# Play manually, then close the recording normally.
py -3.11 -X utf8 run.py record stop
py -3.11 -X utf8 run.py inspect recordings
py -3.11 -X utf8 run.py prepare-dataset recordings --output datasets/combat-v1
```

转换排除 diagnostic 会话，观测帧 N 对齐人工操作帧 N+1；主动正标签同时要求按下边沿和真实使用事件。完整录制后再准备数据集，不能把空房按键测试当作战斗示范。

在集群 Linux / L40S 环境中：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
# Install a CUDA-compatible Torch build for the cluster before the training extras when needed.
pip install -e '.[train]'
python run.py train datasets/combat-v1 --output artifacts/combat-v001 --model Qwen/Qwen3.5-2B
```

至少需要独立训练与验证整局。示范数据中的模型自身输出不作为人工正确标签。主动道具没有正样本时该输出默认不启用。

当前使用 L40S 常驻推理，本地只连接游戏。先双击 `Start_Server_Tunnel.cmd`，在 SSH 窗口输入服务器密码并保持窗口打开，再双击 `Start_Remote_Shadow.cmd`。shadow 仅评估战斗动作，角色仍由人控制；无需本地 Qwen、checkpoint 或 Torch。详细部署、接管命令和测速见 [L40S 远程推理](docs/REMOTE_INFERENCE.md)。

保留本地推理入口供开发使用，需要与训练兼容的 CUDA 环境：

```powershell
python run.py infer artifacts/combat-v001 --mode shadow
```

## 文档

- [当前接入步骤和原游戏保护](docs/CONNECT_ISAAC.md)
- [原始数据与模型输入契约](docs/DATA_CONTRACT.md)
- [v004 训练、动作复制修正与验证](docs/TRAINING_V004.md)
- [dsh 的 isaac-play 技能与工具使用](docs/DSH_SKILL.md)
- [完整层级架构](docs/isaac_hierarchical_pipeline.md)
- [以撒战斗设计](docs/isaac_jev_design.md)
- [TerraBlind 源码核对](docs/terrablind_review.md)

## 测试

```powershell
$env:PYTHONPATH = 'src'
py -3.11 -X utf8 -m unittest discover -s tests -v
```

MIT。游戏和 Qwen 权重分别遵循其自己的许可，不包含在 Git 中。
