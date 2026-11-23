# Isaac Jev

非视觉以撒游戏桥、个人示范采集和冻结 Qwen3.5-2B 的战斗决策头。

高层路线/道具决策计划交给 DeepSeek API；当前实现首先打通房间战斗数据与控制链路。

当前安装状态：**实验副本已安装 REPENTOGON 1.1.3a，并通过官方 Launcher 的 DLL 与游戏版本验证**。原游戏有中文替换文件，保留原安装；实验副本导入官方 Steam depot，再应用官方补丁。用户已选择 Sandboxie 完整隔离，环境正在配置，尚未启动游戏或验证实机桥接，见 [接入指南](docs/CONNECT_ISAAC.md)。

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

```powershell
py -3.11 -X utf8 run.py inspect recordings
py -3.11 -X utf8 run.py prepare-dataset recordings --output datasets/combat-v1
```

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
