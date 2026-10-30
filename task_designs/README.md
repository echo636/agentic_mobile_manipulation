# 已构建的自建任务

截至 2026-10-30，按**不同任务目标**去重，共 **16 项：长程分支 14 项，yujie 分支 2 项**。
只收录已有实际仿真场景和任务定义的任务。未构建设计不进入清单；同任务的旧版本、重复运行、六份 runtime 快照均不另计。
复用 BEHAVIOR 资产但重新定义场景或任务目标的任务属于自建任务；原样测试 BEHAVIOR 任务不属于此集合。

机器清单：[constructed_catalog.json](constructed_catalog.json)。这是构建资产目录，**不是 16 项自主成功记录，也不是 16 个不同场景**。

## 当前复杂任务、保留基线及新增任务：7 项

| 任务 | 定义 | 已有证据及接入状态 |
| --- | --- | --- |
| 庭院维护、设施装配与双人休息区布置 | [garden_maintenance_and_guest_setup_v2](complete_v2/garden_maintenance_and_guest_setup_v2.json) | 已冻结场景，已有完整自主实验 |
| 工作间家具装配、备柴与物资交接 | [workshop_build_process_and_handover_v2](complete_v2/workshop_build_process_and_handover_v2.json) | 已冻结场景，已有完整自主实验 |
| 厨房启用、双人备餐与留餐冷藏 | [kitchen_reopen_meal_and_cold_storage_v2](complete_v2/kitchen_reopen_meal_and_cold_storage_v2.json) | 已冻结场景，已有完整自主实验 |
| 书房维护、洗烘与客房入住准备 | [study_guest_room_and_laundry_v2](complete_v2/study_guest_room_and_laundry_v2.json) | 已冻结场景，已有完整自主实验 |
| 酒店鞋履除尘、配对与归位 | [hotel_shoe_care_and_pairing_100_v1](constructed_100plus/hotel_shoe_care_and_pairing_100_v1.json) | 20 只鞋的重复搬运基线；已构建并核对初态 |
| 准备并端上热饮与点心 | [prepare_and_serve_hot_drink](imported/yujie/README.md#热饮与点心) | yujie：已构建并运行；历史采用高层热饮评分，当前执行器接入待完成 |
| 两袋杂货搬入、分类收纳与关门 | [carry_in_and_put_away_groceries](imported/yujie/README.md#两袋杂货) | yujie：已构建，历史有原生自定义 BDDL 自主成功；当前执行器接入待完成 |

四个复杂任务的源码配方仍保留早期 `complete_recipe_physics_pending` 字段；后续实际冻结场景及自主运行证据在清单的 `built_instance` / `evidence` 中。目录收录不修改旧配方和历史结果。

## 历史已构建任务：9 项

这些也纳入总数，但不因为收录而重新加入当前实验队列。

| 任务 | 定义 | 区别与构建证据 |
| --- | --- | --- |
| 双人热午餐配送与厨房复原 | [warm_lunch_and_restore_v2](evaluation_v2/warm_lunch_and_restore_v2.json) | 已构建、恢复并执行参考流程；v1/v2 合计一项 |
| 观影准备与客厅复原 | [movie_night_and_restore_v2](evaluation_v2/movie_night_and_restore_v2.json) | 做爆米花、清桌摆台及收尾；v1/v2 合计一项 |
| 工具箱整套配送、卸载与空箱归还 | [workshop_setup_and_return_v2](evaluation_v2/workshop_setup_and_return_v2.json) | 终态为工具留在工作台、空箱归还；v1/v2 合计一项 |
| 双人整盒送餐、分餐与空盒归还 | [kitchen_service_and_reset_v1](candidates/kitchen_service_and_reset_v1.json) | 复用午餐场景，但增加整盒配送、分餐和空盒归还目标；已真实执行参考流程 |
| 工具箱往返配送与完整盘点 | [workshop_inventory_roundtrip_v1](candidates/workshop_inventory_roundtrip_v1.json) | 复用工坊场景，但要求全部卸出盘点、重新装箱并整箱归还；旧版有完整脚本参考通过记录 |
| 庭院盆栽处理与分区布置 | [garden_treatment_and_distribution_100_v1](constructed_100plus/garden_treatment_and_distribution_100_v1.json) | 20 盆处理、分区及保护对照；已冻结场景 |
| 车库与庭院劈木、产物归置及扫地 | [garage_cut_sort_store_113_v1](constructed_100plus/garage_cut_sort_store_113_v1.json) | 16 根木料切分、收纳及清理；已冻结场景 |
| 双卧室腾空吸尘、归位与夜间闭场 | [bedroom_vacuum_reset_close_102_v1](constructed_100plus/bedroom_vacuum_reset_close_102_v1.json) | 两房腾空、吸尘、归位与关灯；已冻结场景 |
| 双卧室清洁与共用书柜整理 | [bedroom_shared_library_branching_v2](constructed_branching/bedroom_shared_library_branching_v2.json) | 两房清洁与公共书柜形成交叉依赖；49 条初态检查及独立恢复通过，完整参考未通过 |

任务 ID 内的 `100`、`102`、`113` 是历史设计账本标记，不是实测最短步数。

## 文件与证据

- 原有定义保留原路径；现有运行、冻结源码和评测输入不因整理而改变。
- yujie 的两项任务定义放在 [imported/yujie](imported/yujie/README.md)，来源固定为 PR #6 的 `bbe9b5f230cae81c58bc945ccb15aaaf4c01a206`。本次未合并整个 PR，也未导入其执行器实现。
- `constructed_catalog.json` 的 `definition` 相对仓库根目录；`built_instance` 和 `evidence` 相对共享工作根目录 `/mnt/nas/home--xujingyi/agentic_mobile_manip`。本地 14 项逐项记录冻结任务和场景 SHA-256。
- yujie 的构建与运行来自作者归档证据；定义文件已迁入，场景资源和历史运行协议通过固定版本来源索引保留。整理文件不等于已在当前执行器上复测。
- 本次核对记录：共享根目录 `operations/custom_task_catalog_20261030/`。只验证清单、文件来源和已有冻结资产，没有新 GPU/模型试验。

## 不纳入统计

未构建的提案、短版重设计，以及 yujie 的餐后清台设计不收录。BEHAVIOR 原有的蘑菇、披萨、海鲜饭、派对清理、餐厅摆台和自助餐任务等，也不因换了执行器或实例而计作自建任务。
