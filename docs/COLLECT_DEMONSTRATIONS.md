# 批量采集与正式训练

采集、L40S 训练、4060 模型加载及实机快照推理已验证，可以先扩大原始数据采集。当前模型的质量和延迟不足以接管战斗；连续影子推理及实时执行仍需在性能优化后验收。

## 采集

双击项目根目录的 `Start_Recording.cmd` 开启后台采集。之后只需在实验版游戏中正常游玩，新局、死亡重开、退出游戏和重新进入均自动处理，每局或连接片段分开保存。新版模组显示 `manual | CONNECTED | REC` 表示正在录制；`NOT REC` 表示未录制，`REC ERROR` 表示写入错误或丢帧，需要检查。

重开游戏不会关闭后台采集；游戏断开时自动封存当前 gzip，重连时自动新建文件。重玩同一种子仍归入同一训练/验证集合。请使用独立沙箱中的实验版，原版 Steam 游戏没有这条采集通路。

当前正在运行的旧模组需要下次重启实验游戏才显示 REC，新版后台可以先开始保存数据。重启电脑后需再双击 `Start_Recording.cmd`；关机前双击 `Stop_Recording.cmd` 封存录制。不要直接强制结束采集进程或断电，否则当前尚未封存的片段可能不完整。

也可以通过命令手动开启一次连续录制：

```powershell
.venv\Scripts\python.exe -X utf8 run.py record rotate --purpose demonstration
```

正常游玩。先限定普通泪弹与单人 Isaac；不要主动制造受伤来凑负样本，也不要用调试刷怪/道具片段混入正式示范。不同随机种子、房间、弹幕和普通主动道具逐步扩充。负样本会从自然受伤的前段轨迹自动标注；单纯没掉血不意味着所有动作都正确。

可以正常使用炸弹，操作与实体仍会完整记录。当前战斗模型不支持放炸弹，因此战斗中人工按过炸弹键的整个房间会在数据准备时自动排除，其他房间仍可训练；不用手动停止录制或删文件。探索阶段放炸弹不按此规则排除。

需要结束整个采集时，双击 `Stop_Recording.cmd`，或运行：

```powershell
.venv\Scripts\python.exe -X utf8 run.py record stop
```

每次新局无需手动开启或停止录制。录制完整原始状态，之后可以调整弹幕数量、历史摘要和标注窗口，无需重新游玩。同一种子重玩/继续始终属于同一集合。

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
