# TerraBlind 参考与以撒输入预览

源码与迁移分析见 [TERRABLIND_ISAAC_TRANSFER.md](../../docs/TERRABLIND_ISAAC_TRANSFER.md)。

| 文件 | 内容 |
| --- | --- |
| `terrablind-request-shape.json` | 固定 dev/agent 源码逐字提取的 instructions；state/criteria 为明确占位符 |
| `isaac-stationary-preview.txt` / `.json` | 现有以撒站定战斗快照的简短输入与完整审计事实 |
| `isaac-cleared-room-preview.txt` / `.json` | 清房后火焰危险快照的简短输入与完整审计事实 |
| `review-verification.json` | 参考提交、源码哈希、范围、检查与本机测量 |

预览不请求模型、不输出推荐动作、不发送游戏输入。它不是现有模型契约。JSON 保留证据，文本仅作摘要；未知机制没有填成确定结论。

在项目根目录运行：

```powershell
python scripts/preview_terrablind_transfer.py .local/isaac-skill/v005-stationary-snapshots.json --index 2 --output reports/terrablind-transfer/isaac-stationary-preview
python scripts/preview_terrablind_transfer.py .local/isaac-skill/fire-navigation-fixture-96.json --output reports/terrablind-transfer/isaac-cleared-room-preview
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -p test_terrablind_preview.py -v
```

输入快照属于本机已有研究材料，参考源码位于 `.local/references/TerraBlind` 与 `.local/references/TerraBlind-dev`。本轮未运行上游的训练脚本或游戏模组。
