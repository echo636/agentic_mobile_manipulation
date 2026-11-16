# 多模式长程规划调度基准任务套件（`scheduling_v1`，共 7 项）

本目录存放已在远端 RTX PRO 6000 上完成**初始场景冻结、双策略特权参考流程验收、物理反例校验与多模型/多 CLI 消融回合**的 **7 项多模式长程规划基准任务定义**（轻量文本配方与冻结任务契约）：

- **T1–T3（时间等待并行掩蔽族）**：包含设备计时过程（微波炉 `180 s`、洗衣机 `300 s` + 烘干机 `240 s`、电水壶 `240 s`）与跨区域载具搬运耦合。
- **T4–T7（零等待 `0 s` 纯空间、状态与条件耦合族）**：无任何设备计时器，纯粹检验**双向回程配货（M1+M3）**、**单调工具洁净度状态机与闭柜盲盒盘点（M1+M4+M5）**、**有限缓冲区非单调腾挪交换（M1+M7）**，以及**带盈余退货代价的闭柜精确配平与压盖工具箱多跳解锁（M1+M5+M6）**。

按仓库清洁规范（`AGENTS.md` 第 3 节），几十 MB 的仿真初始场景快照 `initial_scene.json`、录像及回合日志不入 Git，统一存放在仓库外实验目录 `/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_lunch_benchmark_20261113/inputs/<task_id>/`（T7 存放在 `/mnt/data/home/sunyujie/robot_manipulation/experiments/route_ab_team_20261115/inputs/blind_cabinet_exact_restock_unlock/`；其 SHA-256 已固定记录在各目录 `task.json` 的 `frozen_instance.sha256` 字段中，可通过 `make_recipe.py` + 场景构建脚本复现验证）。

| 编号 | 任务 ID | 中文名称与主导规划模式 | 配方与冻结契约 | `task.json` SHA-256 | `initial_scene.json` SHA-256 |
|---|---|---|---|---|---|
| T1 | `hot_lunch_living_l2` | 双人热午餐 L2（M1 载具 + M2 并行等待 `180 s`） | [make_recipe.py](hot_lunch_living_l2/make_recipe.py) · [recipe.json](hot_lunch_living_l2/recipe.json) · [task.json](hot_lunch_living_l2/task.json) | `e1b3a2c7ed4a5aff3c1aa15f16f329171945d4031d5049f7717c9dfaaacfbd15` | `a1336ec63ed6015d9d2b9a5c88492c8a5e48ddc02b55b4dad6d8f27c836d0e89` |
| T2 | `study_guest_laundry` | 书房/客房整理与双段洗烘配送（M2 双段串联等待 `540 s`） | [make_recipe.py](study_guest_laundry/make_recipe.py) · [recipe.json](study_guest_laundry/recipe.json) · [task.json](study_guest_laundry/task.json) | `c18ca001121f17d3f810a50fa7056f65c07d688133383ac04d568aa27bc1f73b` | `a0b48b029ef890b5ad3c6649673a6a3bbbebbdb5c482cabda11df3e8898d0600` |
| T3 | `garage_garden_patio_prep` | 车库工坊与庭院露台晚间备场（M1 载具 + M2 窑干燥等待 `360 s`） | [make_recipe.py](garage_garden_patio_prep/make_recipe.py) · [recipe.json](garage_garden_patio_prep/recipe.json) · [task.json](garage_garden_patio_prep/task.json) | `93c102ce78f5c1622102082b182ec6bc3975c033d3d606711582735823418988` | `3447f3cfeff3352a7c7a70f2c7823bc7c2b005254cf4f50d3c7873f49cc56476` |
| T4 | `upstairs_study_bedrooms_exchange` | 二层双卧与中央书房 4 换 4 载具置换清洁（`0 s` 等待，M1+M3 双向配货） | [README](upstairs_study_bedrooms_exchange/README.md) · [make_recipe.py](upstairs_study_bedrooms_exchange/make_recipe.py) · [recipe.json](upstairs_study_bedrooms_exchange/recipe.json) · [task.json](upstairs_study_bedrooms_exchange/task.json) | `cc024b8ce0e2b8ddcea183cd0f5396ceeda83cc8ff4c4b3c62a675b38111de91` | `aee964f69b1d724a61e73ad296a59740f8a2ce5fc93edeb7fd3b0bb37fcc6727` |
| T5 | `kitchen_dining_cleanliness_restock` | 餐厨洁净度单调清洁与闭柜盲盒补货（`0 s` 等待，M1+M4+M7，契约 `r3`） | [README](kitchen_dining_cleanliness_restock/README.md) · [make_recipe.py](kitchen_dining_cleanliness_restock/make_recipe.py) · [recipe.json](kitchen_dining_cleanliness_restock/recipe.json) · [task.json](kitchen_dining_cleanliness_restock/task.json) | `f96749f82b24e3f48ea2eed68aa9953aa16d957ea47ba2bc8b94696400c5aaab` | `89eff5a6f09510fc99f5dacdc10e7fa937e7a434d62971241f33e332f647ab42` |
| T6 | `tabletop_buffer_nonmonotonic_swap` | 有限缓冲区非单调腾挪擦拭交换（`0 s` 等待，M1+M5 非单调腾挪，契约 `r3`） | [README](tabletop_buffer_nonmonotonic_swap/README.md) · [make_recipe.py](tabletop_buffer_nonmonotonic_swap/make_recipe.py) · [recipe.json](tabletop_buffer_nonmonotonic_swap/recipe.json) · [task.json](tabletop_buffer_nonmonotonic_swap/task.json) | `c74dd28288ee181c3426c0863619e1b9963314d8279c4e207a96976b096c6676` | `00754c1d94844eb0c6598b040aa9bae0475f349827818476cb4229c01858bdc9` |
| T7 | `blind_cabinet_exact_restock_unlock` | 闭柜未知存货精确配平与压盖工具箱多跳解锁（`0 s` 等待，M1+M5+M6） | [README](blind_cabinet_exact_restock_unlock/README.md) · [make_recipe.py](blind_cabinet_exact_restock_unlock/make_recipe.py) · [recipe.json](blind_cabinet_exact_restock_unlock/recipe.json) · [task.json](blind_cabinet_exact_restock_unlock/task.json) | `23f559b2afd1a2a8eb15f373423e59a716219d53236dc83a7f6cdf8456291c31` | `9f7ea072112535353eb75fe9cf31b6f641325fcd06cd662ae6d164a85e400402` |

完整实验配置、强弱模型六项效率指标同表汇总与逐帧审查结论见：
- [docs/progress_and_results_20261115.md](../../docs/progress_and_results_20261115.md)（最新总览报告）
- [docs/multi_mode_planning_benchmark_design_20261115.md](../../docs/multi_mode_planning_benchmark_design_20261115.md)（多模式规划难任务构造规范与文献调研）
- [docs/scheduling_ablation_20261114.md](../../docs/scheduling_ablation_20261114.md)
- [docs/scheduling_benchmark_baseline_20261114.md](../../docs/scheduling_benchmark_baseline_20261114.md)
