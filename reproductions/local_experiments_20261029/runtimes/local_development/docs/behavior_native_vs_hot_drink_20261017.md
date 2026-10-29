# BEHAVIOR 原生任务与热饮长程实验的差异（2026-10-17）

## 结论

这不是「原生 BEHAVIOR 都能完成，热饮任务不能」的二分。已有原生 `autonomous_cut_r4` 和 `autonomous_hang_r1` 成功，但 `autonomous_spray_r1` 失败。真正的工程差异是：原生实验使用同一份 BDDL/实例绑定来初始化和判定；热饮实验虽然加载了实例，却另写了高层动作状态和 11 项评分，场景、可观察反馈与评分之间没有单一事实来源。

| 已有运行 | 动作数 | 目标比例 | 官方判定 |
| --- | ---: | ---: | --- |
| `atomic_actions_20261008/autonomous_cut_r4` | 14 | 1.0 | 成功 |
| `atomic_actions_20261008/autonomous_hang_r1` | 6 | 1.0 | 成功 |
| `atomic_actions_20261008/autonomous_spray_r1` | 10 | 0.0 | 失败 |
| `hot_drink_real_assets_20261015/10_rgb_pixel_explicit_nav_partial` | 20 | 2/11 | 不适用 |
| `hot_drink_real_assets_20261015/11_rgb_pixel_explicit_nav_retest` | 41 | 0/11 | 不适用 |

原生配置路径见 [`omnigibson_backend.py`](../src/manipulation_agent/omnigibson_backend.py)：读取 `available_tasks.yaml`、指定 scene template 与实例状态，检查物体绑定，使用 BEHAVIOR task。原生 BDDL 例子 [`clean_a_keyboard`](../../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/clean_a_keyboard/problem0.bddl) 只有一个清洁目标；[`chopping_wood`](../../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/chopping_wood/problem0.bddl) 虽有多个目标，但属于同类重复操作。热饮任务的 [`high_level_visual.bddl`](../tasks/prepare_and_serve_hot_drink/high_level_visual.bddl) 跨冰箱、橱柜、灶台和客厅茶几，且水被注释为只由高层 harness 追踪、没有物理粒子。成功判定实际来自 [`HighLevelHotDrink.evaluate`](../src/manipulation_agent/high_level_hot_drink.py)，`official_task_success` 为 `null`。

## 已确认的失配

1. [`scene_layout.json`](../tasks/prepare_and_serve_hot_drink/scene_layout.json) 和固定实例把橱柜设为打开，先前高层状态却初始化为关闭。机器人在 RGB 中看到打开的橱柜，选取里面物品时可能被 `container_closed` 拒绝。这一项已修复为打开，并增加回归测试。
2. 场景有多个可见工作台。此前选中另一个合法工作台像素会被固定名称匹配拒绝；已改为接受同类表面并保留实际选中的物体。
3. 旧版 `wait` 将 20 秒加热记录为符号状态，画面中却没有稳定的烧开反馈。现已改为模型对 RGB 中的灶台选点并调用 `boil`；水壶须在灶台且装有水，成功结果明确返回 `water_boiled=true`。新版 `wait` 不再加热；旧轨迹仍按原规则重放。此反馈只确认本次高层动作，不等于官方 BEHAVIOR 成功。
4. 长链增加了失败机会，但不是启动慢的特有原因：已有热饮 v11 启动约 153 秒，而原生 cut/hang 样例约 206/329 秒。v11 的 18 次显式导航调用中 17 次形成路径，仍在 41 个动作后为 0/11；瓶颈在长程闭环与状态一致性，而不只是仿真加载。

## 后续实验准则

维持用户指定的人类短指令、`reasoning_effort=low`、80 次动作预算、`MAS_DEMO_MOTION=0`、`OG_SKIP_PHYSICS_STEPS=0`，以及模型显式选择 `navigate_to` 与 RGB 像素目标。运行前用场景实例校验高层初态、物体可见性和每个原子动作的可达目标；运行后同时报告像素/导航协议、轨迹与语义目标，不混用官方成功率。要得到可比的 BEHAVIOR 成绩，需要让目标判定回到官方 BDDL/仿真状态，或明确把当前结果标为非官方高层 harness 实验。
