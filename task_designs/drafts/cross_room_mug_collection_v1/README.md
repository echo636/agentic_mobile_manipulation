# 跨房间收杯：远处用托盘、近处手拿

2026-11-03。状态：**定义、派生场景与 CPU 检查完成；原生授权与三条完整参考在运行，自主 RGB 未开始。** 运行记录在工作区 `experiments/cross_room_mug_collection_20261103/`。

## 为什么建这个任务

前面的规划实验没有收益，一个主要原因是仿真器几乎不对走路收费：目标在画面里看得见时，`act` 内部接近免费完成移动，托盘省下的路换不来任何东西（见 `experiments/minimal_graph_gain_review_20261103/WHY_NO_GAIN.md`）。这个任务让一半物品远在另一个房间：模型看不见它们，必须自己多次导航过去。

## 任务

四只干净空杯收回厨房水槽台面，托盘回到该台面，最后空手。两只在厨房旁的餐桌上（约 2 m），两只在洗衣房长台面上（约 21 m，经客厅、走廊和两道推拉门）。托盘初始在厨房台面，已验证可一次托四杯。不规定是否用托盘及顺序。

- 定义：[problem0.bddl](problem0.bddl)；中英文指令：[instruction.zh.txt](instruction.zh.txt)、[instruction.en.txt](instruction.en.txt)。
- 空手由公开附加终态检查；"杯不能留在托盘上"写进了 BDDL。
- 场景由 `scripts/prepare_cross_room_mug_fixture.py` 从已通过授权的四杯任务派生：只移动两只远处杯子，并把两道推拉门交给原生授权开启。

## 三种完整方法

| 方法 | 操作 | 跨房间来回 | 何时最好 |
|---|---:|---:|---|
| 直送 | 8 | 远处 2 次 | 走路几乎免费时 |
| 远处托盘、近处手拿 | 16 | 远处 1 次 | 一次来回折算 ≥ 3 次调用时（历史跨区每次 5–19 次） |
| 托盘巡回（先餐桌后洗衣房） | 22 | 远处 1 次 | 几乎从不：比混合多 6 次操作，只省很少的路 |

所以最好的做法不是"总用托盘"，而是只在远处、多件的来源上用载具。

## 复现

```bash
PYTHONPATH=src:../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3 \
 /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python -m pytest -q tests/test_cross_room_mugs.py
```

原生参考：`scripts/run_cross_room_reference_chain.py --root <新实验目录> --source <冻结源码> --input <派生 task.json>`。参考每段跨房间只用一次特权导航，所以它只证明可执行和测路线米数，不能代替 RGB 中的调用成本。
