# 连接与首次运行

技能安装在 `%USERPROFILE%\.dsh\skills\isaac-play`。入口使用 dsh 当前 Python 即可，运行不依赖 Torch。`connection.json` 指向项目目录、工具端口 9530、游戏桥 9527、SSH 转发的模型端口 19528；可通过环境变量 ISAAC_JEV_PROJECT 覆盖项目路径。

首期步骤：

1. 项目 `Start_Recording.cmd` 启动采集桥。
2. 项目 `Start_Server_Tunnel.cmd` 打开现有 SSH 隧道；在该窗口输入密码，密码不保存在技能中。
3. 使用已有 Sandboxie 实验环境运行游戏，启用 Isaac Jev Bridge 0.4.2。游戏当前已运行时，控制台执行 `luamod isaac_jev_bridge` 重载一次。随技能的 assets/isaac_jev_bridge 是对应模组，项目实验目录已安装；原 Steam 目录保持原状。`doctor` 的 `input_timing` 应为 `start-relative-v1`；旧模组会被启动检查拒绝。
4. 结束旧独立 shadow/AI 客户端，F8 回人工，再取消暂停。
5. 用工具 setup、doctor 检查，然后 start_autoplay。工具会告诉你缺少哪条通路；对连接问题做修复，不用调试命令增加资源或改变存档。

工具服务启动时只读取状态，start_autoplay 才申请自动控制。pause/F8 后整个自动会话停止，用户再次要求继续时显式调用 start_autoplay。

在 dsh 发出游玩请求后回到游戏，取消暂停并保持游戏前台；start_autoplay 默认等待新游戏帧最多 20 秒，避免切换窗口时立即报错。

用 `python <技能目录>/scripts/isaac_tools.py check` 连续读取两次游戏状态（默认间隔 2 秒），确认 `frames_advancing=true`、`alive=true`、`mode=manual` 和 `ready_for_start=true`。检查是只读操作。如果 `ready_for_start=false`，保留它给出的具体状态；暂停/失焦、死亡、已自动运行和断线分别处理，不把所有结果都当成需要按 Esc。

## dsh 终端问题

- 终端报 `dsh-*` 临时目录不存在时，错误发生在 Python CLI 启动之前；重新初始化终端会话或重启 dsh 后再试。不要把临时会话目录写入项目连接配置。
- PowerShell `ConstrainedLanguage` 可能禁止 .NET 编码/方法调用，内联 JSON 也可能丢失引号。直接调用已有 Python CLI，用 `--arg event_cursor 3 --arg timeout_s 25` 等简单参数；复杂数据继续用 `--args-file`。这个入口已在受限语言模式下验证，无需新建 HTTP 调用脚本。
- 游戏帧不推进时，请用户回到实验游戏检查暂停菜单；需要时手动关闭暂停并保持游戏前台。不要在未确认菜单状态时用 ctypes、COM 或其他方式盲发 Esc，可能反而打开暂停或退出菜单。

连续 120 秒没有 agent 工具调用时自动停止并释放控制，避免 dsh 退出后仍保持战斗。正常 wait_for_decision 每次最多 25 秒；网页查询较长时可查询执行状态维持会话。

MCP 可用时，server command 填当前 Python executable，args 填技能脚本绝对路径和 `mcp`。dsh 的具体配置入口按当前安装界面操作；CLI 方式无需 MCP 配置。

可读取项目 `.local/isaac-skill/service.log` 和 `events.jsonl` 诊断实际目标、执行前后状态与中断原因。只关闭工具服务使用脚本 shutdown，它不停止 L40S 模型或采集桥。

更新代码后优先使用 `python <技能目录>/scripts/isaac_tools.py restart`，或项目 `Restart_Skill_Tools.cmd`。该命令先检查日志、事件和 PID 文件写入条件，再关闭旧服务；正在自动游玩时会拒绝重启，先停止会话。只检查启动条件可运行 `preflight`。`setup` 对已在运行的服务仍是只读、幂等检查。

仅主 `service.log` 不可追加而其他运行文件可写时，启动器自动选用同目录 `service-<随机标识>.log`，返回实际路径和 warnings。若整个运行目录对当前进程只读，restart 会在关闭旧服务前失败，需通过有项目写权限的启动环境运行。WinError 5 本身不能证明 ACL 损坏，不应据此删除日志或重置整个目录 ACL。

网络切换可导致 SSH 隧道断开。doctor 会实际探测缓存连接；失败时会显示 127.0.0.1 的策略端口和 `Start_Server_Tunnel.cmd` 提示，失效连接不会继续报 ready。重新建立隧道后再运行 doctor/check，继续用户已授权的测试。
