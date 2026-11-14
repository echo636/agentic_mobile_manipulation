# 跨区长程调度基准任务定义（`scheduling_v1`）

本目录存放已在远端 RTX PRO 6000 上完成**初始场景冻结、双策略特权参考流程验收、物理反例校验与 `gpt-6.1-sol (low)` 强模型基线回合**的 3 项跨区域长程调度基准任务定义（轻量文本配方与冻结任务契约）。

按仓库清洁规范（`AGENTS.md` 第 3 节），几十 MB 的仿真初始场景快照 `initial_scene.json`、录像及回合日志不入 Git，统一存放在仓库外实验目录 `/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113/inputs/<task_id>/`（其 SHA-256 已固定记录在各目录 `task.json` 的 `frozen_instance.sha256` 字段中，可通过 `make_recipe.py` + 场景构建脚本复现验证）。

| 任务 ID | 中文名称 | 配方与冻结契约 | `task.json` SHA-256 | `initial_scene.json` SHA-256 |
|---|---|---|---|---|
| `hot_lunch_living_l2` | 双人热午餐 L2（跨厅载具分批 + 并行加热） | [make_recipe.py](hot_lunch_living_l2/make_recipe.py) · [recipe.json](hot_lunch_living_l2/recipe.json) · [task.json](hot_lunch_living_l2/task.json) | `e1b3a2c7ed4a7c87339a2b2c81c92c0a5054f30164a498f88be704ba976ab88a` | `a1336ec63ed6178962fb98f5126f6c5319d9216fb55e99b18d70d88b1b771ba8` |
| `study_guest_laundry` | 书房/客房整理与双段洗烘配送 | [make_recipe.py](study_guest_laundry/make_recipe.py) · [recipe.json](study_guest_laundry/recipe.json) · [task.json](study_guest_laundry/task.json) | `a9ce1aa4463d58cf194421aa039f9a79d150b5db09be0e1920073393e3e94b89` | `a0b48b029ef82c980cf317a401809ba00f46b77525e0e14d5ed1a1585e9694af` |
| `garage_garden_patio_prep` | 车库工坊与庭院露台晚间备场 | [make_recipe.py](garage_garden_patio_prep/make_recipe.py) · [recipe.json](garage_garden_patio_prep/recipe.json) · [task.json](garage_garden_patio_prep/task.json) | `d44cddf6d9cb02287e7f9334d39757489ba1ebbc9d6b385d5111b3b75f28612b` | `3447f3cfeff352809b329346f8c0969e5814b34bf3b00b0f6b974e6c788a96af` |

完整实验配置、六项效率指标同表汇总与行为诊断见 [docs/scheduling_benchmark_baseline_20261114.md](../../docs/scheduling_benchmark_baseline_20261114.md)。
