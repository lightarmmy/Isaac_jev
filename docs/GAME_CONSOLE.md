# 游戏内连接、录制与演示 HUD

模组 0.5.0 提供以下命令。安装更新后，在实验游戏控制台执行一次 `luamod isaac_jev_bridge`。

1. 重启电脑后运行项目的 `Start_Game_Link.cmd`。新启动仅连接游戏、不录制；已有桥接不改变录制设置。首次安装依赖使用 `Setup_Game_Link.cmd`，SSH 依赖保存在项目 `.local/serverdeps`。
2. 打开游戏控制台，输入 `jev_connect 10.28.0.22:31203`，在本机弹窗输入密码。待 HUD 显示 `SERVER: ready` 后，dsh 可以使用原自动游玩流程。
3. 需要采集时输入 `jev_record on`；结束输入 `jev_record off`，等 `OFF; saved` 确认。连接服务器与录制相互独立，人工示范不需要连接服务器。

| 命令 | 用途 |
|---|---|
| `jev_connect <IP[:SSH端口]> [密码] [用户名]` | 自动 SSH 认证、启动已有 L40S 服务并转发本机 19528；默认端口 31203、用户名 root |
| `jev_disconnect` | 回人工并关闭本桥接管理的隧道，保留服务器模型进程 |
| `jev_status` | 查看桥接管理的服务器连接状态 |
| `jev_record on / off / toggle` | 开启、封存、切换录制，不改变控制模式 |
| `jev_hud on / off` | 显示/隐藏扩展 HUD |

密码含空格时用双引号包住。直接在命令里写密码可能被游戏自己的控制台和 `cmd_history.txt` 保存；推荐省略密码使用弹窗。认证请求不进入项目录像、桥接状态或日志，密码不保存到配置；SSH 指纹沿用 `.local/isaac-lab/ssh-known-hosts`，首次 accept-new，指纹变化拒绝。

本机已有正常 SSH 隧道时会复用，不更改它连接的服务器。若要换 IP，先关闭原隧道。网络切换导致断线后重新运行 `jev_connect`。Lua 模组不能直接启动沙箱外的 Python，因此本机桥接仍需先启动一次。

HUD 显示连接/录制状态、dsh 当前工具（短调用留作 Last tool）、简短思考状态、导航目标/阶段或 Jev 战斗。当前游戏字体使用英文。dsh 在选择目标或查百科前调用：

```text
python <技能目录>/scripts/isaac_tools.py call report_agent_status --arg state thinking --arg summary "Choosing next room"
```

该摘要由 dsh 显式报告，不读取模型内部推理；60 秒过期，工具服务失联显示 offline。工具调用状态由工具服务自动采集，无需逐个上报。HUD 更新与控制指令分离，显示操作不申请控制租约或改变战斗动作。
