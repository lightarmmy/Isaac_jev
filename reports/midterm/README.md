# Isaac Jev 中期报告 / Midterm report

GitHub 仓库保留本目录的报告源码与讲稿；原视频及两个内嵌视频的 HTML 超过 GitHub 普通 Git 单文件限制，保留为本地交付文件。重新构建还需要对应的实验结果/权重包及视频，见下方说明。

The repository includes report sources and speaker scripts. The original video and the two standalone decks with embedded video exceed GitHub's standard Git file limit and remain local deliverables. Rebuilding requires the corresponding experiment artifacts and video, as described below.

主稿 8 页，建议总时长约 5 分钟；另有 2 页附录，提供决策头参数明细与完整实验表。中英文两版采用相同的版式、图表、页序和数据，HTML 内嵌样式与脚本，无需联网。

- `midterm.zh-CN.html`：中文。
- `midterm.en.html`：English.

用 Chrome 或 Edge 打开任一 HTML。方向键翻页，`F` 全屏，`N` 显示讲稿，`O` 总览，`A` 显示附录，`H` 隐藏工具栏。工具栏可切换语言并保持当前页。打印 / PDF 会输出全部 10 页。

第 5 页已内嵌指定的 `202611261335.mp4`，1920×1080、约 2 分 33 秒，保留完整原画质和音频。中文或英文 HTML 都可单独分享，无需另发 MP4 或 assets 目录；接收者用 Chrome / Edge 打开，翻到 Demo 页点击播放即可。刷新页面会重新加载内嵌视频。每份 HTML 约 247 MB。

以后更换视频，点击视频区域的“更换视频”，选择新文件，再点击“导出含视频 HTML”；分享新下载的 HTML 即可。视频及导出均在本机处理，不会上传。五分钟汇报建议播放约 40 秒关键片段，完整录屏可在讨论时播放。

资料更新至 2026-11-26。主稿重点是完整框架、TerraBlind 事实描述方法的参考、自训练 Jev 式架构与 V010 的 1,002,753 个可训练参数、dsh 资源运营协议，以及 V003–V010 迭代时间轴。第 7 页将原来的两页合并，上方说明各阶段改进，下方列出数据数量与质量、Jev 文本输入、skill 技能三个后续方向，最终目标是依靠这套架构实现完整通关。第 8 页为感谢与简短引用，包括 TerraBlind、Qwen3.5-2B、REPENTOGON / Isaac API。运行配置为 V009，V010 已完成训练；实机追敌与躲避效果仍需验证。早期 V006 九组实验保留在附录。

`speaker-script.bilingual.html` 提供面向未参与项目的汇报者的中英对照讲稿，按页、按段对应，可切换英文或中文单栏，也可打印。纯文本版本为 `speaker-script.bilingual.md`。演讲任选一种语言，约 5 分钟含 40 秒视频及看图停顿。`speaker-notes.en.md` / `speaker-notes.zh.md` 与 PPT 内按 `N` 展示的口述稿保持一致。

讲稿第 3–4 页补充 Jev 式架构的优势：复杂文本组织与语义理解、一次前向编码后直接选择动作，以及逐 token 生成完整分析带来的延迟区别。与 BERT 的比较表述为语义知识与能力描述的迁移潜力。约 53 毫秒的数据是一次 V009 L40S 请求的模型计算时间，不包含网络和游戏执行。

TerraBlind 的所核对 Jev 战斗链路调用 TypeSafe Jev API；本项目借鉴其状态事实、目标和候选后果的文本设计，自行冻结 Qwen 主干并训练动作决策头。报告中的具体层结构和参数量属于本项目，未假定 TypeSafe 原生 Jev 采用相同内部实现。

编辑 `content.bilingual.json` 中的中英文文案、`deck.css` 和 `deck.js` 后，在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 reports\midterm\build_decks.py
```

构建器默认重新嵌入 `assets/demo.mp4`。要更换构建视频，可运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 reports\midterm\build_decks.py --demo 'D:\path\new-demo.mp4'
```

`assets/demo.mp4` 和 `assets/demo.json` 是重建用的原视频副本与校验信息，分享已生成的 HTML 时不需要这两个文件。

脚本从 V008–V010 和历史九组实验的结果文件读取数字，并按 V010 权重张量统计参数，生成两份自包含 HTML 和中英对照讲稿。`content.bilingual.json` 是共享文案来源。三个 HTML 都可单独复制使用。视觉检查、截图及打印版检查存放在项目 `.local/midterm-qa-20261126`。

Open either HTML in Chrome or Edge. Use arrow keys to navigate, `F` for fullscreen, `N` for speaker notes, `O` for the overview, `A` for appendices and `H` to hide controls. The language switch retains the current slide. The main deck has 8 slides and targets five minutes. Printing includes all 10 slides. Slide 7 combines the development timeline with next steps for data, Jev text inputs and skill capabilities, aiming at a complete game run with this architecture. Slide 8 thanks the audience and lists the main references.

Slide 5 includes the full embedded 1920×1080 recording, approximately 2 minutes 33 seconds. Share either HTML by itself; no video attachment or assets folder is required. Open it in Chrome or Edge and press play on the demo slide. Each HTML is about 247 MB. To replace the video, choose a new local file, click “Export HTML with video”, and share the downloaded HTML. Processing stays on your computer. A roughly 40-second excerpt fits the five-minute talk.

The paired speaker script introduces the project for a presenter who did not participate in development. It distinguishes TerraBlind's use of the TypeSafe Jev API from this project's independently trained, Qwen-based action heads. The latest model shown in the architecture is V010. The deployed configuration remains V009, and the demo should retain its actual recorded model label.
