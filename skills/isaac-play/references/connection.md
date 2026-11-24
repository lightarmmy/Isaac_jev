# 连接与首次运行

技能安装在 `%USERPROFILE%\.dsh\skills\isaac-play`。入口使用 dsh 当前 Python 即可，运行不依赖 Torch。`connection.json` 指向项目目录、工具端口 9530、游戏桥 9527、SSH 转发的模型端口 19528；可通过环境变量 ISAAC_JEV_PROJECT 覆盖项目路径。

首期步骤：

1. 项目 `Start_Recording.cmd` 启动采集桥。
2. 项目 `Start_Server_Tunnel.cmd` 打开现有 SSH 隧道；在该窗口输入密码，密码不保存在技能中。
3. 使用已有 Sandboxie 实验环境运行游戏，启用 Isaac Jev Bridge 0.4.1。游戏当前已运行时，控制台执行 `luamod isaac_jev_bridge` 重载一次。随技能的 assets/isaac_jev_bridge 是对应模组，项目实验目录已安装；原 Steam 目录保持原状。
4. 结束旧独立 shadow/AI 客户端，F8 回人工，再取消暂停。
5. 用工具 setup、doctor 检查，然后 start_autoplay。工具会告诉你缺少哪条通路；对连接问题做修复，不用调试命令增加资源或改变存档。

工具服务启动时只读取状态，start_autoplay 才申请自动控制。pause/F8 后整个自动会话停止，用户再次要求继续时显式调用 start_autoplay。

在 dsh 发出游玩请求后回到游戏，取消暂停并保持游戏前台；start_autoplay 默认等待新游戏帧最多 20 秒，避免切换窗口时立即报错。

连续 120 秒没有 agent 工具调用时自动停止并释放控制，避免 dsh 退出后仍保持战斗。正常 wait_for_decision 每次最多 25 秒；网页查询较长时可查询执行状态维持会话。

MCP 可用时，server command 填当前 Python executable，args 填技能脚本绝对路径和 `mcp`。dsh 的具体配置入口按当前安装界面操作；CLI 方式无需 MCP 配置。

可读取项目 `.local/isaac-skill/service.log` 和 `events.jsonl` 诊断实际目标、执行前后状态与中断原因。只关闭工具服务使用脚本 shutdown，它不停止 L40S 模型或采集桥。
