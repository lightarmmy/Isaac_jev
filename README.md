# Isaac Jev

[English](README.md) | [简体中文](README.zh-CN.md)

A hierarchical agent for **The Binding of Isaac**, driven by native game telemetry. **dsh + the `isaac-play` skill** handles exploration, items, and resource management. Local scripts provide navigation and higher-level facts. A **frozen Qwen3.5-2B backbone with independently trained action heads** handles real-time combat.

Our text construction draws on [TerraBlind](docs/terrablind_review.md). This project trains its own Jev-style policy; it does **not** use TypeSafe's native Jev service.

As of **November 26, 2026**, the configured deployment uses **V009, epoch 1**. **V010** has completed 30 training epochs, with every action head retained, but has not replaced the deployed model. The bridge mod is **0.7.0 / `combat_schema=3`**. Exploration, combat, and pickups across a floor have been demonstrated; completing a full game remains the goal.

## Project layout and requirements

| Component | Location | Runs on |
| --- | --- | --- |
| Game bridge, navigation, text features, policy client, and tool service | `src/isaac_jev/` | Windows game computer |
| Lua mod and installation, connection, and recording scripts | `mods/isaac_jev_bridge/`, root `.cmd` files, `scripts/` | Windows game computer |
| High-level skill, tool entry point, and instructions | `skills/isaac-play/` | Local dsh installation |
| Dataset processing, action-head training, and GPU policy server | `scripts/`, `src/isaac_jev/` | Linux GPU node |
| Trained heads, processed datasets, and original recordings | `artifacts/`, `datasets/`, `recordings/` | Supplied separately; copied where needed |

The local and cloud components come from the same source tree. All **104 files** in the downloaded V010 training-source manifest matched the local snapshot when checked. Older cloud workspaces, including the deployed V009 version, may differ. Use matching source and checkpoint versions on both sides; adapt filesystem paths and CUDA settings to your machines.

You need more than the source code, the game, and REPENTOGON:

- **Windows and Python 3.11**, plus a legitimate game installation and the DLC required by REPENTOGON. This project was tested with **Repentance+**.
- A **Linux NVIDIA GPU node** with working CUDA and SSH access. Our deployment uses an **L40S**. The Windows client needs neither Torch nor local Qwen weights when using the remote server.
- The **`Qwen/Qwen3.5-2B` backbone** and a matching complete action-head package: `head.safetensors`, `policy_config.json`, `normalization.json`, `action_map.json`, `manifest.json`, and any other files listed in that manifest.
- **dsh with a configured high-level LLM** for exploration and resource management. Combat-only testing can use the standalone client.
- **Processed datasets** for retraining, and finalized human recordings if you want to rebuild inputs and annotations.

**Weights and datasets are not included in Git.** Obtain the matching packages from the project author, or collect demonstrations and train your own policy. Virtual environments, caches, local deployment state, recordings, temporary probes, and large embedded-video presentations are also excluded. Presentation sources and speaker scripts are included.

## 1. Windows: install the game bridge and mod

Clone the repository, then run the following commands from its root:

```powershell
git clone https://github.com/lightarmmy/Isaac_jev.git
cd Isaac_jev
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[link]"
```

1. Download the Launcher from the [official REPENTOGON installation page](https://repentogon.com/install.html) or the [official Launcher repository](https://github.com/TeamREPENTOGON/Launcher). Select a clean, supported game executable and complete its compatibility checks and installation. Follow the official supported-version guidance; this project's historical offline patch script is not a general installer.
2. We recommend a project-local game copy. The command below copies your clean game to `.local/isaac-lab/game`; select that copy's `isaac-ng.exe` in the Launcher.
3. Copy `mods/isaac_jev_bridge/` into `mods/isaac_jev_bridge/` under the **game directory actually launched by REPENTOGON**, then enable **Isaac Jev Bridge** in the mod manager. The supplied installer only writes under `.local/isaac-lab/`.
4. Enable **Lua debug / `--luadebug`** in the Launcher; TCP sockets require it. For in-game `jev_*` commands, set `EnableDebugConsole=1` in the active save directory's `options.ini`. After a mod update, restart the game or run `luamod isaac_jev_bridge` in its console.

```powershell
.\scripts\prepare_lab.ps1 -SourceGame 'YOUR STEAM GAME DIRECTORY'
# After REPENTOGON installation, use the actual launch directory if it created a subdirectory
.\scripts\install_bridge.ps1 -LabGame '.local\isaac-lab\game'
.\Setup_Game_Link.cmd
.venv\Scripts\python.exe -X utf8 run.py status
.venv\Scripts\python.exe -X utf8 run.py frame
```

`Setup_Game_Link.cmd` starts the local bridge, not the game. After subsequent computer restarts, use `Start_Game_Link.cmd`. The mod connects automatically to **127.0.0.1:9527**. Check for `connected=true`, `capture_phase=pre_update`, advancing game frames, and `mode=manual`. Keep the game in the foreground and unpaused.

**Copying game files does not isolate saves.** To protect existing progress, use a separate Windows account or a verified sandbox and Steam Cloud isolation setup. [Game connection and save isolation](docs/CONNECT_ISAAC.md) documents our experimental setup; its historical machine paths and versions need adaptation.

## 2. Cluster: deploy the GPU policy server

Allocate a GPU container or node that supports SSH forwarding and can remain running. Execute the commands below **on that allocated GPU node**. Store code, models, and logs on persistent storage: a detached process still ends when its cluster job or container is reclaimed.

Upload matching `src/`, `scripts/`, `configs/`, `run.py`, and `pyproject.toml` to a directory such as `/workspace/isaac-jev`. Preserve relative paths when uploading the complete `artifacts/combat-v009-feedback/` package. Use SFTP or `scp -P SSH_PORT`; the server does not need game files.

```bash
cd /workspace/isaac-jev
python3.11 -m venv .venv
source .venv/bin/activate
# First install a CUDA-enabled Torch build compatible with the node's driver
python -m pip install -e '.[train]'
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Acceleration dependencies tested on L40S; verify compatibility on other hardware
python -m pip install flash-linear-attention==0.4.0 fla-core==0.4.0 einops==0.8.1
```

Our tested environment uses **Torch 2.6.0+cu124**, **Transformers 5.3.x**, and **BF16**. `scripts/setup_l40s.sh` validates an L40S environment and rejects other GPU models. Timing will depend on hardware and environment.

**Download the backbone before starting the service.** The server loads offline from the project's `.cache/huggingface/hub`. Download the exact revision specified by the action-head configuration:

```bash
python - <<'PY'
import json
from huggingface_hub import snapshot_download
cfg = json.load(open('artifacts/combat-v009-feedback/combat-v009-feedback-s01/policy_config.json'))
snapshot_download(cfg['model'], revision=cfg['revision'], cache_dir='.cache/huggingface/hub')
PY
```

For offline nodes, download on another machine and transfer the complete Hugging Face cache. If `.local/offline-hf/` exists, the server prefers that directory; check that it contains the intended revision.

Adapt `configs/remote_policy.json` to your paths:

```json
{
  "checkpoint": "artifacts/combat-v009-feedback/combat-v009-feedback-s01",
  "warmup_dataset": "datasets/combat-v009-feedback",
  "cpu_threads": 1,
  "pad_multiple": 512
}
```

The matching warmup dataset is optional: if absent, the server still performs fixed-length warmup. Start the service and wait for readiness; initial loading and kernel compilation can take time.

```bash
.venv/bin/python scripts/start_policy_service.py
tail -f .local/policy-service.log
# In another SSH terminal; this file is written after warmup
cat .local/policy-service-ready.json
```

The service listens only on **127.0.0.1:19528**. Logs and PID files live under `.local/policy-service*`; closing SSH does not unload the model. The launcher reuses an existing process rather than changing its checkpoint. To switch models, stop autoplay, run `.venv/bin/python scripts/benchmark_rpc.py --network-only --shutdown`, wait for the old process to exit, update the configuration, and start again. Keep source, text rules, numerical feature order, backbone revision, and the complete checkpoint package matched.

## 3. Connect locally and play

Once the server is ready, open a Windows terminal and establish the tunnel. Replace `SSH_PORT`, `USER`, and `HOST`, and keep the terminal open:

```powershell
ssh -N -p SSH_PORT -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:19528:127.0.0.1:19528 USER@HOST
```

`Start_Server_Tunnel.cmd` and the automatic launcher behind in-game `jev_connect` contain the original deployment's address or remote directory. Use the generic tunnel above for a new deployment, or adapt the `.cmd` file and the remote launch path in `src/isaac_jev/server_link.py`. `jev_connect` reuses an existing healthy tunnel. With the automatic path configured and no external tunnel, enter `jev_connect IP:SSH_PORT` in the game console and supply the password in the local dialog; the default SSH username is `root`.

Install the high-level skill and check all connections:

```powershell
.\Install_Dsh_Skill.cmd
.venv\Scripts\python.exe skills\isaac-play\scripts\isaac_tools.py setup
.venv\Scripts\python.exe skills\isaac-play\scripts\isaac_tools.py call doctor
.venv\Scripts\python.exe skills\isaac-play\scripts\isaac_tools.py check
```

The skill installs to `%USERPROFILE%\.dsh\skills\isaac-play`; the tool service uses **127.0.0.1:9530**. Configure your LLM in dsh and ask it to use `isaac-play` to manage resources and progress toward completing the game. Before starting, verify `battle_ai.ready=true`, the intended checkpoint, advancing frames, and a living player. dsh handles exploration and pickups; combat is handed to Jev automatically. Installing the skill does not install dsh or provide LLM access.

For **combat-only testing**, dsh is optional:

```powershell
# Shadow mode predicts while you control the character
.venv\Scripts\python.exe run.py remote-infer --mode shadow
# End the shadow client first, then let the policy control combat for 30 seconds
.venv\Scripts\python.exe run.py remote-infer --mode ai --max-seconds 30
```

Press **F8** to return to manual control; pausing also stops automatic control. The standalone combat client does not explore or collect items. After source updates, reinstall the skill and use `Restart_Skill_Tools.cmd` to restart its backend, after stopping any active session. See [skill usage](docs/DSH_SKILL.md) and [in-game commands](docs/GAME_CONSOLE.md).

## 4. Cluster: retrain the V010 action head

The shortest route uses the **prepared dataset**. In addition to the environment and running Qwen feature service from step 2, upload:

- The complete `datasets/combat-v010-tactical/` directory, including samples, numerical feature names, option texts, quality references, and runtime fixtures. Its train/validation/test counts are **1387 / 3513 / 3079**.
- The V009 initialization package at `artifacts/combat-v009-feedback/combat-v009-feedback-s01/`.
- The legacy encoder configuration at `artifacts/combat-v005/policy_config.json`, supplied separately. The current trainer still reads this path and loads its tokenizer from `--source/.cache/huggingface/hub`. Its model revision must match the running feature service.

```bash
cd /workspace/isaac-jev
source .venv/bin/activate
# Start the V009 server as in step 2 and wait for its feature RPC on port 19528
python -u scripts/train_v007.py \
  --source "$PWD" --dataset datasets/combat-v010-tactical \
  --output artifacts/combat-v010-retrain --groups s01 --epochs 30 \
  --initialize-from artifacts/combat-v009-feedback/combat-v009-feedback-s01 \
  --learning-rate 0.0001 --feature-profile combat-tactical-v10-s01 \
  --transfer-input-contract --run-label combat-v010-tactical
```

The trainer extracts frozen Qwen features through local **port 19528** and trains approximately **one million action-head parameters**. Do not run autoplay while extracting features. The output subdirectory retains the historical name `combat-v007-s01`; `checkpoints/epoch-001` through `epoch-030` contain every head package, while the subdirectory root contains the validation-selected checkpoint. Inspect `status.json` for progress and `result.json`, `history.json`, and `verification.json` for results. The test set is not used to select an epoch.

To deploy this result, set `checkpoint` to `artifacts/combat-v010-retrain/combat-v007-s01` and `warmup_dataset` to `datasets/combat-v010-tactical` in step 2's configuration, then stop and restart the policy service. Reuse the original Qwen backbone; each epoch saves the action head rather than another backbone copy. See the [V010 input and training report](docs/FEEDBACK_V010.md).

**Collecting your own data:** use `Start_Recording.cmd` for human demonstrations and `Stop_Recording.cmd` to finalize them under `recordings/`. Keep both `session.json` and `frames.jsonl.gz`; exclude diagnostic sessions, dropped frames, and AI actions. The current reconstruction chain is `prepare_v007.py` → `prepare_quality_subset.py` → `prepare_feedback_dataset.py` → `prepare_tactical_dataset.py`, using the original V005 split and matching recordings. Each script exposes path arguments through `--help`. Complete damage-free rooms are selected only for training; validation and test remain separate. For new recordings, start with `run.py prepare-dataset` and follow [demonstration collection](docs/COLLECT_DEMONSTRATIONS.md), the [V005 baseline](docs/TRAINING_V005.md), and the [V010 report](docs/FEEDBACK_V010.md). The older `train_l40s.sh` is a baseline entry point, not the current V010 trainer.

## Troubleshooting and references

- **Bridge disconnected:** check the actual launch directory, enabled mod, `--luadebug`, and local port 9527. A sandbox must allow local TCP.
- **Port listening but `battle_ai.ready=false`:** inspect `doctor` and the remote log; close stale tunnels and reconnect. A listening port alone does not prove the RPC is healthy.
- **Checkpoint or rule mismatch:** synchronize matching source and the full checkpoint, rebuild the skill, and restart its backend. Do not bypass hash validation.
- **Missing model weights:** check the configured revision and cache directory. The offline service does not download missing weights automatically.

[Architecture](docs/isaac_hierarchical_pipeline.md) · [Data contract](docs/DATA_CONTRACT.md) · [Remote inference and timing](docs/REMOTE_INFERENCE.md) · [Navigation and terrain](docs/NAVIGATION_TERRAIN.md) · [Midterm report sources](reports/midterm/README.md). Some detailed documents describe historical models and machine-specific paths; use this README and `configs/remote_policy.json` for the current entry points and default version. Most detailed project notes are currently in Chinese.

Project code is licensed under **MIT**. The game, REPENTOGON, Qwen weights, and reference projects retain their respective licenses and are not automatically supplied with this repository.
