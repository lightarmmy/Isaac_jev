# 本地接入步骤：保护原游戏，先完成状态与输入桥

用户原游戏：`D:\SteamLibrary\steamapps\common\The Binding of Isaac Rebirth`。

实验游戏副本：`D:\my_project_for_D\qwen_jev_for_cardgame\.local\isaac-lab\game`。

官方 Launcher 已下载并验证有效签名：`.local\REPENTOGONLauncher\REPENTOGONLauncher.exe`，版本 v0.4.9-beta。原 EXE v1.9.7.19.J511 未通过兼容性校验；用户原游戏使用中文替换文件。实验副本导入干净 depot 后通过兼容性检查，随后因 GitHub API 限流、Steam Workshop 下载不可用而安装失败。

已完成离线修复：官方下载 REPENTOGON 1.1.3a ZIP，核对 SHA-256，依据官方 Launcher 的复制列表和 BSDIFF40 补丁清单构建独立 Repentogon 子目录。官方 Launcher 在 2026-11-23 20:01 验证通过：三个 DLL 均为 1.1.3a，补丁后的游戏为 v1.9.7.12.J273。离线安装脚本不是官方工具，最终验证由官方 Launcher 完成。

## 当前约束

- 后续只在项目实验副本安装 mod 或导入文件。
- 不继续验证或修改原 Steam 安装，不绕过官方 Launcher 的校验。
- 已备份当前 Repentance+ 存档目录到 `.local\backups\`。
- 文件副本不等于存档隔离。官方源码表明，默认使用当前 Windows 账户的 `Documents\My Games\Binding of Isaac Repentance+`；修改 `savedatapath.txt` 无效。
- 官方 [SaveSyncing.cpp](https://github.com/TeamREPENTOGON/REPENTOGON/blob/1.1.3a/repentogon/SaveSyncing/SaveSyncing.cpp) 明确说明：REPENTOGON 进度默认同步原版，而且 GameState 继续游戏文件仍共享。禁用进度同步本身不足以隔离整局存档。
- 当前实验副本尚未启动。用户已选择 **Sandboxie 独立沙箱 + 实验 Steam 云同步隔离**；Sandboxie Plus 1.19.1 官方安装包签名有效，安装向导已打开，环境尚未验证。不能仅设置 USERPROFILE 或仅沙箱化游戏就宣称完成隔离。

## 第一步：获取官方干净文件，保留中文本体

1. Win+R → `steam://nav/console`。
2. Steam 控制台输入：

```text
download_depot 250900 3353471 2583137481533219988
```

这是从本机 Steam manifest 记录的 Repentance+ depot 下载官方文件到 Steam 的 content 目录，不验证或替换现有安装。Steam 账号需有对应游戏/DLC 权限。若该 manifest 不再可用，根据 Steam 返回值调整，不绕过授权。

3. 等待 `Depot download complete`，记录控制台给出的目录。
4. 将官方文件导入实验副本：

```powershell
.\scripts\import_clean_depot.ps1 -DepotDirectory '控制台显示的 depot_3353471 目录'
```

脚本只写实验副本，并要求源目录确实包含 isaac-ng.exe。

## 第二步：安装忏悔龙到实验副本

1. 关闭之前指向原游戏的 Launcher。
2. 打开官方 Launcher，选择实验副本的 `isaac-ng.exe`，不要选原 Steam 目录。
3. 让官方安装器完成兼容性检查和安装。
4. 如果仍不兼容，记录错误并检查官方支持版本；不要跳过检查。
5. 新版 Launcher 接受 `--isaac`、`--luadebug` 和 `--configuration-file` 参数，官方安装位置和具体版本由安装器管理。先完成安装和存档选择，再启动。

本机已完成此步的离线修复。安装报告在 `.local\isaac-lab\offline-install.json`，保存每个补丁及输入/输出文件的 SHA-256。离线脚本 `scripts/install_repentogon_offline.py` 只接受项目内固定实验目录和指定官方 release 的校验值，拒绝覆盖已存在的 Repentogon 目录。它依赖项目本地 `bsdiff4==1.2.6` 和已下载的官方资源，不应直接用于其他游戏版本。

Launcher 的实验配置为 `.local\isaac-lab\launcher.ini`，不会沿用原用户的 Launcher 配置。`--skip-repentogon-update` 仅避免再次触发下载失败，未跳过 DLL 或游戏版本验证。

官方说明：[安装文档](https://repentogon.com/install.html)。新版可能使用固定兼容版本，不能套用旧 DLL 复制教程。

## 启动前：配置独立沙箱

1. 安装官方 Sandboxie Plus，创建专用标准沙箱 `IsaacJevLab`，保留文件/注册表写入虚拟化，关闭自动清空与自动恢复文件。环境的具体设置须在安装完成后核对。
2. 不向沙箱开放原游戏、原 Documents 存档目录或原 Steam userdata 的直接写权限。设置游戏兼容性时，不应直接开放与宿主 Steam 的 IPC 通路。
3. 先正常退出宿主 Steam，再把 Steam 客户端和实验 REPENTOGON Launcher 都放入同一个实验沙箱。仅沙箱化游戏、连接宿主 Steam 不能证明云同步隔离。
4. 在实验 Steam 中关闭该游戏云同步，验证设置落在沙箱内；首轮验证采用 Steam 离线模式。网络上的 Steam Cloud 属于外部状态，文件虚拟化不会自动隔离云端。
5. 先运行存档写入探针，验证宿主文件没有变化、写入确实落在沙箱内，再启动实验游戏。游戏前后核对原存档目录及 Steam userdata 的文件哈希。
6. Python bridge 和模型推理运行在宿主，通过 `127.0.0.1:9527` 接收实验 mod 数据。沙箱应允许此本地 TCP 通道。

此节是待执行流程，目前没有声称沙箱或实机通路已验证。

## 第三步：复制采集 mod

```powershell
.\scripts\install_bridge.ps1
```

当前已复制到实验副本的 mods/isaac_jev_bridge。安装器更新后可以再执行一次。此脚本拒绝原 Steam 目录等实验目录之外的目标。

在实验 Launcher 的 mod 管理中启用 Isaac Jev Bridge。先只启用需要的 mod，保留原游戏的订阅与设置。

## 第四步：先启动 Python，再进入游戏

项目目录终端：

```powershell
py -3.11 -X utf8 run.py bridge --record recordings
```

无需 PyTorch 或模型权重。等待 `Bridge listening on 127.0.0.1:9527`。

实验游戏经 Launcher 启动时启用 Lua debug。这个开关开放 Lua 的系统接口，先只给实验副本使用，不改原游戏的 Steam 启动选项。

进入普通单人游戏后，画面会显示 `Isaac Jev: manual | CONNECTED`，终端显示 `Isaac mod connected`。

## 第五步：验证数据，不让 AI 接管

另一个项目目录终端：

```powershell
py -3.11 -X utf8 run.py status
py -3.11 -X utf8 run.py frame
```

确认 connected=true、game_frame 增长、capture_phase=pre_update、mode=manual。移动、射击和使用主动道具，检查 human_input 与角色状态是否对应。

F8 始终返回人工控制。关闭 bridge 时自动指令失效，不留下无限持续按键。

## 第六步：短输入探针

只有在清空、无敌人/子弹/激光/炸弹且位置远离尖刺/火焰等危险的房间中使用：

```powershell
py -3.11 -X utf8 run.py probe
```

探针提交约 2 个逻辑帧的向右输入，不射击、不使用主动道具，随后恢复 manual。程序检查基本清房条件，剩余地面危险须在实机检查。

## 第七步：个人示范采集与训练

确认通路后，保持 manual 正常游玩。录制在 recordings/时间戳会话/frames.jsonl.gz，停止桥时写入丢帧和完整性信息。

```powershell
py -3.11 -X utf8 run.py inspect recordings
py -3.11 -X utf8 run.py prepare-dataset recordings --output datasets/combat-v1
```

采集多种房间、多次开局、受伤恢复和主动道具使用。训练先用明确覆盖的普通泪弹和主动道具；数据可以保留更多内容，推理能力不能据此直接扩大。

L40S 上安装训练依赖后执行 README 中的 train 命令。当前尚无个人示范或训练好的头，不能进行有效训练或宣称模型已学会战斗。
