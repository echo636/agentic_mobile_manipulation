# `prepare_and_serve_hot_drink`

这是一个有真实前后依赖的长程偏序任务，包含取水、烧水、取餐具、摆台、准备点心、冲饮品和上桌等多个操作：

```text
retrieve_water ──> boil_water ──> pour_drink ───────┐
                                                   ├──> serve_drink
gather_tableware ──> set_table ──> prepare_snacks ──┘
```

- `retrieve_water`：从冰箱取得唯一水源；水瓶用完后的最终位置不限定。
- `boil_water`：使用取出的水装入水壶并烧开；它依赖 `retrieve_water` 的产出。
- `gather_tableware`：从橱柜取出餐巾、餐垫、盘子和茶匙，可以与烧水并行。
- `set_table`：把托盘、餐垫和餐具布置到茶几；盘子、茶匙、餐巾和杯子可放在茶几或茶几上的合适承托物上，给人的指令不限定叠放顺序。
- `prepare_snacks`：把饼干放到茶几上的盘子、托盘或茶几台面，蜂蜜放到托盘或茶几台面；给人的指令没有指定唯一摆法，因此这些最终位置均可接受。
- `pour_drink`：把烧开的水倒入杯子，依赖烧水完成。
- `serve_drink`：把装好热水的杯子端到茶几，同时依赖饮品和点心准备完成。

因此这不是几个互不相关目标的并集，而是一个带两条依赖链和一个汇合点的偏序图。取水是由唯一初始水源和烧水转化共同约束的物理前提，不用未告知机器人的水瓶最终位置充当目标。示例 `PartialOrderBehaviorTask` 追踪其余可从最终状态判定的里程碑，只有依赖满足后，后继里程碑才会计入完成。

`partial_order.json` 只给可从最终目标条件追踪的里程碑编号；`retrieve_water` 是烧水的因果前提，**不是额外的最终位置目标**，因此不占一个 `goal_indices`。当前正式视觉实验使用 `high_level_visual.bddl` 加 `HighLevelHotDrink` 高层评分，尚未使用可选 `PartialOrderBehaviorTask` 作为评分器。完整构建和审计流程见 [长程任务构建 workflow](../../docs/long_horizon_task_workflow.md)。
