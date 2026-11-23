# Isaac Jev

非视觉以撒游戏桥、个人示范采集和冻结 Qwen3.5-2B 的战斗决策头。

高层路线/道具决策计划交给 DeepSeek API；当前实现首先打通房间战斗数据与控制链路。

当前开发版已升级为最近 8 个弹幕 + 0.1/0.3/0.6 秒历史、移动/射击独立分支和结果条件模仿训练。训练使用 positive/negative/unknown，部署固定 positive；旧版训练头不兼容新版特征。实现、标签边界及新输入见 [结果条件训练说明](docs/CONDITIONED_POLICY.md)。

当前状态：**实机采集、人工示范录制、自动输入、两轮 L40S 流程训练和 4060 GPU 推理已验证**。独立实验环境使用 REPENTOGON 1.1.3a，Steam 离线。数据仍为 915 条训练、741 条验证，无测试集。v001 联合动作一致率 4.59%；v002 固定 positive 的全验证集为 7.56%，观察 positive 子集为 7.62%，均未达到可用战斗策略要求。主动正样本仅 1 条，自动主动输出未启用。两版产物已取回并校验。本地 PyTorch 为 2.6.0+cu124；4060 对真实短/中/长输入耗时约 615/820/1096 ms，峰值分配约 4014 MiB，超过当前 200 ms 指令有效期。采集和训练流程可用，实时控制仍需优化。见 [接入指南](docs/CONNECT_ISAAC.md)、[首轮评估](docs/current_model_status.md)、[新版说明](docs/CONDITIONED_POLICY.md)与[批量采集指南](docs/COLLECT_DEMONSTRATIONS.md)。

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

本地部署，先安装与训练兼容的 CUDA 推理环境，再运行：

```powershell
python run.py infer artifacts/combat-v001 --mode shadow
```

## 文档

- [当前接入步骤和原游戏保护](docs/CONNECT_ISAAC.md)
- [原始数据与模型输入契约](docs/DATA_CONTRACT.md)
- [完整层级架构](docs/isaac_hierarchical_pipeline.md)
- [以撒战斗设计](docs/isaac_jev_design.md)
- [TerraBlind 源码核对](docs/terrablind_review.md)

## 测试

```powershell
$env:PYTHONPATH = 'src'
py -3.11 -X utf8 -m unittest discover -s tests -v
```

MIT。游戏和 Qwen 权重分别遵循其自己的许可，不包含在 Git 中。
