# 批量采集与正式训练

采集、L40S 训练、4060 模型加载及实机快照推理已验证，可以先扩大原始数据采集。当前模型的质量和延迟不足以接管战斗；连续影子推理及实时执行仍需在性能优化后验收。

## 采集

桥接服务启动且模组显示 `manual | CONNECTED` 后，开始一局新的随机种子 Isaac / Normal。出生房中开启录制：

```powershell
.venv\Scripts\python.exe -X utf8 run.py record rotate --purpose demonstration
```

正常游玩。先限定普通泪弹与单人 Isaac；不要主动制造受伤来凑负样本，也不要用调试刷怪/道具片段混入正式示范。不同随机种子、房间、弹幕和普通主动道具逐步扩充。负样本会从自然受伤的前段轨迹自动标注；单纯没掉血不意味着所有动作都正确。

结束或死亡后暂停，关闭这次录制：

```powershell
.venv\Scripts\python.exe -X utf8 run.py record stop
```

每次新局重新开启录制。录制完整原始状态，之后可以调整弹幕数量、历史摘要和标注窗口，无需重新游玩。同一种子重玩/继续始终属于同一集合。

```powershell
.venv\Scripts\python.exe -X utf8 run.py inspect recordings
```

## 第一批规模与划分

可先以 20–30 个独立训练局为一个采集批次，另留至少 5 个验证局和 5 个测试局。它是起步规模而不是保证效果的最低门槛；先看敌人/弹幕/动作转换和真实受伤事件覆盖，再决定增加多少。不要用几十条同一次受伤前的相邻帧代替几十个独立案例。

记录预留局的 run_id，显式划分，例如：

```powershell
.venv\Scripts\python.exe -X utf8 run.py prepare-dataset recordings --output datasets/combat-v003 --stride 3 --outcome-window-frames 30 --validation-run isaac-seed:验证种子 --test-run isaac-seed:测试种子
```

`--validation-run`、`--test-run` 可以重复。未指定的种子仍按 SHA256 默认分桶，检查 `dataset.json` 中的 `run_splits`，确认实际分配。不能给同一局同时指定两个集合。验证集用于选模型和改设计，测试集应在方案确定后才做最终评估。

## 上传与训练

```powershell
.venv\Scripts\python.exe -X utf8 scripts\create_training_bundle.py --dataset datasets/combat-v003 --output .local/transfer/isaac-jev-training-v003.zip
```

上传到授权服务器的 `/xufanghui/isaac-jev`，先核对包 SHA256，再解压；游戏、原始存档和模型缓存不会被打入项目包。现有模型缓存可复用。新的输入版本需要重新提取主干特征，不能复用旧版文本的 embedding。

```bash
bash scripts/train_l40s.sh datasets/combat-v003 artifacts/combat-v003
```

第一批建议仍使用短结果窗口，审查部分受伤前轨迹的标签。之后可比较 15/30/60 帧窗口，而不是直接把数秒后的伤害当作单次按键的错误证明。

正式验收应记录独立局清房率、受伤、命中/输出、实际决策耗时、命令因过期被拒绝的比例和人工接管次数。仅看动作准确率会被“持续按住上一动作”基线误导。
