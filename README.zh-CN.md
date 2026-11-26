# Isaac Jev

[English](README.md) | [简体中文](README.zh-CN.md)

通过游戏原生数据驱动《以撒的结合》的分层智能体：**dsh + isaac-play skill** 负责探索、道具与资源运营，本地脚本负责导航及高阶事实计算，**冻结 Qwen3.5-2B + 自训练决策头** 负责实时战斗。文本组织参考了 [TerraBlind](docs/terrablind_review.md)，没有直接使用 TypeSafe 的原生 Jev。

截至 2026-11-26，默认部署为 **V009 第 1 轮**；最新 **V010** 已训练 30 轮，所有轮次的决策头已下载，尚未替换默认服务。模组为 **0.7.0 / combat_schema=3**。已验证一层探索、战斗、拾取等流程，完整游戏通关仍是目标。

## 代码与复刻所需材料

| 内容 | 位置 | 运行位置 |
| --- | --- | --- |
| 游戏桥、导航、文本生成、策略客户端与工具服务 | `src/isaac_jev/` | Windows 游戏电脑 |
| Lua 模组、连接/录制/安装入口 | `mods/isaac_jev_bridge/`、根目录 `.cmd`、`scripts/` | Windows 游戏电脑 |
| 高层技能、工具入口与说明 | `skills/isaac-play/` | 本地 dsh |
| 数据处理、决策头训练、GPU 策略服务 | `scripts/`、`src/isaac_jev/` | Linux GPU 节点 |
| 决策头与配套数据 | `artifacts/`、`datasets/`、`recordings/` | 本地留档，训练/推理时上传所需部分 |

上述项目代码都在本地，云端使用上传的同一套源码。已下载的 V010 训练源码清单中 **104 个文件与当前本地哈希一致**；常驻服务仍是 V009，不能据此认为所有历史云目录都与本地最新版逐字一致。两端应使用同一版本源码，路径、CUDA 环境和部署配置则按机器调整。

**仅有代码、游戏和 REPENTOGON 还不足以自动游玩。** 需要准备：

- Windows、Python **3.11**，以及支持 REPENTOGON 的正版游戏/DLC；本项目实测环境使用 **Repentance+**。
- Linux NVIDIA GPU 节点、可用 CUDA 环境和 SSH 访问；本项目使用 **L40S**。只连接云端时，本机不需要 Torch 或 Qwen 权重。
- `Qwen/Qwen3.5-2B` 主干权重，以及匹配的完整决策头目录：`head.safetensors`、`policy_config.json`、`normalization.json`、`action_map.json`、`manifest.json`，另保留清单列出的其他文件。
- 完整资源运营还需要安装 **dsh** 并配置高层 LLM；只测试战斗可以直接使用项目客户端。
- 重新训练还需要准备好的数据集；重建标注和输入时需要已封存的原始人工录像。

**Git 默认不包含** `.venv/`、`.local/`、`artifacts/`、`datasets/`、`recordings/` 及模型权重。复刻者需向代码提供者另外取得匹配的模型/数据包，或自行采集和训练。临时探针、模型缓存、内嵌视频的大型演示 HTML 和原视频也不上传；仓库保留报告源码与讲稿。

## 1. Windows：游戏、REPENTOGON 与模组

以下命令均在项目根目录执行。先安装 Python 3.11，然后建立本地环境：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[link]"
```

1. 从 [REPENTOGON 官方安装页](https://repentogon.com/install.html) 获取 Launcher，也可访问 [官方 Launcher 仓库](https://github.com/TeamREPENTOGON/Launcher)。选择官方支持的干净游戏 EXE，按 Launcher 完成兼容性检查和安装；具体支持版本以官方说明为准。项目的旧离线补丁脚本不是通用安装器。
2. 推荐在项目实验副本中安装。可运行下方命令，把自己的干净游戏复制到 `.local/isaac-lab/game`，再让 Launcher 选择该目录的 `isaac-ng.exe`。
3. 完成 REPENTOGON 安装后，将 `mods/isaac_jev_bridge/` 复制到 **Launcher 实际启动的游戏目录** 下的 `mods/isaac_jev_bridge/`，在模组管理中启用 **Isaac Jev Bridge**。项目安装脚本只允许写入 `.local/isaac-lab/`。
4. 在 Launcher 中开启 **Lua debug / `--luadebug`** 启动游戏，否则无法使用 TCP socket。需要游戏内 `jev_*` 命令时，在实际使用的存档目录 `options.ini` 设置 `EnableDebugConsole=1`，进入游戏后按控制台键打开；更新模组后重启游戏或执行 `luamod isaac_jev_bridge`。

```powershell
.\scripts\prepare_lab.ps1 -SourceGame '你的 Steam 游戏目录'
# 安装 REPENTOGON 后执行；若 Launcher 建立了新的子目录，传入实际目录
.\scripts\install_bridge.ps1 -LabGame '.local\isaac-lab\game'
.\Setup_Game_Link.cmd
.venv\Scripts\python.exe -X utf8 run.py status
.venv\Scripts\python.exe -X utf8 run.py frame
```

`Setup_Game_Link.cmd` 启动本地桥，不启动游戏；以后每次重启电脑运行 `Start_Game_Link.cmd` 即可。模组自动连接 **127.0.0.1:9527**。验收时应看到 `connected=true`、`capture_phase=pre_update`、游戏帧持续推进及 `mode=manual`。保持游戏前台且未暂停。

游戏文件副本**不会自动隔离存档**。需要保护原进度时，采用独立 Windows 用户或经过验证的沙箱/云同步隔离；本机实验方案见 [接入与存档隔离](docs/CONNECT_ISAAC.md)，其中历史地址和版本不能直接照搬。

## 2. 集群：部署 GPU 策略 server

在集群平台申请可长期运行且允许 SSH 转发的 GPU 容器/节点，下面命令在**分配的 GPU 节点**执行。使用持久存储保存代码、模型与日志；脱离 SSH 的进程仍会在作业/容器被回收时停止。

将同版本 `src/`、`scripts/`、`configs/`、`run.py`、`pyproject.toml` 上传至自己的项目目录，例如 `/workspace/isaac-jev`，并按原相对路径上传完整的 `artifacts/combat-v009-feedback/`。可使用 SFTP 或 `scp -P SSH_PORT`。推理不需要上传游戏文件。

```bash
cd /workspace/isaac-jev
python3.11 -m venv .venv
source .venv/bin/activate
# 先按节点驱动安装合适的 CUDA 版 Torch，再安装项目依赖
python -m pip install -e '.[train]'
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# L40S 实测加速依赖；其他 GPU/CUDA 组合需验证兼容性
python -m pip install flash-linear-attention==0.4.0 fla-core==0.4.0 einops==0.8.1
```

本项目实测环境为 Torch **2.6.0+cu124**、Transformers **5.3.x**、BF16。`scripts/setup_l40s.sh` 可验证 L40S 环境，但它会拒绝其他型号 GPU；不能保证其他硬件具有相同时延。

**先下载主干，再启动服务。** 服务默认离线读取项目 `.cache/huggingface/hub`；下载 revision 必须与决策头配置一致：

```bash
python - <<'PY'
import json
from huggingface_hub import snapshot_download
cfg = json.load(open('artifacts/combat-v009-feedback/combat-v009-feedback-s01/policy_config.json'))
snapshot_download(cfg['model'], revision=cfg['revision'], cache_dir='.cache/huggingface/hub')
PY
```

无外网节点需在联网机器预下载并上传完整 Hugging Face 缓存。若存在 `.local/offline-hf/`，服务会优先读取那里，注意避免旧缓存覆盖预期位置。

把 `configs/remote_policy.json` 改为自己的路径，例如：

```json
{
  "checkpoint": "artifacts/combat-v009-feedback/combat-v009-feedback-s01",
  "warmup_dataset": "datasets/combat-v009-feedback",
  "cpu_threads": 1,
  "pad_multiple": 512
}
```

`warmup_dataset` 可上传匹配数据用于额外预热；文件不存在时仍进行固定长度预热。启动服务后等待 ready，首次加载和编译可能较久：

```bash
.venv/bin/python scripts/start_policy_service.py
tail -f .local/policy-service.log
# 另一个 SSH 终端检查；服务预热完成后生成此文件
cat .local/policy-service-ready.json
```

服务只监听 **127.0.0.1:19528**，日志/PID 在 `.local/policy-service*`，不会随 SSH 窗口关闭而卸载。启动器发现已有服务时不会切换权重；换模型需先结束自动游玩，执行 `.venv/bin/python scripts/benchmark_rpc.py --network-only --shutdown` 停止旧服务，等进程退出后更新配置并启动。源码、文本规则、数值排列、Qwen revision 与 checkpoint 必须匹配，不能只替换 `head.safetensors`。

## 3. 本地连接与自动游玩

服务准备好后，在 Windows 新终端建立 SSH 转发，替换 `SSH_PORT`、`USER`、`HOST`，保持窗口开启：

```powershell
ssh -N -p SSH_PORT -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:19528:127.0.0.1:19528 USER@HOST
```

`Start_Server_Tunnel.cmd` 和游戏内 `jev_connect` 的自动启动逻辑含原服务器的地址/目录。新部署优先使用上面的通用隧道；若要沿用便捷入口，修改 `.cmd` 及 `src/isaac_jev/server_link.py` 中的远端启动路径。已有正常隧道时，游戏内 `jev_connect` 会复用它；无外部隧道且路径已适配时，可在游戏控制台输入 `jev_connect IP:SSH_PORT`，在本机弹窗输入密码（默认用户名 root）。

安装高层技能并检查通路：

```powershell
.\Install_Dsh_Skill.cmd
.venv\Scripts\python.exe skills\isaac-play\scripts\isaac_tools.py setup
.venv\Scripts\python.exe skills\isaac-play\scripts\isaac_tools.py call doctor
.venv\Scripts\python.exe skills\isaac-play\scripts\isaac_tools.py check
```

技能安装到 `%USERPROFILE%\.dsh\skills\isaac-play`，工具服务使用 **127.0.0.1:9530**。在 dsh 配置自己的 LLM，再让它使用 `isaac-play`，尽可能利用资源向通关推进。确认 `battle_ai.ready=true`、checkpoint 正确、游戏帧推进且角色存活后开始；dsh 负责走门/拾取/运营，战斗自动交给 Jev。技能安装不包含 dsh 应用或 LLM 服务。

只测试战斗时，无需 dsh：

```powershell
# 影子模式仅预测，由人操控
.venv\Scripts\python.exe run.py remote-infer --mode shadow
# 结束影子客户端后，接管战斗 30 秒
.venv\Scripts\python.exe run.py remote-infer --mode ai --max-seconds 30
```

按 **F8** 返回人工；暂停也会停止自动控制。独立战斗客户端不负责探索和拾取。代码更新后重新安装 skill，并用 `Restart_Skill_Tools.cmd` 重启工具后台；正在游玩时先停止会话。更多操作见 [技能说明](docs/DSH_SKILL.md)与[游戏内命令](docs/GAME_CONSOLE.md)。

## 4. 集群：重新训练当前 V010 决策头

**使用已处理的数据最直接。** 除第 2 节环境与正在运行的 Qwen 特征服务外，还需上传：

- `datasets/combat-v010-tactical/`：包含样本、数值名称、候选文本、质量参考与运行快照等完整文件；当前 train/validation/test 为 **1387 / 3513 / 3079**。
- V009 初始化头：`artifacts/combat-v009-feedback/combat-v009-feedback-s01/`。
- 旧版基础编码器配置：`artifacts/combat-v005/policy_config.json`，需另外取得。当前训练器仍从该路径读取主干配置，并从 `--source/.cache/huggingface/hub` 读取 tokenizer；revision 必须与正在运行的服务一致。

```bash
cd /workspace/isaac-jev
source .venv/bin/activate
# 先按第 2 节启动 V009 server，等待 19528 的特征服务 ready
python -u scripts/train_v007.py \
  --source "$PWD" --dataset datasets/combat-v010-tactical \
  --output artifacts/combat-v010-retrain --groups s01 --epochs 30 \
  --initialize-from artifacts/combat-v009-feedback/combat-v009-feedback-s01 \
  --learning-rate 0.0001 --feature-profile combat-tactical-v10-s01 \
  --transfer-input-contract --run-label combat-v010-tactical
```

训练器通过本机 **19528** RPC 提取冻结 Qwen 特征，只训练约 **100 万参数**的决策头；期间不要同时自动游玩。输出子目录仍命名为 `combat-v007-s01`（历史脚本命名），其 `checkpoints/epoch-001` 至 `epoch-030` 保留每轮完整头包，子目录根部保存验证集选出的头。训练进展见 `status.json`，结果见 `result.json`、`history.json`、`verification.json`；测试集不参与选轮。

部署训练结果时，将第 2 节配置的 `checkpoint` 改为 `artifacts/combat-v010-retrain/combat-v007-s01`，`warmup_dataset` 改为 `datasets/combat-v010-tactical`，停止旧策略服务后重新启动。只需部署头包并复用原 Qwen 主干，无需每轮保存一份主干。详见 [V010 输入与训练报告](docs/FEEDBACK_V010.md)。

**自行采集/处理数据：** `Start_Recording.cmd` 开始人工示范，`Stop_Recording.cmd` 正常封存到 `recordings/`；完整保留 `session.json` 与 `frames.jsonl.gz`，排除诊断、丢帧和 AI 操作。当前重建链为 `prepare_v007.py` → `prepare_quality_subset.py` → `prepare_feedback_dataset.py` → `prepare_tactical_dataset.py`，需要原 V005 数据划分和相应录像；用各脚本 `--help` 查看路径参数。无伤完整房间只筛训练集，验证/测试保持独立。新采集从 `run.py prepare-dataset` 起步，流程见 [采集说明](docs/COLLECT_DEMONSTRATIONS.md)、[V005 数据基线](docs/TRAINING_V005.md)与[V010 报告](docs/FEEDBACK_V010.md)；旧 `train_l40s.sh` 是基线入口，不等于当前 V010 训练。

## 常见问题与进一步阅读

- **桥接未连接：** 确认实际游戏目录已安装/启用模组、开启 `--luadebug`，本地 9527 服务运行；沙箱需允许本地 TCP。
- **端口监听但 `battle_ai.ready=false`：** 用 `doctor` 检查实际 RPC；关闭失效隧道重连，并检查远端服务日志，不能只看端口存在。
- **模型/规则不匹配：** 同步匹配源码和完整 checkpoint，再重建技能、重启工具服务；不要手动改哈希绕过检查。
- **模型下载失败：** 核对 revision 和上述缓存位置；启动器默认离线，不会自动补下载。

[完整框架](docs/isaac_hierarchical_pipeline.md) · [输入契约](docs/DATA_CONTRACT.md) · [远程推理与时延细节](docs/REMOTE_INFERENCE.md) · [导航与地形](docs/NAVIGATION_TERRAIN.md) · [中期报告](reports/midterm/README.md)。部分旧文档包含历史模型与本机路径；当前入口和默认版本以本 README、`configs/remote_policy.json` 为准。

代码采用 MIT 许可。游戏、REPENTOGON、Qwen 权重及参考项目分别遵循各自许可，不随源码自动提供。
