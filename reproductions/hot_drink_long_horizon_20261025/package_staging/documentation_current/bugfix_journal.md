# 任务失败案例与 benchmark 构建经验

本文服务于**构建一套可靠的长程任务 benchmark**：记录具体任务中的失败，判断失败来自 agent 能力、任务定义、场景还是执行与评分协议，再把经验证的经验用于构建后续任务。每个案例须能回答“哪个任务的什么失败、修了哪一层、新任务怎样提前发现它”。

创建或修改任务前，按场景与机制查本索引；出现失败后按模板补录。每个案例使用稳定编号，追加更正与证据。原始回合、任务哈希和评分保留原样，新定义使用新目录。通用执行器实现细节可链接专项文档，本文保留它对任务有效性的影响。

## 当前案例涉及的任务

| 任务 | 任务包与仿真实例 | 任务结构 | 当前成熟度 |
| --- | --- | --- | --- |
| 两袋杂货搬运与分类收纳 v2 | `tasks/carry_in_and_put_away_groceries/v2_design/`；仿真任务 `carrying_in_groceries`，场景 `house_double_floor_lower`，实例 301 | 车库取两袋 → 跨房间搬运 → 六件食品分类收纳 → 空袋归位与关门；搬运和分类可以交错 | 研究候选，尚未达到正式 benchmark 收录条件 |

以下 GROC 案例全部来自该任务，不能外推为所有场景均存在或均已修复。任务包含三个历史定义：单柜 `_0`、单柜 `_1`（各 11 项目标），以及当前双柜任选（每分支 12 项目标）。当前另有纸袋高度 ×1.2 的场景变体和 `explicit_navigate_to_only_v1` 动作协议；BDDL 不变也须区分场景与协议版本。按各回合冻结的 BDDL 与场景 overlay 区分版本，任务名相同不代表分数可直接比较。

## 索引

| 编号 | 具体任务中的失败 | 失败归属 | 对新任务构建的启示 | 当前状态 |
| --- | --- | --- | --- | --- |
| GROC-001 | 杂货 v2：开门后仍不能从车库进入室内 | harness 导航执行器 | 跨房间任务先验证门状态改变后的真实携物通路 | 探针和自主搬运验证通过 |
| GROC-002 | 杂货 v2：放入另一合理厨房吊柜被判失败 | 任务 BDDL 与场景绑定 | 指令允许的合理解必须与评分接受集合一致 | 离线、双柜分放探针与新定义自主回合通过 |
| GROC-003 | 杂货 v2：漏掉同类第二件，模型报告完成 | agent 清单与完成判断；可观测性待核实 | 保留多件清点难度，验证每件均能发现和操作 | 旧失败保留，新回合六件未漏；不宣称普遍解决 |
| GROC-004 | 杂货 v2：操作动作出现非显式导航路径 | harness 动作边界 | 任务可执行还须通过统一动作协议审计 | 自动接近已移除，2026-10-21 自主路径审计通过 |
| GROC-005 | 杂货 v2：声明装袋的番茄加载后不在袋内 | 场景装袋布局与容纳空间 | 添加食品后重新验证全部初态谓词，不能只查新增物体 | 初态、静置及开发实例自主携物导航通过 |
| GROC-006 | 杂货 v2 成功回合：暂放遮挡造成反复开关门、换位，旧 skill 又误称抓取可自动接近 | agent 恢复与 skill 指导；因果贡献待消融 | 用动作段和反馈优化通用 skill，版本与任务分别冻结 | 候选 v1：120 次、11/12，漏关吊柜，未证明收益 |
| GROC-007 | 杂货 v2：选中低矮牛奶盒后，导航按地板航点执行，随后物品被裁切 | 执行器导航分类 | 按选中可见表面的所属物体区分地板；不放宽抓取距离 | 分类及仿真核心链已修正；完整对照与自主回归待完成 |

历史执行器修复另见 [2026-10-01 记录](executor_repairs_20261001.md)。本文件记录杂货任务迭代及之后的新问题。

## 新条目模板

未知项写明未知，补上下一步验证方法。

```text
编号 / 日期 / 状态：
任务名称 / 任务包 / 场景 / 实例 / 种子：
任务定义版本与哈希 / 场景 overlay / 动作与评分协议：
任务要测的能力及发生失败的子目标：
现象与触发条件：
最小复现或原始回合：
失败归属：agent、任务定义、场景、harness、仿真器，或待定位
已确认原因及证据：
仍待验证的解释：
修复层与文件：
修改前后的行为：
验证命令 / 预期结果 / 实际结果：
验证层级：静态、原生逻辑、模拟器探针、自主回合，分别列出
复用条件与边界：
新任务的预防检查 / 对应通过条件：
对 benchmark 有效性与历史结果可比性的影响：
遗留问题 / 下一步：
更正记录：
```

只有针对原始现象的回归检查通过，才标记该现象已修复。动作成功、离线逻辑通过、探针通过和自主任务成功分别记录。

## 怎样把案例用于构建 benchmark

| 构建检查 | 可复查的通过条件 | 案例来源 |
| --- | --- | --- |
| 连贯性与能力覆盖 | 每个子目标服务同一用户目标；标明所测能力与真实依赖。任务集覆盖不同场景、机制和依赖结构，记录差异；动作多或路线长本身不算复杂性 | 杂货的搬运、分拣、容器与收尾链 |
| 指令与评分一致 | 对合理完成方式设正例，对漏件、错误目的地和未关闭设反例；只对指令要求评分；同类合法容器都可接受 | GROC-002 |
| 可发现且可执行 | 目标物可从允许的 RGB 视角逐步发现，遮挡可通过合法动作解除；关键链有真实动作及原生谓词证据，跨门包含携物探针 | GROC-001、GROC-003 |
| 能区分能力失败和设施缺陷 | 场景可执行、协议正确时，保留 agent 漏件或规划失败。发现不可达、错误评分或暗中代做时先修相应层，再冻结新版本 | GROC-001 至 GROC-004 |
| 独立评分与统一协议 | 模型不读取作者目标清单、私有状态与评分；统一动作语义、预算和观测；显式导航、物理连续性、日志与独立评分审计通过 | GROC-004 |
| 复现与泛化 | 固定定义/场景哈希、实例、种子与运行配置；按场景或布局/对象组合划分开发实例和留出实例，在留出实例检查机制可用性并独立报告 agent 结果 | 后续任务集必须补充，当前单实例未覆盖 |

**收录判断：** 任务定义、可观测性、关键动作链和协议检查通过后，才冻结为 benchmark 版本。agent 成功率不作为收录门槛；有效的失败应保留为测量结果。修复任务缺陷后更新版本、重跑受影响检查，分别报告旧版与新版结果。通过增加预算、暴露隐藏清单或按某个模型的选点收窄评分来提高通过率，会改变测量条件，必须另立版本。

**杂货 v2 的待办：** 2026-10-21 首个新协议自主回合 12/12，路径与视频审计通过，本开发实例的较高袋携物导航和六件逐件 RGB 选择已有证据。重复运行、留出布局、不同暂放条件和成组搬运边界仍需验证。旧 9/11 原样保留，这个任务仍不是已验收的 benchmark 条目。当前配置与探针入口见[候选任务卡](../tasks/carry_in_and_put_away_groceries/v2_design/benchmark_card.md)。

## GROC-001：门开着，机器人却进不去

- 任务与失败阶段：两袋杂货 v2，`house_double_floor_lower` / 301，车库取物后的跨房间搬运；基础设施缺陷妨碍评估搬运与规划能力。
- 日期：2026-10-20。状态：修复已通过私有几何探针和自主往返搬运验证，不代表整任务成功。
- 现象：杂货 v2 首轮在车库门附近反复导航失败，门打开后仍进不了室内。
- 原因：运行时地图保留门关闭时的阻塞；地板选点沿用物体观察的约 1.4 米停靠距离，短距离跨门航点可能停在门外。
- 修复层：harness 的地图更新与目标停靠语义。文件为 [doorway_traversability.py](../src/manipulation_agent/executors/doorway_traversability.py)、[gt_navigation.py](../src/manipulation_agent/executors/gt_navigation.py)、[omnigibson_rgb.py](../src/manipulation_agent/executors/omnigibson_rgb.py)。未修改 OmniGibson / PhysX 源码。
- 修改：地板航点按落脚点执行；真实门状态为 Open 时，在该门附近合入源场景开门通行图的格子。源地图和其他门保持原状态。
- 证据：失败回合 `../experiments/groceries_v2_gpt61sol_low_20261020_r2`；探针 `../experiments/groceries_v2_door_probe_20261020_r6/result.json` 在同一地板目标实际移动约 1.74 米到门内。探针不是自主成绩。
- 回归入口：`PYTHONPATH=src python3 -m unittest discover -s tests -p test_doorway_traversability.py`；相关检查在 `tests/test_gt_navigation.py`、`tests/test_gt_backend.py`。当时相关测试合计 61 项通过。
- 自主证据：`../experiments/groceries_v2_gpt61sol_low_doorfix_20261020_r3` 两袋过门；`../experiments/groceries_v2_cabinetfix_gpt61sol_low_20261020_r1` 再次完成往返。轨迹保存在各自 `run/base_motion.jsonl`、`run/navigation_plans.jsonl`。
- 复用条件：遇到“门开了但路径仍不可达”，分别检查地图更新和航点停靠距离。补图要求有对应场景开门通行图、正确门位置与真实 Open 状态；其他场景须重新做门洞探针。
- 边界：未证明所有门型均可通过；显式导航协议另见 GROC-004。

## GROC-002：合理吊柜放置被错误评分

- 任务与失败阶段：两袋杂货 v2，`house_double_floor_lower` / 301，饼干分类收纳的终态评分；任务定义缺陷会制造假失败。
- 日期：2026-10-20。状态：两柜语义通过离线回归；`readiness_probe_20261020_r3` 在真实模拟器中分放两柜后原生整目标为真，开柜反例为假。尚未做新定义的自主回合。
- 现象：`doorfix_20261020_r3` 两包饼干抓放成功，目标却为 false。
- 证据：该回合 `run/events.jsonl` 的 `event-00116`、`event-00122` 显示实际放入 `top_cabinet_tynnnw_1`；原评分只绑定 `_0`。两对象在源模板中都属于 `kitchen_0`，人类指令只要求“厨房吊柜”。
- 根因：把类别级目标收窄为一个未告知机器人的实例。首次只把绑定改到 `_1`，仅修正该次选柜，没有表达任一厨房吊柜都合理。
- 更正：用户指出两柜都应接受后，新增 `cabinet.n.01_2` 绑定另一个厨房吊柜；每包饼干的 Inside 目标使用 OR，允许同柜或分柜；两个吊柜均要求关闭。
- 文件：[problem0.bddl](../tasks/carry_in_and_put_away_groceries/v2_design/problem0.bddl)、[build_scene_overlay.py](../tasks/carry_in_and_put_away_groceries/v2_design/build_scene_overlay.py)、[test_grocery_task_goals.py](../tests/test_grocery_task_goals.py)。属于任务定义修复，未改通用评分器和仿真器。
- 回归命令（仓库根目录）：

  ```bash
  PYTHONPATH=src:../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3 \
    /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python \
    -m unittest discover -s tests -p test_grocery_task_goals.py
  ```

- 实际结果：修改前 4 个子用例失败（3 种合理选柜组合被拒绝，另一柜未关闭被忽略）；修改后 4 组测试通过，覆盖 4 种选柜组合、任一牛奶/饼干缺失、任一柜未关闭和非法候选容器。
- 场景检查：`../experiments/groceries_v2_scene_overlay_20261020_either_cabinet` 的两份模板及实例同时绑定两柜，状态一致；原生 BDDL 得到 4 个替代分支，每分支 12 个原子目标。离线真值测试没有验证物理抓放。
- 版本边界：旧定义 11 项，新定义每分支 12 项。历史 9/11 保留为旧定义结果，不追溯重写；`goal_options` 多行是替代分支，不是时间序列。
- 复用：指令允许任一同类目标时，列出满足房间和用途限制的候选，通过 OR / 量词表达允许集合，并测试合法及非法候选。执行器仍操作模型实际选中的对象。

## GROC-003：只处理每类一件，漏掉同类第二件

- 任务与失败阶段：两袋杂货 v2 的单柜 `_1` 定义，`house_double_floor_lower` / 301，多件清点与结束判断；这种能力挑战应保留，可观测性缺陷须另行排查。
- 日期：2026-10-20。状态：失败行为已确认，尚未修复；遮挡与散落的贡献尚未隔离。
- 回合：`../experiments/groceries_v2_cabinetfix_gpt61sol_low_20261020_r1`，`gpt-6.1-sol / low`、规划 skill 开启、120 动作预算；77 次 act/look 后 `finish(achieved)`，独立评分 9/11。
- 未满足：第一盒牛奶入冰箱、第二包饼干入吊柜。奶酪、番茄、第二盒牛奶、第一包饼干有成功抓放链；漏掉两件没有对应的成功链，不能据此诊断为抓取器普遍失效。
- 已确认失败链：初始计划把全部冷藏品合并一项；`event-00196` 用“冷藏三件已入冰箱”将其设为 done；`event-00261` 写“四件食品均已成功分类放置”，把全部子目标设为 done；`event-00263` 据此声明 achieved。实际有六件食品。类别完成被当成逐件完成，空袋声明缺少可靠清空证据。
- 相关条件：原指令未列食品数量；`event-00116` 的模型记忆报告两次桌面放置失败后食品散出；袋口和冰箱侧壁遮挡导致多次换视角。这些线索不足以证明漏件各自的最终物理位置或唯一根因。
- 未做的修复：没有增强抓取器、给模型隐藏对象清单、暴露评分或通过结束守卫强制成功。
- 下一步：用公开 RGB/动作反馈维护逐件清单，区分已见数量、去向及不确定袋内物；核对每件和容器清空证据。若指令新增准确数量，单独标为任务输入变化。通过新自主回合与独立评分验证效果。
- 复用：同类多件和遮挡任务，分别维护类别、数量与完成证据。某类动作成功一次不能证明所有同类物品完成。
- [完整审阅页](http://10.130.140.115:8769/groceries_v2_cabinetfix_20261020_index.html)；原始证据为该回合 `run/events.jsonl`、`run/run.json`、RGB 和 `controller/model_events.jsonl`。

## GROC-004：显式导航协议尚未完全满足

- 任务与失败阶段：两袋杂货 v2，`house_double_floor_lower` / 301，操作与导航交错阶段；协议缺陷未解决前，无法与要求完全显式导航的任务公平比较。
- 日期：2026-10-20。状态：已修复两处 RGB 自动接近逻辑；单元检查和真实模拟器远距操作检查通过，待完整自主导航审计。
- 证据：`doorfix_20261020_r3`、`cabinetfix_20261020_r1` 的 `demo_audit.json` 均有 `all_paths_from_explicit_navigate_to=false`。后者调用对齐、底盘高度和录像通过，仍不能视为完整协议合格。
- 确认原因：`RGBBackend.execute_visual` 对超过 1.4 米的操作目标主动调用 `_navigate`；demo 手臂够不到时还会导航重试。是执行器真实代做，不能只解释为日志归属。
- 修复文件：`src/manipulation_agent/executors/omnigibson_rgb.py`。远距离 RGB 操作返回 `out_of_reach` 且不动底盘；取消 demo 隐式导航重试。历史命名 semantic 模式独立保留。新 provenance 记录 `explicit_navigate_to_only_v1`，旧回合不追溯更改。
- 复现及回归：`tests/test_visual_manipulation_navigation.py` 修改前远距离 grasp/open/close/place_inside 均触发暗中导航；修改后 3 项边界测试通过。相关导航、放置、demo、高层视觉和评分回归合计 56 项测试、17 个子测试通过。
- 真实证据：`../experiments/groceries_v2_readiness_probe_20261020_r3/run/result.json` 的远距操作返回 out_of_reach、机器人位置不变。该检查注入私有几何选点，不证明模型 RGB 发现能力；完整新自主路径审计仍需运行。未修改仿真器源码。

## GROC-005：新增食品后，原有食品初态失真

- 任务与阶段：杂货 v2，`house_double_floor_lower` / 301，场景加载与装袋初态；这是场景有效性问题。
- 现象与证据：`../experiments/groceries_v2_readiness_probe_20261020_r1/run/result.json` 中原袋高度下番茄 Inside 为 false，另五件为 true；静置 60 步仍失败。番茄位于奶酪上方。定义却声明六件都在袋内，静态对象绑定无法发现这个问题。
- 受控修改：仅将两只纸袋的垂直 scale 乘 1.2；食品尺寸、数量、目的地与评分条件不变。`build_scene_overlay.py` 支持显式 `--bag-height-scale` 并写入 manifest，默认使用 1.2；原 overlay 与旧实验保持不变。
- 验证：r2/r3 的六件 Inside 初态与 60 步静置均通过，两袋暂放后再静置 90 步仍通过。说明增加容纳高度能修复该实例的装袋几何；尚未隔离各接触体的具体碰撞贡献，也未证明其他布局通用。
- 失败恢复：r3 在真实 `_placement_context` 内对第一袋故意释放并移动，再注入错误，袋子、三个子物体和持握状态均恢复。这是故障注入，不是原失败像素的逐帧重放。
- 探针自身失败也保留：r1 先拿支撑牛奶导致成组搬运后支撑校验失败；r2 先拿奶酪连带番茄，采样预算耗尽；r3 冷藏优先仍连带第二袋饼干。后续探针对全部六件按几何高度排序，仅用于证明逐件提取路径，不能作为给模型的计划答案。
- 完整机制回归：r4（seed 1 / GPU 0）与 r5（seed 0 / GPU 3）各 22/22 通过；全部六件单件抓取与 Inside、双柜分放整目标、两柜未关反例和最终 90 步稳定性均通过。r5 放置有明显耗时，GPU 不同，未隔离 seed 与主机/GPU 条件的影响。两个 seed 不是留出布局。[全部探针录像和结果](http://10.130.140.115:8769/groceries_v2_readiness_20261020_index.html)。
- 复用：新物体加入已有容器后，对所有原有和新增物体检查真实初态谓词、静置、搬运与回滚。容器外观能装下不等于原生 Inside 成立。任务不能通过重写初态标签掩盖几何缺陷。
- 边界与下一步：较高袋子的逐件 RGB 可发现性、携物过门和留出布局待验证。探针、录像与模型成绩分别报告。

## 2026-10-21：GROC-001 至 GROC-005 的完整自主回归

- 冻结回合：`../experiments/groceries_v2_robust_gpt61sol_low_20261021_r1`；任务包、场景和实例同上，seed 0；BDDL SHA256 `b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1`，独立 overlay `groceries_v2_scene_overlay_20261021_robust_r1`、袋高 1.2、`explicit_navigate_to_only_v1`。模型 `gpt-6.1-sol / low`，规划 skill 开启、无 demo，原指令与预算不变。
- 结果：正式 `finish(achieved)`，独立原生自定义 BDDL **12/12**；`official_task_success=null`。105 次 act/look、13586 个 env.step、模型执行墙钟约 80.6 分钟；连续视频 13692 帧、456.4 秒。
- GROC-001/005：两只袋都带原来的三个食品从车库进入厨房；第一袋在冰箱旁放下，第二袋先在车库暂放关后备箱，再携物过门。`carry_diagnostics.jsonl` 记录袋 81 起抓 env_step 4308，袋 82 起抓 8967、暂放后重抓 9063，各有三个载荷。本实例较高袋子的携物回归通过，不外推到其他门型。
- GROC-002：四件冷藏品与两包饼干均满足当前原生目标，两袋归位，后备箱、冰箱及两个柜关闭。当前模型把两包饼干放同柜，是被允许的解；分柜原生正例仍由 2026-10-20 探针覆盖。
- GROC-003：本轮逐件清点成功，六件各自独立抓取并入库，未重现旧漏件；未修改模型输入以告知准确数量，未设评分驱动的结束守卫。旧失败保留，不以单次成功宣布普遍修复计数能力。
- GROC-004：`demo_audit.json` 的 `all_paths_from_explicit_navigate_to=true`，调用对齐、公开决策、RGB 选点、视频与底盘高度均通过，整体 `passed=true`。取消自动接近后已有完整自主回合证据。
- 中途失败：15 次 `out_of_reach`、1 次 `fixed_object`、2 次 `navigation_unreachable` 均保留，均未修改运行中的 harness 来绕过。第二袋上台面后袋壁遮挡，模型移到地面恢复视角；牛奶暂放被冰箱门挡住，经开关门与换位找回。它们是这条轨迹中的恢复成本，目前未证实为新基础设施 bug。
- 复用检查：容器的装袋初态、携物过门、暂放后的可发现性、每件独立抓放、合理容器选择与显式导航审计要分别记录。成功抓取反馈中的类别是允许的有界状态通道，不能称为没有任何状态反馈的纯 RGB。
- 边界：这是开发实例的一次成功，不是成功率或留出泛化结果；仍需留出布局和重复运行。定义、袋高与协议均较旧回合变化，不能把 9/11 到 12/12 的变化归因于单一修复。
- [审阅、完整录像与审计](http://10.130.140.115:8769/groceries_v2_robust_20261021_index.html)，首页、五视角页与浏览器视频 HTTP 200。逐件事件索引及命令见该回合 `experiment_record.md`。

## GROC-006：成功回合中的重复恢复与过期 skill

- 任务/版本：2026-10-21 的 `groceries_v2_robust_gpt61sol_low_20261021_r1`，双柜 BDDL SHA `b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1`，场景 `house_double_floor_lower` / 301 / seed 0，袋高 1.2，显式导航协议；105 次 act/look、12/12，原记录不变。
- 失败子过程：第二盒牛奶在门边暂放后被遮挡，动作 67–92 共 26 次操作、约 809 秒；其中 79–80 为相邻开→关，无中间取放。第二袋上台面后袋壁挡住内容，56–61 又改放地面。它们是已恢复的局部困难，不能据此称整任务失败或直接扣掉全部动作。
- 已确认：旧 pick-and-place、material-actions 的“grasp 自动接近”与当前执行器矛盾；要求读取私有载荷数量也不符合实际反馈。两个 update_plan 拒绝分别来自缺顶层 reason 和 evidence ID 拼接。技能接口说明有缺口；它对 15 次超距、门遮挡成本的具体因果贡献未隔离。
- 修复层：仅 policy skill 文本。visual-exploration 加入四路选点、接近终止和地面小物体视角规则；pick-and-place 新增门遮挡/暂放参考并拆分特殊案例；long-horizon-planning 加入局部依赖、逐件证据、路线与暂放成本、计划格式；相关 visual/failure/material 入口同步。未更改 harness、场景、评分或预算。
- 验证：skill 加载与规划接口 6 项测试通过；新参考均可由 SkillLibrary allowlist 读取。候选 c19b2601 的完整自主回合为 120 次 act/look、26 次失败、独立 11/12；漏关一个吊柜，模型如实 finish(blocked)。六件与两袋均到位，调用、路径和录像检查通过；整任务及整体 audit 未通过。未证明动作减少或遮挡已解决。完整分析、hash 和[回放](http://10.130.140.115:8769/groceries_skill_iteration_20261021_index.html)见[skill 动态优化记录](skill_iteration_20261021.md)。主文“先开目的地”与参考中的门遮挡例外存在冲突，下一版修正；不把全部失败归因于这一句话。
- 复用：新任务先验证内容可发现、开门后的暂放位置、显式导航与技能描述一致；优化时分别报告任务成功和恢复成本。必要导航、终态核查、安全恢复不得直接当作冗余删除；用同配置回合与留出任务检验规则。

## GROC-007：低矮物体接近被当作地板航点

- 状态：局部分类错误修复与真实仿真 6/6 已证实；完整自主回归均失败，尚未建立整任务收益。原完整回合使用旧执行器，原 11/12 保持不变。
- 任务/版本：`groceries_v2_skillv1_gpt61sol_low_20261021_r1`，carrying_in_groceries / house_double_floor_lower / 301 / seed 0；双柜 BDDL SHA `b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1`，袋高 1.2，协议 `explicit_navigate_to_only_v1`，skill `c19b260155167d12ec8a2023aabcef03969519acb215c11a2a9984681905be58`。失败子过程为第二袋包装食品接近后的可见性恢复。
- 原始证据：第 91 次动作 event-00302 是 navigate_to，选择 `rgb-00091-back` 的 `[0.13, 0.9]`。离线查看原图，红圈位于蓝白牛奶盒表面；标注图保存在相邻 `_source/motor91_selected_pixel.jpg`，没有发送给模型。`grounding_diagnostics.jsonl` 记录命中 `z=0.0692355633`、`object_resolution_required=false`；同次 `navigation_plans.jsonl` 记录 `floor_waypoint=true`、`visual_standoff_m=0.2`。第 92 次动作说明接近后地面物体被裁切，改选观察位置。
- 已确认的规则：`omnigibson_rgb.py::_navigate` 用 `selected is None` 且点高与底盘差小于 0.12 米判断地板航点；`gt_navigation.py::navigation_approach_settings` 对该分支返回 0.2 米、无终点可见性要求。该次导航未进行物体解析，因此低矮物体像素也进入了此分支。
- 复现：`test_visual_manipulation_navigation.py::test_low_object_navigation_is_not_a_floor_waypoint` 通过真实 execute_visual→_navigate 分类路径、合成的同像素所属物体 fixture，在修正前稳定失败 True!=False，地板与较高目标对照通过。红灯源码与输出位于 `../experiments/groc007_navigation_classification_20261021_unit_before/`。这是分类边界复现，不能单独证明画面裁切。
- 真实仿真：`groc007_navigation_classification_20261021_probe_r1` 在同一环境中以旧版/新版执行真实 RGB 导航。旧版将低矮牛奶盒当作地板，接近后不可选；新版按物体处理，仍可选并抓取成功，核心 4 项检查通过。探针用私有几何布置物体、重置机器人及投影选点，不是原回合完整状态重放或自主成绩。r1 因地板对照点本来不在相机视野而整体失败，全部证据保留；r2 只将该对照点改到可见地面，6/6 检查全部通过，实际地板仍走航点分支，较高冰箱目标保持物体分支。不能将 r1 写成全部回归通过，也不把原回合所有裁切归因于同一分支。
- 排除过的粗假设：此前 22 条计划中 9 次非地板接近终点均距所选导航点不超过 1.4 米，不支持“所有物体导航都停在操作距离外”。选点和操作目标可能不同，不能仅凭一次 out_of_reach 推定导航距离上限冲突。
- 修改层与范围：仓库执行器 `omnigibson_rgb.py` 的导航也解析同一像素射线上的首个可见物体，只有真正 floor/floors 表面且高度接近底盘时采用地板航点。深度落点不替换，抓取距离仍 1.4 米，没有自动接近；未改上游仿真器、任务或评分。provenance 改为 `explicit_navigate_to_only_v2`，记录 navigation_object_lookup=true 与 selected_visual_floor_only。新旧协议成绩分开；后续不能把执行器和 skill 同时变化归为纯提示增益。
- 单元回归：导航意图、地板过门、RGB 操作不自动行走、GT 路径与私有信息边界共 41 项、7 个子测试通过。绿色源码记录位于 `../experiments/groc007_navigation_classification_20261021_unit_after/`；真实地板/较高目标对照已通过；独立过门与完整自主回合仍待验证。
- 复用检查：新任务包含薄片、小包装或地面物品时，分别验证“点中地板作为落脚点”和“点中物体作为后续操作目标”。物体离地很低不等于像素选中了地板；导航成功也不等于下一张 RGB 仍可选中物体。

- 2026-10-21 受控自主回归：`groceries_v2_navv2_skillv1_gpt61sol_low_20261021_r1` 已启动，沿用失败回合的 c19b2601 skill，只改变导航分类执行器，模型仍为 gpt-6.1-sol / low。在独立 execution 源码副本运行，保留仓库中的候选 v2，不修改运行中的源码或技能；启动记录位于同名 `_source/launch.json`。结果待完成，不能先报告成功。[局部真实仿真对照及失败录像](http://10.130.140.115:8769/groc007_navigation_20261021_index.html)，首页与两段视频均 HTTP 200。

- GROC-007 范围补充：新增的导航像素所属物体也传给既有 `_approach_visible`，其 `ray_hits_selected_object` 对同物体射线命中的接受可能改变非地板目标的候选位置。因此新旧执行器全回合比较衡量组合导航 grounding 改动，不能称为仅改地板谓词的消融。低矮物品局部复现仍直接验证分支与后续可选/可抓差异；源码复查本身不证明此可见性行为是新 bug。此说明不更改冻结执行器，同协议 skill 比较四轮仍使用相同源码。

## 2026-10-22：GROC-003 / 006 / 007 的冻结重复回归

- 完整矩阵为导航协议 v2 下的 v1/v2 skill 各两回合：独立目标依次 5/12、9/12、8/12、4/12，动作 120、106、120、80；两版均 0/2 成功、平均 6.5/12。版本、逐项漏件、原始事件、RGB、录像与成本见 [skill 迭代记录](skill_iteration_20261021.md) 和 `../experiments/skill_iteration_20261021_controlled_plan/matrix_result_review.json`。
- GROC-003：v2-r1 报告 achieved，但第二盒奶、第二包饼干及第二袋 NextTo 不成立。类别层面的抓取/放置记录和“全部完成”的文字不能替代逐件证据；这是独立评分发现的错误完成声明，不能把 106 次较短轨迹称为更有效。v1-r2 的第一袋 NextTo 也未保持，尽管模型文字称两袋已归位；保留模型声明与真实终态的区别。
- GROC-006：门遮挡、路线恢复与选点失败仍发生。v1-r1 第一袋动作 36 抓取、43 暂放，番茄 48 抓取、54 入库；动作 26/27 的原图与私有 ray 显示墙体选点，不能当成过门修复重现失败。v2-r2 在 80 次动作受阻结束，导航失败需继续诊断，不能仅凭模型说“无路”判断仿真器故障。
- GROC-007：四轮显式导航、RGB/公开决策、路径记录、底盘高度与连续录像检查均通过，但任务均未成功。局部探针证明低矮物体分类问题已修复，不证明整任务更鲁棒。新增所属物体解析也影响原有终点可见性检查，因此新旧运行时全回合差异不能单独归因于地板谓词；尚未用同状态差分复现确认新的导航回归。
- 已完成的记录层变化：增加四轮逐项独立结果与失败成本，保留原始失败和实际 skill/runtime hash；未在矩阵中途修改任务、评分、场景或预算。[完整四轮回放](http://10.130.140.115:8769/groceries_navv2_skill_matrix_20261021_index.html)，首页、四个五视角页与四个视频均 HTTP 200。
- 复用：构建多件入库 benchmark 时，同时保留错误完成声明、较早 blocked、物品漏计和暂放后终态漂移；动作少但任务未完成不是优化收益。导航失败分别核查 RGB 所选表面、规划候选、动态障碍与实际运动，再决定修 policy、harness 或场景。

## HOT-001：热饮迁移回归的动力学初始化配置失败

- 任务/版本：`prepare_and_serve_hot_drink` / house_single_floor / 301 / seed 0，冻结 high_level_visual.bddl SHA `4be5469c5eff25f59504c90c4d78d04312057353ae35a0bf007012c2d0a9b915`，导航 v2 + high_level_hot_drink_state_v1；配置模型 gpt-6.1-sol low，但失败发生在模型控制器启动前。
- 失败：`hot_drink_navv2_skillv1_gpt61sol_low_20261022_r1` 的 startup_stages.jsonl 在 construct_evaluator_and_environment 阶段明确记录 `Failed to initialize water system. Please set gm.USE_GPU_DYNAMICS=True.`；summary 的 controller_exit_code、正式 finish 和 video 均 null，没有模型动作或录像。v2-r1 因批次完整性关卡未通过而未启动。
- 已确认原因：沿用杂货的 `--cpu-dynamics` 使初始化前就关闭 GPU dynamics，而热饮保存场景的水系统仍要求 GPU 初始化；高层视觉 BDDL 省略水粒子目标不等于加载场景无需初始化水系统。
- 修复层：仅实验启动参数，移除 `--cpu-dynamics`，沿用已有 runner 对热饮设置的 `OG_SWITCH_TO_CPU_AFTER_WATER=1`（GPU 初始化后再转 CPU）。没有修改模拟器、执行器、任务或评分。新建 v1-r2 和 v2-r2 目录，原失败及两个原计划保留。
- 状态：修正后的批次已启动，监督初始 PID 775327，状态见 `../experiments/hot_drink_skill_transfer_20261021_frozen/transfer_status_20261022_r2.json`；v1-r2 已通过 bridge_ready 并开始模型动作，真实初始化验证通过；完整自主任务结果仍待完成，不能把启动修复当作任务成功。此启动失败单列为基础设施/实验配置失败，不计作模型任务失败。[启动失败记录](http://10.130.140.115:8769/hot_drink_skill_transfer_20261022_startup_failure.html)，HTTP 200，无录像的原因明确标注。
- 复用：跨任务复制启动命令前，核对场景系统的初始化前提与后续动力学模式；冻结 BDDL 和技能还不够，物理初始化流程也要一致。场景未就绪时停止批次，保留失败并用新目录修正，不自动覆盖或无限重试。

- GROC-003 后续定位：v2-r1 的 event-00100→00327→00353 整份 DAG 替换丢掉了尚未解决的袋内检查义务，类别节点后来全部 done。成功抓取实际只有 milk79（动作 82）、cookie85（91），最后重新归位的是 bag81（103→106），与 9/12 的三个漏项吻合。相邻对象事件索引保存于 `controlled_plan/skillv2_r1_object_event_review.json`。候选 v3 仅改 planning skill 的整份计划 reconciliation 合约，保留未决义务与容器身份；实际加载与单资源差分通过，自主效果尚未验证。不能把静态文档改动宣称为计数问题已修好。

## HOT-002：热饮迁移回归末段托盘未上桌（未解决）

- 任务：prepare_and_serve_hot_drink / house_single_floor / 301 / seed 0；high_level_visual.bddl SHA `4be5469c5eff25f59504c90c4d78d04312057353ae35a0bf007012c2d0a9b915`，high_level_hot_drink_state_v1，导航 v2，skill v2 bundle `26f420d34792c9061eff2bb600e2749908b5d1a768fe2f4f1936b40a1fc773a3`。回合 hot_drink_navv2_skillv2_gpt61sol_low_20261022_r2，模型 gpt-6.1-sol low。
- 终态：10/11，tray_on_table=false，120 动作后正式 blocked；协议/控制器/录像检查通过，保留为模型任务失败。源码、任务、评分未改。
- 证据：动作 44 已计划取托盘，55–71 多次柜前接近、选点和视角恢复；113–118 再次返回与调整视角，119 首次成功 grasp category=tray，120 导航后无预算放置。可成功抓到托盘，因此不能把此回合直接归为抓取器故障或不可发现的任务；早期恢复是否有执行器问题仍未确认。结构化事件见 frozen/hot002_tray_failure_review.json。
- 改动层：本次仅记录诊断，无运行时/skill 修复。现有 visual-exploration、pick-and-place 与 staging-and-doors 已含改善视角和复用落脚点指导，不因一次失败重复堆叠规则。
- 下一步/复用：对柜内薄物，分开记录首次看见、首次成功类别确认和最终送达；保留从发现到送达的全链路成本。若要声称某视角或动作冗余可删除，需前后 RGB 与同状态对照，不能由文字摘要倒推。构建 benchmark 时验证可见性及关键链，但保留有效预算内的规划失败。
- 对照边界：同冻结协议 skill v1 一次回归 11/11，v2 一次 10/11，只是负面迁移证据，不足以估计可靠总体差异。[两轮审阅/连续录像](http://10.130.140.115:8769/hot_drink_skill_transfer_20261022_index.html) 已核验 HTTP 200。

## GROC-008：导航前后目标身份切换（局部确认，策略未修复）

- 任务：两袋杂货 v2 / house_double_floor_lower / 301 / seed 0；BDDL SHA `b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1`。回合 groceries_v2_navv2_skillv2_gpt61sol_low_20261022_r1，导航 v2、skill v2、gpt-6.1-sol low；完整回合仍在运行。
- 证据：60 grasp 和 61 navigate_to 的私有像素归属均 milk83；62 grasp 与 63 navigate_to 的私有归属变为 cheese84，公开决策仍称牛奶。62 抓取返回 out_of_reach，随后 64 抓取成功的公开类别为 swiss_cheese，模型据此改称奶酪并运输；69 place_inside 成功。逐项证据保存在同名 source/food_identity_60_63_interim_review.json。实例身份仅用于事后诊断，未进入模型输入。
- 原因边界：确认存在相机/落脚点变化后的视觉身份混淆；不能将 61→62 当成同一物体“导航后仍不可达”的执行器反例。尚未复现可归因于 harness 或模拟器的缺陷。成功类别反馈帮助纠正该次身份，但不证明整任务无漏件。
- 改动层：只新增审阅记录，没有修改当前 skill、执行器、场景或评分；v2/v3 冻结比较继续。根因防治仍待完整结果及重复轨迹，不宣称已修复。
- 复用检查：构建小包装或相似物品任务时，保留导航前后四相机 RGB 与私有像素归属供离线核对。判定连续操作失败前先确认两次选择确是同一对象；模型文字不是归属证据。成功 grasp 类别属于已声明的有限反馈通道，不追加实例 ID 或隐藏目标列表。


- GROC-003 / GROC-008 后续完整证据（2026-10-22）：新前瞻矩阵 v2-r1 正式 achieved，独立 10/12，缺 milk79 Inside 和 bag82 NextTo；两枚饼干已成功存放，不能概括为小件抓不起。milk83 在 98 抓取、100 存放，milk79 未单独成功抓取；00328 明确保留第二盒奶，00331 计划弱化该义务，00378 冷藏品整体 done 后误报完成。计划更新与计数丢失吻合，但尚未证明 v3 能修复；bag82 终态关系退化的具体触发动作未定位。116 动作、20 失败；4 条服务重连记录单列，回合有效结束、录像通过。记录层改动，无 skill/harness/仿真器修复。复用时分别检查“单件成功”“整类齐备”“容器取空”“最终关系保持”，不得用某一单件存放成功覆盖整个类别。完整证据 `../experiments/skill_v3_controlled_20261022/cell1_result_review.json`，审阅 `groceries_skillv3_20261022_index.html`；第二格 v3 已接续，效果待验证。


- GROC-003 后续 v3 反例：`groceries_v2_navv2_skillv3_gpt61sol_low_20261022_r1`，同 BDDL/导航v2/301/seed0/gpt-6.1-sol low，bundle5fd7031b…，81动作后误报achieved，独立10/12，缺milk83和bag82相邻。3次整份计划更新未删任何未决ID，最终271仍将“全部冷藏食品”收窄为“全部已见冷藏食品”，用关冰箱249作为完成证据。milk79与两枚不同饼干处理成功不等于整类齐备；候选v3仍未解决整体核查。当前仅新增失败记录，未修skill/执行器/仿真器。复用时审查量词、范围限定词和证据类型，不能仅检查ID不消失就认定义务保留。关系退化与额外洗碗机关闭动作分别待诊断，不混成同一根因。结构化证据 `../experiments/skill_v3_controlled_20261022/cell2_result_review.json`；两轮后续重复按原冻结配置继续。


## GROC-009：非指令导致的洗碗机门外观变化（未定位，未修复）

- 任务/版本：groceries_v2_navv2_skillv3_gpt61sol_low_20261022_r1，同双柜v2 BDDL b38c56b6…、301/seed0、导航v2、skill5fd7031b…、gpt-6.1-sol low。
- 证据：人工检查四相机 contact sheet，capture13 front和16 front显示洗碗机门闭合，capture81 left显示已打开，81 close后capture82 left闭合。成功open的私有选中对象仅室内门、冰箱和吊柜；没有洗碗机显式成功open。第81步close确选中dishwasher_ynrhuk_0。结构化证据与图在对应source/dishwasher_state_change_review.json、dishwasher_extra_action81_review.jpg。
- 结论边界：存在早期与末段非指令外观变化；81虽不属于任务目标，但具有恢复原状作用，不能直接称无意义冗余。尚无精确首次变化、joint轨迹和接触证据，不归因为某次碰撞、求解器或模拟器bug。不同视点图像不能替代同状态因果回放。
- 改动层：只记录离线证据，没有改skill、harness、仿真器或评分。冻结矩阵继续；后续串行私有探针先捕获首次变化的关节/接触并建立可判红复现，再测试修复。不能据此直接放宽碰撞或固定所有门。
- 复用：新benchmark保留任务外可动门的前后图像及必要状态取证；将额外整理、状态异常恢复与可删除重复动作区分。不能由模型“搬运期间打开”的文字直接推导物理原因，也不把恢复原状动作从成本中隐去。


## GROC-010：同一小物体导航后仍超距的边界案例（候选，未复现修复）

- 任务：前瞻v2/v3矩阵首两格，carrying_in_groceries/v2_design/house_double_floor_lower/301/seed0，同BDDL b38c56b6…、导航v2、gpt-6.1-sol low。完整离线索引 `../experiments/skill_v3_controlled_20261022/cells1_2_adjacent_nav_grasp_identity_review.json`，私有对象身份从未反馈模型。
- 筛选：7组相邻“navigate_to成功→grasp out_of_reach”，4组选择的对象不同（含正常从袋接近食品等，不自动算身份混淆），其余3组同一非空owner。v2动作77→78为cookie86、95→96为milk83，v3动作63→64为cookie85。
- 测量：3组导航 visual_standoff=1.4、floor_waypoint=false、max_target_distance=null、可见性和动态碰撞检查开启。实际最后底盘到随后抓取落点3D距离分别1.43426、1.41622、1.44144米；前后命中表面点差分别0.03645、0.02599、0.01304米，不能把这个差值当作物体运动。实际位置与规划终点吻合。
- 边界：底盘到点的描述性距离不能替代执行器的reach原点/度量，也不证明附近存在安全且可见的更近落脚点。证据支持针对边界候选建立复现，不支持宣称导航或抓取根因已定位；尚无完整红色可运行复现。保留GROC-008的身份先核对要求，不把所有out_of_reach合成一个bug。
- 改动层/下一步：仅离线记录，冻结矩阵不改skill、harness或仿真器；批次完成后在实际调用链重现上述同owner、同表面、终点和reach判定，再讨论候选筛选与操作范围合约。不能直接增加抓取距离、放宽碰撞或暗中自动接近。

- GROC-010 度量补充：核对两格实际归档执行器SHA c8258d21…，操作前置gate确为机器人底盘到新选表面点的XY范数，阈值1.4米，发生于_demo_reach之前。按最后导航底盘记录计算，三例XY分别1.43416、1.41473、1.44133米，超过该阈值3.42、1.47、4.13厘米；与早期gate拒绝相容。公开boundary把多个内部out_of_reach消息归一，故不能由公开文案确定具体抛错位置。尚未获取精确grasp时底盘回放或证明更近安全终点，仍不是完整红色复现。只新增离线合约核对 `../experiments/skill_v3_controlled_20261022/cells1_2_manipulation_reach_contract_review.json`，未修改运行代码。

- GROC-010 异常来源已核实：进一步读取三个既有失败的private_executor_error，均精确匹配XY前置gate的内部文案，而非_demo_reach接触失败；公开文案仍不足以区分，私有记录可区分。第三格v2-r2动作22导航→23同盒奶抓取也命中该gate，24再次导航实际行走2.22036米，25成功抓取milk79，27成功存放。后续有效路径的存在不等于早先同状态反事实已验证，仍待完整红色回放。证据 `../experiments/skill_v3_controlled_20261022/groc010_private_exception_origin_review.json`；仅离线诊断，无运行代码/skill/仿真器修改。

- GROC-003 / GROC-008 第三格中途证据（v2-r2，仅截至72动作，非终态）：第二袋47和51抓取时私有contained_rigid_objects均3；65成功单抓cookie86并67入柜。68接近后模型称未见余物，69关柜；70再查又称见黄色食品，72试图抓该食品实际成功类别paper_bag，私有owner为bag82且仍含2件物品。模型随后改称“袋内未见独立食品实体”并开始搬袋。说明类别纠错不等于袋底已核清，不能把一次错抓袋壁当作空袋证明；尚不据此判断终态漏件或新根因。证据 `../experiments/groceries_v2_navv2_skillv2_gpt61sol_low_20261022_r2_source/second_bag_identity_through_action72.json`。仅离线记录，不向运行agent泄漏内容计数，不改冻结skill/执行器/场景。

- GROC-003 第三格正式终态：v2-r2为9/12误报完成，缺milk83、cookie85和bag81相邻。72的仍含2件证据已由终态漏件补强，最终253用旁放249证明取空并将单枚饼干存放推广为整类完成。0个未决ID删除仍不代表语义义务完整。75预算动作、9次接纳动作失败，另有1次参数拒绝；自动报告76是请求尝试数，已单独标注统计口径问题。bag81关系退化触发待定位；第四格冻结对照继续，未修skill/执行器/仿真器。完整结构化证据 `../experiments/skill_v3_controlled_20261022/cell3_result_review.json`，实际审阅/连续录像均已核验HTTP200。

## BENCH-001：成本报告把参数拒绝计作预算动作（离线分析器已修复）

- 触发任务：groceries_v2_navv2_skillv2_gpt61sol_low_20261022_r2，杂货v2/house_double_floor_lower/301/seed0，BDDL b38c56b6…，skill26f420d3…，导航v2，gpt-6.1-sol low。原始run.json消耗75动作；自动成本报告76，并将look计为3次而非实际2次。
- 复现：原始event197把look作为act primitive提交，198由schema拒绝，无actions_used；有效look随后成功。最小日志经真实analyze路径测试，修复前2失败：2个预算动作+1拒绝计为3，未应答请求计为1。命令 `PYTHONPATH=src /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python -m pytest -q tests/test_workflow_efficiency.py`，修复后2通过。
- 确认原因/修改层：scripts/analyze_workflow_efficiency.py把所有act/look请求追加为actions。现按返回actions_used确认预算动作，新增action_requests、rejected_motor_calls、runtime_actions、一致性与未分类响应检查；保留全部timeline和请求延迟，分别记录请求序号number与预算序号action_number。执行失败仍消耗预算；参数拒绝与未应答独立报告。未修改机器人运行时、simulator、skill、任务定义、评分或冻结实验副本。
- 原日志回归：前3格重新分析到 `../experiments/skill_v3_controlled_20261022/costs_through_cell_3_counting_v2.json`。预算/请求计数分别116/116、81/81、75/76，均与run.json一致，0个未分类成功响应；第三格9次执行失败+1次参数拒绝。断言原评分、env步数、距离、全部请求条目数、请求总耗时均不变。历史completed_through_cell_3.json保留，第四格冻结分析器仍为旧版，批次完成后另生成新版报告。
- 复用/限制：benchmark必须分开请求成本、动作预算成本和环境步数。未应答请求无法由日志单独确认预算消耗，报告为未应答并核对runtime计数；旧协议若无actions_used须显式显示未分类/不一致，不默认计为0成本。此次仅证明成本口径修复，不改变任务失败或证明policy效率提升。

- BENCH-001 历史回归补充：六场旧杂货/热饮完整日志均与runtime动作数对齐，0个未分类成功响应。旧杂货v2-r1（20261021）实际105预算动作、106请求尝试，20次执行失败另加1次stale_observation前置拒绝（event191），不是106预算动作/21执行失败；其9/12及误报完成结论不变。其余五场预算/请求分别111/111、120/120、120/120、120/120、80/80。保留旧报告，修订输出 `../experiments/skill_v3_controlled_20261022/historical_cost_counting_v2_validation.json`。

## EXP-HOT-20261022-WALL：确认配置与旧 runner 的整回合限时不一致

- 任务：prepare_and_serve_hot_drink / house_single_floor / 301 / seed 0,11,22；high_level_visual.bddl，11 项高层评分；新批次启动前发现，无自主失败子目标。
- 复现：run_demo_trial.py 强制正 --timeout，向 vision_cli 传 wall-seconds，并向 controller 和 subprocess.run 传同一总时限；不符合用户确认的无任务墙钟截止。为配置契约问题，不归因于 agent。
- 修改层：runner、controller、Budget、vision_cli、VisionHarness 与 bridge 私有健康监督。新增 --unbounded-episode，以 null 表示无墙钟限制，保留动作/调用/环境步预算。900 秒初始化与180秒预算耗尽/关闭收尾独立 watchdog；历史默认模式保持。
- 验证：PYTHONPATH=src，behavior Python 运行 test_demo_provider、test_episode_deadline、test_clients、test_skills_loop、test_long_horizon_planning、test_workflow_efficiency，共41项及28子测试通过；新增 test_unbounded_episode 两项通过。实际自主回合待运行，不把测试当作仿真收益。
- 证据与预防：experiments/hot_drink_optimization_20261022/baseline_manifest.json 和 plan.json。每次启动应对照确认配置核验控制器、harness、外层进程三层限制；分别记录任务预算与初始化/收尾 watchdog。保留所有尝试及失败录像，不能静默把大数时限写作无限制。

- GROC-009 新对象/更窄区间（第四格v3-r2，仍在运行）：人工检查rgb13-right冰箱正面闭合，13导航后rgb14-front显示冰箱门打开及内部；间隔仅event58→60的navigate_to。此前成功open目标为汽车car_ssxsje_0和室内门door_bexenl_0，没有冰箱open。将无指令门变化定位到导航13区间，但没有首次joint变化、接触或反事实回放，不能据此断言碰撞/载荷/模拟器根因。此时手持bag81。对应source/fridge_articulation_during_nav13_review.json与fridge_before_explicit_open_through13.jpg；与洗碗机案例共同进入设施调查，未改当前skill/执行器/场景/评分。

- GROC-003 第四格v3-r2中途反例（仅至35步，非终态）：bag81初抓10含3件；23、31、34再次抓袋均仍含1件。番茄19抓/21入，奶酪28抓/30入，但尚无牛奶单件抓取存放。模型将袋子32放桌、33近查、34重抓后35旁放，称“第一袋清空归位”并返回车库。调整至桌面及额外接近未使这次判空可靠；仍需后续轨迹和终态确认，不提前给漏件评分或认定物体不可发现。私有内容计数未反馈agent，source/first_bag_content_through_action35.json保存具体owner与载荷快照，冻结配置未改。

- GROC-003 / GROC-008 第四格后续中途证据（v3-r2，仅至60动作）：56独立抓cookie85、58入吊柜后，59返回检查第二袋；60试图抓白色食品，实际公开类别为paper_bag，私有owner为bag82且仍含2件。模型明确纠正为误抓纸袋，下一请求重新放袋改善视角，尚未在此处宣称取空。保留这次恢复与第三格误判的区别，暂不推断终态或物体不可发现。证据 `../experiments/groceries_v2_navv2_skillv3_gpt61sol_low_20261022_r2/source/second_bag_misselection_action60.json`；未向运行agent反馈私有信息，未改变冻结配置。

- GROC-003 / GROC-008 第四格60误抓的恢复证据：61重新放袋，62/63改变观察位置后，64从rgb64-back独立抓到milk83，公开类别carton_of_milk与私有owner一致。这一合法动作链说明该件在本次轨迹中可以发现并抓取；不能由先前单帧遮挡推断其不可操作。尚未完成整轮，不据此认定v3效果或全任务成功。证据 `../experiments/groceries_v2_navv2_skillv3_gpt61sol_low_20261022_r2/source/second_bag_recovery_through_action64.json`；未改运行配置。

- GROC-003 第四格晚期自纠正（至83步，非终态）：82关冰箱后，83模型在rgb83-left发现袋内蓝紫包装，明确撤回空袋判断并独立抓到milk79（公开carton_of_milk与私有owner一致）。第一袋此前34仍含1件、后被标done的遗漏义务在本轮重新被视觉发现；不能提前将中途误判当成终态漏件。证明milk79在该轨迹允许观测/动作下可发现抓取，但尚无最终入库与全任务成功证据，亦不证明v3因果收益。证据 `../experiments/groceries_v2_navv2_skillv3_gpt61sol_low_20261022_r2/source/late_milk_recovery_action83.json`。

- GROC-003 第四格独立评分已结束（v3-r2，后处理审计尚待）：89动作，11/12误报achieved；六件食品Inside及全部关门目标均真，唯一缺项为bag82 NextTo fridge。81旁放返回成功，后82关门、83取milk79、84暂放、85开门、86重抓、87补入、88关门、89退后核查；关系退化首次触发尚未定位，不能直接归因为关门。第一袋晚期漏件在此回合已补齐，不能继续写成终态漏奶。保留袋子关系保持为设施调查候选；证据 `../experiments/skill_v3_controlled_20261022/cell4_scoring_review_pending_audit.json`。v2两轮0/2、均值9.5/12；v3两轮0/2、均值10.5/12，均为开发固定实例小样本，不据此宣称效率提升或推广v3。

- 第四格后处理核验完成：14项独立完整性检查全通过，89预算动作与89请求一致，7次执行失败，无模型服务错误；任务audit失败仍为11/12未完成。审阅页/连续录像实际HTTP200。正式结果与完整实验记录已落盘 `../experiments/skill_v3_controlled_20261022/cell4_result_review.json`、对应trial/experiment_record.md。旁放记录env_step11464明确next_to_verified，终态bag82相邻假，首次退化仍待回放，不将根因提前归于关门。

- GROC-003 关系保持复现入口已准备：`tasks/carry_in_and_put_away_groceries/v2_design/probe_bag_next_to_retention.py`，通过现有run_readiness_probe.py隔离运行；空袋私有预置后使用真实_checked_place_next_to及_ideal_state_action，按旁放/关门/再开/再关逐阶段保存NextTo、袋/冰箱pose与AABB、门关节和录像。选择点取第四格81实际grounding。此为简化设施探针，非原回合精确重放、非自主得分。py_compile与git diff --check通过，仅证明静态检查；尚未运行、未得到红色复现，不声称根因/修复。GPU0另一热饮实验PID3258219仍运行，串行资源释放后再启动。

- GROC-003 探针已实际排队：`../experiments/groc003_bag_retention_20261022_queue/queue_plan.json`冻结src/scripts/任务副本及135个Python文件哈希；队列进程3318521核验仍存活，等待热饮仿真PID3258219及其启动时间匹配进程退出后自动启动私有probe_r1。运行前校验冻结源码；探针输出/录像/清单独立保存，不覆盖自主回合。当前状态waiting_for_simulator，尚无复现结果，不能写成红色测试或修复。

- GROC-010 几何最小红色复现已实际运行三次、每次同样失败：`PYTHONPATH=src /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python ../experiments/groc010_planner_repro_20261022/reproduce_planner_reach.py`。真实plan_navigation和rig投影在空旷0.05米网格选终点(2.4,2.0)，距低物体(3.82,2.03)为1.420316866米；同网格(2.45,2.0)可直达、投影满足原margin，距离1.370328428米。断言导航后距离<=现有1.4米操作gate实际报AssertionError，结果与源码哈希保存red_reproduction.json。此复现覆盖规划/投影几何失配，不包含真实动态障碍、mesh可见性、抓取执行或原回合精确状态；运行修复仍待该层验证，不扩大操作范围或降低安全margin。

- GROC-010 候选执行器修复（尚未仿真验收）：新增真实execute_visual→_navigate→plan_navigation调用回归，修前实际失败1.420316799>1.4；修后49 passed、7 subtests passed（visual_manipulation_navigation、gt_navigation、rgb_boundary）。仅omnigibson_rgb.py公开物体导航传max_target_distance=1.35，为原1.4米操作gate留5厘米余量；纯地面上限0.5和legacy in-action策略不变，碰撞/可见性/操作范围均未放宽。源码哈希和命令见 `../experiments/groc010_planner_repro_20261022/candidate_fix_validation.json`。仍待真实场景可达性、抓取与自主回归；严格终点可能增加狭窄场景不可达，超过5厘米的新选点漂移也不保证成功，不宣布GROC-010已解决。袋子队列冻结旧执行器用于独立关系复现，不混入本候选改动。

- GROC-010 真实RGB导航/抓取对照已准备并排队：`probe_navigation_reach.py`复用已有低物体探针的私有位置重置与精确可见owner投影，旧执行器c8258d21与候选74bb8c80分别运行真实导航和fresh RGB抓取，记录新选点距离、实际抓取错误、物体可见性，并保留纯地面/高物体控制。基线若未重现out_of_reach只报告未复现，不能当成修复证据。`../experiments/groc010_navigation_reach_20261022_queue/queue_plan.json`冻结完整src/scripts/任务副本、基线与哈希；队列PID3395303实际存活，等待袋子队列3318521退出后串行启动。仅py_compile通过；仿真尚未运行、未得结论，不计入自主成功。

- 探针调度修正：检查发现热饮baseline监督跨回合持有 `../experiments/sunyujie_simulator_gpu0.lock`，仅等待首个sim PID不足以保证串行。两个探针尚未启动时，保留原queue脚本/计划/状态，重启等待进程并加入同一flock；新PID3517128、3517129，后者等待前者。探针冻结源码和配置均不变；修正证据 `../experiments/groc003_groc010_gpu_lock_queue_update_20261022.json`。这是本地实验调度修正，不是动作执行器/仿真器或任务修复。


### 2026-10-22：GROC003 / GROC010 私有探针改为分 GPU 并行

用户指出有空闲 GPU 后，检查发现 GPU 2/3 分别约有 25/26 GiB 空闲显存。此前两项探针串行等待 GPU 0，形成不必要的调度瓶颈。现将 GROC003 关系保持探针移到 GPU 2，GROC010 导航距离修复对照移到 GPU 3；每项使用独立 appdata、输出目录及按 GPU 的互斥锁。冻结源码和任务参数不变。原排队配置、状态和脚本保留为 `.before_multigpu`；调度修改记在各 `queue_plan.json` 的 `scheduling_revision`。两个 launcher 已启动，尚无探针结论；这些是私有机制验证，不计入自主 benchmark 成功率。

证据：`../../experiments/groc003_bag_retention_20261022_queue/`、`../../experiments/groc010_navigation_reach_20261022_queue/`。后续实验应按实际空闲显存安排独立 GPU，并隔离 appdata；GPU 利用率为零不代表无其他进程。


### GROC003：2026-10-22 关门触发关系丢失的实测进展

GPU 2 私有探针在同一空袋、同一选中冰箱点、真实放置和开关门助手下，已记录 `after_place_open_fridge: NextTo=true` → `after_first_close: false` → `after_reopen: true`。袋子位移仅约 1.38 微米，冰箱根姿态不变；其包含门板的 AABB 随开关变化。按上游 `NextTo._get_value` 的距离门槛计算，开门时 AABB 间距 0.031733 m，小于阈值 0.329339 m；关门后间距 0.498146 m，大于阈值 0.267042 m，因此无需缓存异常即可解释拒绝。证据支持动态几何关系变化，而非袋子被门推走。

复现入口为 `../../experiments/groc003_bag_retention_20261022_queue/queue_plan.json` 的冻结命令；逐状态原始记录在 `probe_r1/run/result.json`，计算在 `probe_r1/relation_distance_gate_analysis.json`。当前探针仍运行，待第二次关门及视频收尾。该私有场景复现了同类失败，不证明历史自主回合的每个中间状态。未修改评分器、仿真器或放置器，未重写历史得分。

定位：`src/manipulation_agent/executors/placement.py::_checked_place_next_to` 接受当前开门状态下成立的官方关系；上游 `object_states/next_to.py` 使用当前完整 AABB。后续需明确工具对铰接目标的稳定放置保证，保留官方评分，避免通过放宽目标掩盖问题。新任务预检应覆盖“先建立邻近关系、再执行要求的关门动作”的组合后置条件，而非只验证各动作独立成功。

探针收尾：第二次关门再次 `NextTo=false`，完整序列 true/false/true/false；`reproduced_relation_loss=true`。launcher 正常收尾，探针因关系保持断言失败退出 1，非超时或启动失败。原始失败与视频均保留。

GROC003 独立候选试验：`../../experiments/groc003_body_distance_candidate_20261022/` 已冻结并启动 GPU 2 探针。只在该实验源码的 `placement.py` 中，为多链接目标增加根链接 AABB 距离筛选，随后仍须通过原有官方 NextTo、支撑与碰撞检查。候选尚未验证，不写回仓库放置器，不改变评分器或仿真器。探针追加根链接 AABB 记录，以检查主体假设是否适用于该冰箱；多链接不必然代表铰接、根链接不必然代表完整固定主体，因此仍有泛化与采样耗尽风险。失败和视频继续保留。

GROC010 私有仿真对照收尾：GPU 3 探针 8/8 检查通过，低位牛奶的旧版/候选版均导航至同一位置，新观测抓取点距离均为 1.204448 m，二者抓取均成功；地面与高处目标分类控制也通过。`baseline_reproduced_out_of_reach=false`，故这项探针提供分类与实际动作回归证据，不构成历史超距失败的仿真 red→green 证明。候选 1.35 m 上限已记录；此前真实规划器离线 red→green 仍独立保留。尚需更贴近失败几何的仿真复现和完整自主回归，未宣布修复完成或推广。证据：`../../experiments/groc010_navigation_reach_20261022_queue/probe_r1/run/result.json`。

GROC010 下一项私有探针已启动 GPU 3：`../../experiments/groc010_cookie_boundary_20261022/`，取历史 cookie85 失败附近坐标（小件约 [8.195,2.54]，起点 [7.45,1.3]）构造约 1.45 m 边界场景，对旧版及 1.35 m 候选上限分别执行真实 RGB 导航与重新选点抓取。该场景只参考坐标，未复原历史家具状态和物体方向，不称为精确回放。保留上一项未复现失败的通过结果；等待新的实际对照判据，不据坐标推测修复成立。冻结差异和完整源码校验记于 `source_factor_review.json`。

GROC003 固定主体距离候选试验失败，未部署：100个候选中26个无地面命中、44个包围盒重叠、30个被新增主体距离筛选拒绝，最终 sampling_error 并回滚。不是“保持关系通过”，也没有建立初始关系。冻结失败保留于 `../../experiments/groc003_body_distance_candidate_20261022/`，结论见 `result_review.json`。需要进一步记录主体/链接包围盒和候选位置，以区分根链接代理不适用与采样覆盖不足；不得以删除碰撞或放宽评分换取通过。

GROC003 针对失败候选追加几何诊断，GPU 2 运行于 `../../experiments/groc003_body_geometry_diagnostic_20261022/`。保持上轮放置行为不变，只新增固定主体/全部链接 AABB 与通过现有碰撞筛选的候选距离日志，复用已终止前项的隔离 appdata。用于区分根链接代理错误与候选覆盖不足，不是第二个已验证修复。官方 NextTo 随铰接几何变化具有定义依据，不能仅凭关系变化称为仿真器 bug；工具稳定放置偏好、任务组合验收与 agent 最终复核需要分别评估。

GROC010 完整自主回归已预注册并排队于 GPU 3：`../../experiments/groc010_full_regression_20261022/`。与已完成skill v3回合的冻结执行源码逐文件比较，仅 `omnigibson_rgb.py` 不同，使用候选1.35m导航上限；保持skill v3、任务301/seed0、场景overlay、gpt-6.1-sol low、120动作/20000步/14400秒预算不变。冻结BDDL哈希已核对。等待边界探针终止且manifest通过后，按GPU锁启动完整episode并检查finish、视频、skill hash、调用审计和独立评分。未混入袋子放置原型。历史两轮仅作描述性参考，不宣称随机受控效果或修复已完成。

GROC003 几何诊断已收尾，再次采样耗尽。根链接 AABB 为 x=[4.664,5.712], y=[-0.998,-0.119]，与固定冰箱主体一致，根代理错误假设在本实例不成立。开门后的完整目标 AABB 横跨门叶与主体间空地；记录几何的最小计算已确认：一个原有半径0.34m候选与完整AABB重叠，却不与任一非退化实体链接AABB重叠。证据 `../../experiments/groc003_link_bounds_candidate_20261022/recorded_geometry_reproduction.json`。

下一冻结候选仅将目标碰撞检查改为逐链接包围盒，保留上轮固定主体距离筛选、其他物体碰撞、官方NextTo和地面支撑验证；已在GPU 2启动同序列探针。未写回仓库。退化链接跳过逻辑及多链接目标泛化仍须审查，单个家具的几何计算不证明物理关门保持成立。源与命令见该实验目录。

GROC003 逐链接候选私有序列通过：同一bag82/选中点下，放置、关门、开门、再关门的官方NextTo均为true，4/4检查通过；原版同序列true/false/true/false。manifest通过，视频哈希核对及整段解码通过。证据 `../../experiments/groc003_link_bounds_candidate_20261022/probe_r1/result_review.json`。仅证明该私有序列，尚未写回仓库或推广；需其他袋子/目标回归、自动化测试接缝与完整自主验证。

GROC003 仓库候选已集成（仍非benchmark验收版本）：`src/manipulation_agent/executors/placement.py` 与通过bag82私有探针的冻结候选逐字一致。新增 `tests/test_next_to_articulated_target.py` 调用实际采样器，旧版选择开门距离通过而关门距离失败的位置，先运行得到预期红色断言；应用候选后，与放置、导航及RGB边界测试合计55项及7子测试通过，diff检查通过。测试使用受控射线/物理/状态替身，不能替代真实邻接与门碰撞验证。完整验证记录在 `../../experiments/groc003_link_bounds_candidate_20261022/repository_candidate_validation.json`。上游仿真器与评分器不改；当前GPU3自主实验运行冻结旧放置器，因此不会混入本次修改。其他目标与完整自主设施回归仍待。

GROC003 完整自主回归已预注册排队：`../../experiments/groc003_full_regression_20261022/`，等待bag81私有探针正式通过后在GPU 2启动。与正在GPU3运行的导航候选实验逐文件比较，仅放置器源码变化；任务301/seed0、skill v3、导航上限候选、模型gpt-6.1-sol low和预算均相同。不同GPU不用于受控耗时结论，两次单回合不支持统计改进结论。冻结源码和BDDL校验通过，独立评分/调用对齐/正式finish/视频审计仍须完成。

GROC003 bag81 私有回归收尾：放置/关门/重开/再关四项官方NextTo均为true，4/4通过；视频哈希与整段解码通过。与bag82一起提供同场景两只袋子的证据，不代表新布局或跨任务泛化。结果见 `../../experiments/groc003_link_bounds_bag81_20261022/probe_r1/result_review.json`。满足预注册条件后，GPU2完整自主回归已启动，launcher PID 21118，冻结skill v3和导航候选，只更换放置器，完整结果仍待。

GROC003 新增单链接刚性目标正向回归：同一实际采样器测试参数化为开门多链接目标和刚性单链接目标，2/2通过。该项仅检查受控几何环境下普通目标分支未被破坏，不是跨任务物理回归；未据此重跑无变化的全套55测试。记录 `../../experiments/groc003_link_bounds_candidate_20261022/rigid_target_regression.json`。

GROC003 候选兼容性边界（不是原杂货失败根因）：实际采样器的受控“零体积结构根+实体子链接”测试先出现采样耗尽；仅使新增主体距离筛选跳过无有效几何的根链接后通过。逐链接碰撞、官方NextTo和支撑验证保留。开门目标、单链接目标、空根目标及既有放置检查合计8项通过，diff检查通过；非有限根也不作为几何锚，但未独立实景验证。记录与新源码hash见 `../../experiments/groc003_link_bounds_candidate_20261022/empty_root_guard_validation.json`。这是待验收候选的兼容性修正，当前两轮自主实验冻结源码不变，不能将其结果算作新guard版本的完整验证。

设施版本登记：`../../experiments/groceries_facility_candidates_20261022/manifest.json` 分别登记当前自主回归的逐链接/主体距离版本（placement SHA 1b9ace…）和空根保护版本（97de9c…）；后者已冻结完整执行副本，逐文件核对与前者只差placement.py。共有任务BDDL、场景overlay、导航候选和skill v3哈希一并记录；二者的物理、自主及离线验证范围分开，不能混用结论或静默作为同一skill对照设施。


### 2026-10-22：skill 对照的混杂控制

适用杂货v2、house_double_floor_lower/301/seed0研究任务。minimal相较planning同时移除计划、记忆和skill，故其差异不能单独解释为skill文档增益；尚未产生错误归因的模型比较。新增planning_no_skills，只移除skill目录/读取、库加载及读取指令，保留七个共同工具及逐字段相同schema、public decision与supervisor收尾。修改层级为实验协议与harness配置，未改评分、任务或动作执行器。34测试/32子测试通过，实际mock harness/三客户端注入及冻结副本核验通过；仍未证明自主效果。原始校验见experiments/skill_ablation_protocol_20261022，四cell预注册见experiments/skill_presence_controlled_20261022。以后造benchmark时，先明确比较的变量，固定共同工具、场景和预算，并将关闭整套辅助工具与仅关闭skill文档分开报告。

消融接入后回归补充：旧hot-drink导航单测仍期待_ground(require_object=False)，与GROC-007已采用的像素owner分类契约相反。更新测试使用地面owner，检查require_object=True且所选owner、原始点和步骤预算完整传入导航；未修改运行代码。导航/抓放/RGB边界/planning共70项及7子测试通过，日志experiments/skill_ablation_protocol_20261022/facility_protocol_regression.txt。初始失败输出与旧消融validation均保留，不覆盖历史。物理与自主范围仍以原探针及进行中的两轮完整回归为准。

BENCH-001统计补充：为固定杂货v2/301/seed0的恢复成本审查，在analyze_workflow_efficiency.py加入失败预算动作后的同类成功候选区间。区间分别计后续请求、预算动作与工具墙钟，参数拒绝不增加预算，未响应重试不当作成功。confirmed_recovery恒为null，须人工结合RGB/对象身份判断；区间可重叠，不汇总成已证实恢复成本。两个新用例先因字段缺失失败，再实现后4项统计测试通过。历史v2/v3四轮重算，所有旧字段逐项保持一致；分别有20、9、9、7个待审区间。冻结分析器、测试与输出见experiments/skill_recovery_review_20261022。未改变已冻结实验执行副本、任务评分、动作执行器或skill。

GROC-010完整自主回归中途证据：navreach_candidate/skillv3/301/seed0动作28抓bag82超距，前一步27选的是地面；29明确选同袋导航（floor_waypoint=false、max_target_distance=1.35），30fresh RGB重新选同袋后抓取成功。实际查看三图并核对私有选点身份；fresh选点到规划终点XY约1.018137m（不是直接测得最终base姿态）。证据experiments/skill_recovery_review_20261022/live_second_bag_recovery_review.json及live_second_bag_recovery.jpg。证明这一次自主恢复链能执行，不能据此宣称新版优于旧版或整轮通过。此前pending诊断快照保留。

消融审计遗漏修复（尚未发生模型误判）：scripts/audit_demo_trial.py仅将关闭skill目录的minimal视为合法RGB配置，会错误拒绝planning_no_skills并漏查其public decision。将profile审计提取为visual_profile_checks，认可无skill规划组，继续检查每次decision并另查禁止skill工具调用。新增用例先ImportError再修复，审计/planning/统计共19项通过。用已实际执行的两份MCP/HTTP mock调用账本通过新版profile检查。改变层级为实验审计，无任务/评分/执行器变更。四cell尚未启动，原execution及plan_v1保留；execution_v2仅更新audit与分析器，154文件哈希核验通过。证据experiments/skill_presence_controlled_20261022/audit_supervisor_validation_v2.json、audit_pytest_v2.txt。以后扩展agent profile应同步核查CLI、工具目录、提示词、harness、provenance和独立审计，防止仅工具能调用但审计不识别。


## GROC-011：小购物袋被导航障碍过滤，接近期间食品散出（候选修复）

- 任务：杂货v2、house_double_floor_lower/301/seed0，BDDL b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1，RGB显式导航、skillv3、导航74bb/放置1b9，完整回归stableplacement回合仍在运行。风险子目标：搬运后源容器载荷保持/清点，非最终得分。
- 证据：bag81初抓与再次抓均含3件，厨房12放袋成功且payload检查通过；13开冰箱，14接近袋。实际查看capture13/14/15四视图及连续视频env2500/2550/2600/2650/2700：2500袋口食品仍可见，2550附近番茄散出，2600牛奶已在袋外地面。base_motion的2548步实际底盘位置距放置袋中心约0.211673m；不能把这个距离当作已记录的物理接触。视频截图与选择索引在experiments/groc003_full_regression_20261022/nav14_*，原连续视频保留。
- 已确认缺陷：真实_dynamic_floor_obstacles排除max(width,depth)<0.35的对象，本袋约0.121×0.182m因此不进障碍图；日志障碍名单也没有袋。用实际方法和测得尺寸构成CPU回归，旧冻结源码断言names含staged_bag失败。精确物理接触与散落因果仍未确认。排查假设依次为导航碰撞、释放后载荷不稳定、开门交互；尚需真实仿真有/无导航与旧/新map对照。
- 候选修复层级：自身执行器omnigibson_rgb.py，仅去掉地面对象的家具宽度门槛、保留非退化足迹及原高度/固定物/持有物排除；不改仿真器、任务、评分、skill或1.4m操作范围。真实方法旧红/新绿，持有同袋不作障碍控制通过；相关57测试与7子测试通过。很低物体的既有8cm高度门槛不在本次修复范围。
- 冻结与限制：experiments/groc011_small_floor_obstacle_20261022含完整execution、旧RGB源码、测试、红绿输出及validation；候选RGB d57f520d…，相对正在回归的1b9设施只改RGB文件。当前两轮与已冻结消融策略未被替换；有无skill对照仍待设施审核，明确加入GROC011调查。无物理red→green或自主成功证据，不能宣布载荷散落已修好。
- 造task复用：把小容器放地面后再接近，检查其真实足迹是否进动态障碍图、整条路是否有底盘净空，并在导航后再次检查载荷，不能仅凭place成功证明后续运输/清点稳定。保留无导航静置控制以区分碰撞与释放不稳定。

GROC-011真实物理对照已准备并排队：experiments/groc011_small_floor_obstacle_20261022/probe_plan.json，监督进程726924实际存活，持等待GPU3互斥锁（当前完整自主仿真4018953仍运行）。独立appdata在锁释放后复制；保留三种case：静置350步、旧障碍图导航、候选障碍图导航。以历史start/bag XY私有预置，使用原场景袋内相对布局，逐case sim.dump/load恢复同一预置物理状态；食品/袋/base每5步私有位姿日志、末态Inside、导航障碍名单、连续录像分别存证。不称精确历史回放，不给模型私有状态、不计自主成功。baseline若未散落仅算未复现；静置若也散落则优先排查源容器物理稳定性。源码与任务文件哈希冻结，py_compile通过，尚未实际仿真验证，不能据脚本准备宣布修好。

GROC-011旧路径几何复核：从放置日志center/extent推算袋XY足迹，调用冻结候选中的真实mask_dynamic_obstacles（原margin0.38、0.05m网格），已记录导航14路径的52个底盘采样点落入候选禁区，step2524–2575，与连续录像2550附近散落区间重叠。输出experiments/groc011_small_floor_obstacle_20261022/recorded_route_dynamic_mask_review.json。使用空静态网格隔离动态遮罩、仅放置后推算AABB，未重建原静态地图或每步袋子运动，不证明实际接触、新路线可达或物理修复。物理对照仍在GPU3队列。


### 2026-10-22：导航设施完整回归的终态漏件（GROC-003 / GROC-010）

固定杂货v2 / house_double_floor_lower / 301 / seed0，BDDL b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1；gpt-6.1-sol low、skill v3、导航74bb8c、旧放置器的完整自主回合 `../../experiments/groceries_v2_navreach_candidate_skillv3_gpt61sol_low_20261022_r1` 正式结束。独立最佳binding为11/12：仅第二件饼干入柜失败，两袋NextTo及全部关闭目标终态通过；63预算动作、7909环境步、79.201108米。模型误报achieved，不能以动作返回成功代替逐件验收。

确认原因：event-00179称第二袋底部可见且无余物并抓袋，私有carry记录env6855显示bag82仍携带cookie86；cookie85已单独入柜，cookie86未被单独尝试抓取。这是取空/完成判断遗漏，不是cookie86抓取能力失败。已实际查看rgb52四视图：袋在back图下缘，袋口较小且斜视，无法确认完整袋底或从图中可靠识别余饼干；不应将未见物体解释为空。图像、事件与私有状态分别保存于该轮设施目录的 second_cookie_omission_review.json、second_bag_empty_claim_rgb.jpg、final_goal_review.json。私有身份/关系仅诊断，不注入模型。沿用GROC-003，不因新回合另造根因；尚未修改skill，不宣称已修复。后续统一设施后验证已有视觉覆盖候选与逐件清点，通用skill不写入此场景准确物品数量。该轮并未重现关门后NextTo退化，不能由单轮通过否定历史设施缺陷。


- GROC-011 探针准备失败与恢复（2026-10-22）：首次复制appdata时Kit/shared/screenshots无读权限，尚未启动仿真；保留失败状态、日志与部分目录。queue_probe_v2.py只跳过该精确截图目录后重试，冻结源码/资产/任务/基线不变；新launcher已启动，物理结果待验收。证据见 ../../experiments/groc011_small_floor_obstacle_20261022/provisioning_repair_record.json。复用运行环境时应单独排除非实验输入的受限截图缓存，不能删除或修改源目录权限，也不能把准备失败算作任务失败。

- GROC-011 物理探针r1：静置350步三件Inside均真；旧导航239步后番茄Inside=false，复现散出。候选组被探针自身TypeError中止：上一case将绑定旧类的_step写回实例，切换__class__后仍调用旧类方法，super类型不匹配。属于诊断脚本缺陷，不是候选执行器失败。新probe_small_bag_navigation_v2.py在finally删除实例_step包装以恢复类方法解析；原脚本、r1源码快照/结果/录像均保留，r2另目录重跑同一物理配置。候选食品保持及红绿对照尚未验证，不能宣布修复。详见 ../../experiments/groc011_small_floor_obstacle_20261022/probe_r1/run/result.json 和 probe_plan_v2.json。


### 2026-10-23：设施回归完成与有无skill对照revision3

placement1b9 + navigation74bb + skillv3 的完整杂货v2/301/seed0回归独立12/12，88预算动作、12412环境步，审计所有检查通过，录像全流解码通过。单轮成功不证明放置器因果收益或稳定成功率。GROC-011探针r2完成同一dump初态的三组物理比较：静置350步三件保持；旧导航239步番茄散出；候选263步三件保持。旧组障碍遗漏/候选包含小袋检查均通过，录像解码通过。私有构造对照，不是原回合精确重放或自主成绩。

据此skill_presence_controlled_20261022更新至revision3：新execution_v3仅替换execution_v2的RGB导航为d57f520d6848e0a16f4f83db8bdeaeabf49d75e55f98715256da14a5d45df7d0，保留placement1b9、skillv3、相同模型/任务/预算。原plan/execution保留，154文件manifest校验；facility_review.json关联物理/自主/录像证据哈希。GPU2先无后有、GPU3先有后无，各2重复，独立准备进程已启动。全批仍研究协议；新导航尚无整轮自主结果，不将预期收益写为结论。记录每个任务失败，逐轮检验实际profile/skill哈希和审计；与历史v2/v3比较不得混为有无skill证据。

## HOT-003：热饮成功回合的重复面板尝试与末段小物搜索

- 任务：prepare_and_serve_hot_drink / house_single_floor / 301，seed0/11/22；high_level_visual.bddl hash `4be5469c5eff25f59504c90c4d78d04312057353ae35a0bf007012c2d0a9b915`，11项高层协议，baseline skill 5fd7031b…，显式导航。三回合均整任务成功；本例是成本问题，不是失败成功率证明。
- 证据：三个基线请求109/110/102才抓到糖饼干；seed22请求73/75/85/87/90/92为被拒门/器具面板操作，seed11请求80/81/107等上柜尝试被拒。原图显示柜台边缘遮架面；较早小棕色片仅是候选，不能确认当时已可识别或抓取。
- 归属：agent视觉搜索与skill的负证据记忆/覆盖记录不足是待验证解释；小物分辨率、遮挡和随机策略亦可能贡献，未证明所有搜索均冗余。
- 修改层：候选1仅skills/visual-exploration/SKILL.md。记录实际可见区域和被拒面板，要求有新视觉证据再重试，取物后检查新露架面。未修任务、场景、执行器或评分。
- 验证：skill格式及冻结skill/planning六项测试通过，bundle d6f25846…；完整三seed候选已启动，收益待验证。保留baseline与候选每次尝试，按任务成功优先，再比较成本。
- 可复用检查：给柜内小物任务记录“看见开口”和“看清整个目标架面”的区别；拒绝的面板位置不能在换相机后自动变为未检查储物空间。规则允许新可动部件/解除遮挡后的重试，避免封死必要恢复。
- 证据目录：experiments/hot_drink_optimization_20261022/baseline_evidence_review.json、baseline_visual_review、candidate1/manifest.json。

## HOT-004：热饮seed11导航后四路近白色视野，随后自主恢复（原因未隔离）

- 同HOT-003任务/定义/协议，seed11。请求33(event-00118)意图沿沙发旁返回厨房，使用rgb-00032-front的模型像素；之后capture33四路图主要为近白色几何。请求34–43执行恢复，capture42-left恢复厨房可见，43回到正常柜前视野；整任务最终11/11。
- 原因：尚不能区分模型选点、目标停靠/路径、碰撞地图和相机贴近几何的贡献；没有确认导航穿墙或修改执行器。不能把此回合所有墙钟差异归因于skill。
- 证据：基线seed11原始events.jsonl、navigation_plans/base_motion和frames；批次baseline_visual_review/seed11_navigation_recovery.jpg及baseline_evidence_review.json记录窗口33–43、调用与成本。
- 状态：未解决的设施/策略边界调查；本轮候选1运行时保持冻结，不用提示词宣称修复。后续需在同初态复现选中表面和私有停靠路径，核对有效通行区域与相机几何，再决定所属层；若修执行器另立协议对照。

### 2026-10-23：有无 skill 对照首格重现 GROC-003 漏掉第二份饼干

- 任务：固定杂货 v2 / house_double_floor_lower / instance301 / seed0，BDDL `b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1`；revision3 统一设施，gpt-6.1-sol low，planning_no_skills，120动作/20000环境步/14400秒预算。原始回合：`../../experiments/groceries_v2_skill_presence_without_skills_gpt61sol_low_20261022_r1`。
- 终态：event-00244 最佳 binding 11/12，唯一缺失为 goal5 第二份饼干 Inside 任一合法吊柜；两袋 NextTo、四件冷藏品、第一份饼干及全部关闭目标通过。77预算动作、11失败、8943环境步，独立 volume 采样1360步；agent 误报 achieved。`final_goal_review.json`、`workflow_efficiency.json` 与 `demo_audit.json` 保留；任务失败之外运行完整性检查通过。
- 已确认与未确认：第二袋两次起抓的私有 carry 记录均有两份饼干和牛奶；模型末段称两袋已取空。终态评分证明第二份饼干未入合法柜，尚未定位其最终位置及逐视角覆盖，不把本轮直接归因为抓取器失效或视觉不可发现，也不以该单格推断 skill 收益。私有信息仅用于诊断。
- 修复状态：本轮未修改 skill、评分、执行器或仿真器；保留真实 agent 失败，继续冻结对照。该例复用 GROC-003，不另立未经证实的根因。造任务检查仍需逐件验收、允许遮挡解除、核查同类多件的取出证据，不能用类别完成替代每件完成。
- 性能候选：第二盒牛奶 place_inside 返回成功但耗时563.53秒，1194次采样物理步后由 verified_fillable_grid_fallback 完成；精确阶段耗时未分解。证据 `../../experiments/skill_presence_controlled_20261022/milk_placement_sampling_cost_review.json`。后续另版本验证采样预算，当前对照不更改。

### 2026-10-23：GROC-003 在完整对照中三次复现，尚未解决

同上 revision3 固定任务、instance301/seed0、定义b38c56b及d57/1b9设施，四格独立验收全部结束，三次失败均11/12，缺第二份饼干入任一合法吊柜，并误报完成。各回合保留 second_cookie_extraction_review.json、独立评分、审计与录像。完整索引为 `../../experiments/skill_presence_controlled_20261022/four_cell_comparison_reviewed.json`。

有skill第二轮最后一次抓bag82（env13629）私有carry仍含cookie86，未见成功单独抓取该物记录；无skill第二轮env7402抓bag82也仍含该物。前者rgb-00102-front人工检查见袋壁遮挡且完整袋底未显露，模型仍从“底部遮挡”转为“未见食品、已空”，最后只记录一份饼干便宣称完成。这确认缺乏充分清空证据的agent判断失败；不能据单张图断言物体不可发现，也未证明抓取器故障或最终准确位置。成功回合曾通过重摆袋后单独抓取第二份饼干而12/12，说明存在至少一条实际可执行链，但未建立跨视角的稳定可发现性。

修复层级：本组未改评分、执行器或仿真器。独立视觉候选要求辨明未覆盖内部区域、有效换位及逐件取出证据，仍待完整自主对照；案件保持未解决。未来造task需验证同类多件的逐件验收与遮挡解除链，不能用类别已处理或模型声称袋空替代。若进一步复现确认观测或动作设施缺陷，应另版本修复并回归。

### 2026-10-23：GROC-003 放置性能调查补充（未修复）

针对无skill首轮第二盒牛奶place_inside耗时563.53秒／1194采样物理步，检查冻结placement1b9的bounded_step：90秒／1200步回退仅在demo_motion启用，180秒旧限制仅用于非managed deadline；当前托管no-demo回合依赖整轮截止时间，不受这两项单次采样限制。静态证据见 `../../experiments/skill_presence_controlled_20261022/milk_sampling_budget_static_review.json`（含源码及哈希）。该分支条件已确认，但尚未分离随机采样与网格回退各阶段耗时，不能认定全部563.53秒由此造成。

修复状态仍未完成。后续设施候选应分别记录阶段耗时，并以同初态物理对照检查采样预算、原有与新内容关系保持、失败回滚，再做自主回归。当前视觉skill对照不改执行器。造任务时应检查关键动作长尾耗时与预算边界，区分模型恢复成本和设施内部搜索成本。

### HOT-003 续：2026-10-23 热饮视觉候选的有效任务失败与规划候选

- 复现：同已冻结热饮301任务／high_level_hot_drink_state_v1／gpt-6.1-sol low／120动作，candidate1 seed0与22均10/11，只缺cookie_served；seed11为11/11。三场正常结束且连续录像／调用对齐／状态重放通过。失败视频与原始日志保留，不能排除失败或改写评分。
- 证据：`../../experiments/hot_drink_optimization_20261022/candidate1/cohort_outcome_review.json`、`full_trace_cost_review.json`；seed0 late_panel_targets.jpg显示多种不同面板；seed22 shelf_search.jpg仍有台沿遮挡，未证实小棕片身份或可达性。确认是剩余必需项未完成；视角、像素、搜索优先级与随机策略的贡献未隔离。
- 修改层：candidate1不晋升；candidate2基于baseline，撤回候选视觉段，仅long-horizon-planning增加未定位项搜索与重复配送成本的优先级比较。不是场景／任务／执行器修复，不改变完成条件。
- 验证：frontmatter有效，冻结测试test_skills_loop.py与test_long_horizon_planning.py共6通过；初次pytest缺scripts根导入路径，修正PYTHONPATH后通过，无运行时改动。三场自主验证刚启动，尚无收益结论。
- 复用检查：逐项保留未定位义务，区分信息获取／失败恢复／必要配送；记录动作预算与请求尝试的区别。以后任务构建仍须证明物体可发现及动作链可执行，不能用策略失败自动推定任务缺陷。


### 2026-10-24：热饮专项九场完成与最终选版

本组 baseline、candidate1、candidate2 均完成 seeds 0/11/22，九场全保留。独立高层11目标成功分别3/3、1/3、2/3；平均动作112.33、119.67、118.00；平均失败动作15.67、19.33、21.00。candidate2/0仍缺糖饼干，10/11；candidate1/0和22同样缺点心。三场失败均正式收尾且如实报告，调用对齐、状态重放、显式导航、空手与五视图录像完整性通过，不是被丢弃的基础设施失败。

结论：两轮候选均不晋级，保留基线 bundle `5fd7031b5e4b2398c27723137156011b9ee539eb8820ac3c62b16a132894edd2`；仅将本轮长程规划候选字节恢复为已冻结基线，视觉探索已为基线，其他层未作本轮新修复。候选与全部失败录像保留。candidate2耗时减少不等于完成效率改善，共享主机及模型等待未控制。

恢复成本实例：candidate2/11 请求98–114暂放饼干后重获定位17次请求、134.81秒工具耗时；candidate2/0请求97–120最后搜索与收尾24次请求、801.76秒仍未完成。窗口混有必要检查与恢复，未做删除反事实，不统计为已证实冗余；失败调用自身延迟不能代替下游恢复成本。仅3个同布局开发seed，无留出/原生粒子/官方挑战泛化结论。HOT-004继续未解决，不凭失败擅改导航执行器。

最终报告：`experiments/hot_drink_optimization_20261022/FINAL_REPORT.md`；完成核验 `completion_audit.json` 校验三版各254源项和九场实际bundle，54个发布链接HTTP200。各轮冻结manifest、完整比较、选帧与结构化恢复窗口、模型用量和录像均在同目录或逐场原始目录；用量不冒充实际费用。

### GROC-012：2026-10-24 冰箱旁放袋后连续导航失败（待诊断）

- 配置：carry_in_and_put_away_groceries/v2_design，house_double_floor_lower，instance301/seed0，BDDL b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1；custom_native_bddl_rgb_agent_ideal_executor，RGB加有限抓取类别反馈，gpt-6.1-sol low；120动作/20000环境步/14400秒，导航d57f520d、放置1b9ace37、v3 bundle5fd7031b。
- 原始复现：groceries_v2_visual_recovery_v3_gpt61sol_low_20261023_r2/run/events.jsonl：event99/100/101旁放调用/私有结果/成功反馈；随后106/109/118/121/127五次navigate_to均失败，关冰箱后仍未恢复。event133独立验收6/12，缺goal2/4/5/6/7/8；34动作主动blocked，未耗尽预算，不是超时。
- 确认与假设：完整自主轨迹已记录旁放后失败，RGB显示冰箱侧遮挡；尚无同状态可判失败探针。碰撞、起点网格、像素目标投影及视野选择仍待区分，不能宣称仿真器或执行器根因已确定。
- 修复层与验证：本轮无实现修复，无评分放宽；四格评分/调用对齐完成，四条原片全片解码通过，人工复核8帧。证据及哈希见[本批审阅](../../experiments/skill_visual_recovery_controlled_20261023/review.md)。待同状态记录碰撞、网格、grounding和合法退出路径，再对确认失约层做红绿回归；探针不计自主成功。
- 造任务复用：验证“旁放→退出→下一物品”关键链，不能仅测孤立放置成功；本案保持未解决。

### 2026-10-24：GROC-003 候选仍漏件及旁放终态差异

同GROC-012配置，候选bundle d24f0fae第二轮110动作/20失败，event363最佳绑定9/12，缺goal1牛奶79、goal5饼干86、goal7袋82归位，却报告achieved。私有抓袋event322/328对应bag81、343/349对应bag82，均contained_rigid_objects=1；信息仅用于事后诊断，未输入模型。关键袋口RGB未完整显露袋底，清空判断缺证据；未证明物体不可发现、抓取器失效或最终准确位置。

event354/355/356袋82旁放局部成功，下一导航后最终goal7失败；v3第二轮袋81亦局部成功而终态失败。新回合尚未定位关系首次丢失时刻，不能直接套用历史门AABB根因。无skill/评分/执行器/仿真器修复，候选不晋升。以后造task须逐件验收、验证遮挡解除、放置后关系保持与退出路径。四格证据及原始哈希保存在[结构化审阅](../../experiments/skill_visual_recovery_controlled_20261023/four_cell_comparison_reviewed.json)。

### 2026-10-24：GROC-012 失败阶段与目标命中核对

五次失败的所选像素均由私有grounding命中地板，排除了“这些调用选中了冰箱而非地面”这一具体解释。event107为No candidate near the visual target is reachable from this start；110/119/122/128为No bounded projection of the selected visual target is navigable，不能把它们统称为已确认碰撞卡死。现有navigation_plans仅记录成功规划，缺失败时网格和候选集合，尚不能判断合法退出路径是否存在。该审阅是日志分类，不是同状态重放，未修改执行器或证明根因。下步复现输入须包含起点、静态/动态网格、目标及候选筛选结果。结构化原始证据：[失败阶段记录](../../experiments/skill_visual_recovery_controlled_20261023/groc012_navigation_failure_stage_review.json)。

### 2026-10-24：GROC-012 补齐私有导航失败快照与CPU重放（设施诊断能力，非修复）

修改层：benchmark执行器诊断；gt_navigation.py为plan_navigation增加可选diagnostics，记录参数、投影候选、可见性筛选结果及搜索阶段；save_navigation_failure保存静态/动态网格及私有上下文的压缩快照。omnigibson_rgb.py在严格/放宽规划失败时保存证据，公共错误接口与路径选择逻辑不变；动态遮罩生成错误、跟随器错误不在本次捕获范围。新scripts/replay_navigation_failure.py可在CPU上重放已记录的规划输入，遇到未记录的可见性决策明确拒绝，不猜测结果。它不重建仿真物理状态，不构成自主成功。

验证：PYTHONPATH=src /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python -m pytest -q tests/test_navigation_failure_capture.py tests/test_gt_navigation.py tests/test_dynamic_navigation_obstacles.py tests/test_self_occlusion_navigation.py tests/test_visual_manipulation_navigation.py：40 passed、9 subtests passed。新测试验证两种失败阶段快照重放、原有错误保持、成功规划结果一致，并实际调用CLI；py_compile及diff检查通过。保存的合成不连通网格CLI重放reproduced=true；不是历史冰箱状态复现。版本SHA与命令见[验证记录](../../experiments/skill_visual_recovery_controlled_20261023/navigation_diagnostic_validation_20261024/validation.json)。

未重写既有实验快照、评分或skill，也未启动新模型回合。根因仍待真实失败状态捕获与同输入红绿验证。未来造task应在允许观测可发现性之外，为关键导航失败保留可离线重放的私有规划输入，避免只有同一公共错误码而无法区分失败层。

### 2026-10-24：GROC-012 恢复成本人工分类

原版第二轮旁放后动作27–34共8动作：5次不同地板目标的失败导航、2次换视角look、1次成功关闭冰箱。该窗口工具耗时86.81秒，其中失败导航9.54秒；旁放返回至最后一次导航返回墙钟195.83秒，至finish请求228.96秒。环境推进118步，来自look/close；失败导航本身未推进env.step。结束仍余86动作，非动作预算用尽。

未确认同像素无变化重复；look获取新视野、close也服务最终关闭义务，因此不将整段8动作称为冗余。它是未恢复成功的实际成本，不证明存在更优合法退出动作。避免把墙钟剩余全归因执行器或把重叠自动恢复窗口相加。[逐动作分类与计算](../../experiments/skill_visual_recovery_controlled_20261023/groc012_recovery_cost_review.json)。

### 2026-10-24：GROC-012 诊断接入的执行器集成验证

新增CPU集成测试直接调用RGBBackend._navigate，使用合成不连通网格而非mock规划器：实际规划失败后保存一份私有压缩快照，原SkillError及changed=false保持，机器人步数仍为0；保存文件通过独立CLI重现同一失败。test_navigation_failure_capture.py为3 passed、2 subtests passed，py_compile和diff检查通过。[集成验证版本与范围](../../experiments/skill_visual_recovery_controlled_20261023/navigation_diagnostic_validation_20261024/adapter_integration_validation.json)。这验证了诊断写入的真实调用位置，仍非历史物理状态复现或bug修复。

通用probe_recorded_actions.py目前没有自定义BDDL参数，默认任务与本批杂货v2不同；有限姿态检查不等于卡住复现，替换image_ref后也缺前缀grounding分歧检查。因此未直接运行该脚本并宣称回归。后续历史前缀探针必须冻结自定义定义及场景，逐步核对命中身份/动作反馈与状态；一旦分歧，保留为重放偏离而不是原bug已消失。没有启动新模型或仿真实验。

### 2026-10-24：GROC-012 历史前缀探针准备完成，尚未仿真运行

新增scripts/probe_navigation_prefix.py，prepare-only从原版第二轮提取34个原始动作、8个预期失败、逐调用grounding和五项输入哈希，保留原像素并只更新当前视图引用。执行前检查任务/场景哈希及no-demo协议，逐步检查命中对象、命中位置（显式0.05米偏差门限）和动作成功/错误码；首次偏离即prefix_diverged，不计作原bug消失。完整物理状态相等性未由这些门限证明，结果标签仅为recorded_feedback_prefix_reproduced，非自主成功。

CPU测试覆盖错物体、非有限/偏移命中拒绝、分歧前不执行动作、错误场景拒绝及执行器关闭；连同导航快照检查6 passed、2 subtests passed。34步计划已从真实归档生成，py_compile与diff检查通过。[准备记录与待完成条件](../../experiments/skill_visual_recovery_controlled_20261023/groc012_private_prefix_preparation_review.json)。

尚未启动仿真或新模型回合。运行前仍需隔离冻结历史执行源码与诊断差异，配置原自定义BDDL及scene overlay、原RGB和CPU/no-demo参数，并做GPU串行资源与进程监督。真实卡住根因及修复保持未完成。

### 2026-10-24：GROC-012 私有历史前缀探针启动

隔离复制历史execution_v3，原154文件清单除gt_navigation.py和omnigibson_rgb.py的诊断记录外均匹配；仅新增私有探针/重放脚本和测试，无任务、skill、规划逻辑或placement修改。独立local appdata、自定义BDDL包overlay及原scene overlay已准备，未改上游定义。首次预检因历史源码副本未含测试而未运行，复制明确的回归测试后，隔离副本44 passed、9 subtests passed。

GPU2串行锁及20GiB空闲门限检查后启动一个34步私有脚本重放，1小时进程组硬超时，无模型、非benchmark尝试。必须先验证五项输入哈希，动作/命中对象/命中点偏离立即停止；门限检查不证明完整物理相等。启动及源码清单见[独立探针目录](../../experiments/groc012_private_prefix_20261024/launch_review.json)。进程当时已核对存活，结果仍待完成审阅，不能宣称已复现或修复。

## BENCH-002：2026-10-24 私有前缀探针渲染器启动失败（未修复）

- 关联任务：杂货v2 / house_double_floor_lower / instance301 / seed0 / BDDL b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1，自定义原生BDDL、无模型私有历史动作探针，非benchmark尝试。五项输入哈希与历史一致，但不能据此证明渲染设施可用。
- 失败证据：groc012_private_prefix_20261024/probe.log明确报告activeGpu 0 is not compatible及No device could be created，video.json为0帧；尚无grounding或动作记录。因此没有执行到GROC-012放袋后导航，不是任务bug已复现或已消失。
- 配置与归因边界：本次启动器同时设置CUDA_VISIBLE_DEVICES=2和额外OMNIGIBSON_GPU_ID=0；源码中后者同时供torch设备和Kit active_gpu/physics_gpu使用，而历史run_demo_trial不显式固定后者。编号语义冲突是待隔离解释；日志也报告CUDA bad state，不能未做对照即断言唯一根因。
- 处理：核实自有进程命令及明确渲染错误后，只终止本探针进程组；supervisor记录exit=-15，两进程已退出。原日志、输入和版本保留，未启动新模型，也未重启同参数探针。[启动故障证据](../../experiments/groc012_private_prefix_20261024/render_initialization_failure_review.json)。不是因观察超时而重启。
- 修复状态：尚未修复，未改变导航或评分。下步先用最小相机首帧探针隔离CUDA/Kit编号配置并确认所用GPU UUID，再做历史前缀重放。以后任务/设施准入需首帧与渲染设备检查，不能只看CUDA上下文或场景哈希；共享机器不重置驱动或干扰其他进程。

### 2026-10-24：BENCH-002 启动GPU覆盖已用最小真实渲染对照定位

最小相同USD场景/脚本v4，两格仅OMNIGIBSON_GPU_ID不同，CUDA_VISIBLE_DEVICES均2：固定0报告No device could be created，取消覆盖实际选中GPU2 UUID22ab12a0并生成512×512 RGBA，RGB std25.70；人工查看见红立方体，立方体世界边界±0.5。确认该私有启动器额外覆盖导致本次渲染选择失败，移除覆盖有真实首帧验证。不是驱动重置或仿真器修改。

保留全部探针历史：v1自动退出0但无验证不通过；v2/v3几乎全黑未达首帧条件不通过；v4显式USD几何才满足验收。完整结果、日志哈希与图像见[对照审阅](../../experiments/bench002_gpu_render_20261024/review.json)。仅证明启动设施问题的该路径，不证明杂货全回合或GROC-012已修复。

另建groc012_private_prefix_20261024_r2，保持原冻结执行源码与任务，启动器仅移除额外GPU覆盖，恢复私有历史动作重放；原失败目录不覆盖。结果待审阅，仍非模型实验及benchmark成功率数据。

### 2026-10-24：GROC-012 完整私有前缀复现，退出障碍缩小到旁放袋遮罩

修正BENCH-002启动配置后，r2真实仿真探针34步成功/失败反馈全部匹配历史，逐步post-action env.step也全部一致，最终3001步；命中身份及位置通过0.05米门限。完整物理相等仍不由此证明，但原五次退出导航失败确实复现，且保存了六份完整网格快照（另含前段一次预期失败）。六份输入均能在CPU独立CLI中复现原错误。原始连续录像全片解码通过、SHA已存，非人工看完全片声明。

单变量离线反事实：第一次退出目标，动态网格失败、仅扩展222格；保留同静态网格及其他家具，只去掉bag81遮罩后成功得到8.804米路径，原动态遮罩重建与捕获字节完全一致。仅静态网格也能规划。后四个近地目标在原动态/静态/去bag81三种网格均无可用投影，因此不能把所有失败统一解释为袋子挡路。

已确认的是旁放后的bag81动态遮罩切断该退出目标的规划可达性，尚未证明遮罩物理几何错误或存在安全穿越路径。移除障碍仅用于诊断，未作为修复，未改导航碰撞限制或评分。下一步应检查旁放选择是否封住出口，以及物理碰撞/可见回收路径；优先验证放置后可退出约束，不让导航穿袋。探针不是模型成功。[完整复现审阅](../../experiments/groc012_private_prefix_20261024_r2/outcome_review.json)，同目录six_failure_replay_review.json与mask_counterfactual_review.json保留原输入索引。

### 2026-10-24：GROC-012 旁放保留退出通路候选（仅CPU通过，物理待验）

确认现有旁放候选筛选仅检查支撑/重叠/NextTo及固定主体距离，缺少后续退出路径检查。候选在RGBBackend生成放置前导航基底（持物排除），对拟放AABB使用原导航0.38米遮罩和同八邻域/禁切角规则，拒绝隔离原可达区域中仍空闲单元的候选；继续搜索其他满足官方NextTo的支撑位置，沉降后再验一次，失败沿用placement context回滚。保留原碰撞、支撑及官方关系判据，未忽略袋子，也未修改任务评分或skill。

改动：gt_navigation.py增加可达分量/区域保持CPU函数；omnigibson_rgb.py提供候选位置检查闭包；placement.py调用并记录blocks_exit拒绝，沉降后复验。真实捕获坏位置由实际RGBBackend检查器在CPU上拒绝，约0.17秒含前后分量检查。初组42测试/5子测试通过；增加真实采样器调用钩子后10相关测试通过，覆盖首次候选拒绝后继续搜索、沉降复验和原开门/刚体/空根目标情况。py_compile及diff检查通过。

这是候选，不是已修复结论：全区域连通约束较保守，合法备选数量和物理表现待验证；无该钩子的独立CheckedPlacement调用不改变行为。新测试最初因helper不存在收集失败，不将其夸大为原故障的行为级单测红绿证据，原故障红判据来自完整真实前缀及快照。下一步固定原26步前缀，验证新旁放关系/支撑成立并允许同一出口导航，同时检查关系保持、回滚与旧任务。版本/范围见[候选记录](../../experiments/groc012_private_prefix_20261024_r2/egress_candidate_version.json)。未启动新模型实验。

### 2026-10-24：GROC-012 候选冻结并启动物理回归（结果待验）

独立目录groc012_egress_candidate_20261024冻结历史placement1b9基线，仅叠加退出通路检查及私有诊断；未混入live版本独立的empty-root guard。冻结副本46 passed、5 subtests passed；2个依赖该独立guard的empty_root参数用例明确排除，不宣称本候选覆盖。source_manifest启动前逐文件SHA核验通过。

启动一次27步无模型私有回归，supervisor PID3304098、child PID3304135，沿用GPU2串行锁、20GiB空闲门限和1小时硬超时，移除已证实冲突的GPU覆盖。前26步要求历史grounding/反馈一致；第27步仅将原退出失败改为要求成功。额外检查旁放与退出后官方NextTo、地面支撑、空手以及底座移动超过0.5米。保留原任务、skill与评分；通过也仅证明该放置/退出路径，不计自主任务成功。[冻结与测试证据](../../experiments/groc012_egress_candidate_20261024/test_review.json)。

### BENCH-002 补充：取消GPU覆盖后provenance误记物理GPU（待修）

GROC-012候选私有回归child3304135实际nvidia-smi进程记录为GPU2 UUID22ab12a0，但backend_provenance.json写GPU0 UUID2f2ab185。确认omnigibson_backend.provenance直接读取OMNIGIBSON_GPU_ID默认0，并用该值查询物理nvidia-smi，未处理CUDA_VISIBLE_DEVICES=2且不设额外覆盖的启动配置。该元数据不能作为实际设备证明；另存带PID的实时nvidia-smi证据gpu_process_evidence.json。未改变当前冻结实验或重启进程，修复设备归属记录另行版本化。以后验收需对照实际进程GPU UUID，不能仅信任该字段。

### BENCH-002 补充修复：设备归属改为实际进程成员查询（工作区候选）

omnigibson_backend.provenance不再把CUDA/Kit默认序号当物理nvidia-smi序号：查询当前PID的GPU UUID列表，恰好一张卡才填单值gpu_uuid，多卡/无匹配填None并保留列表；查询失败/超时保留错误类型而不猜GPU0。增加5秒查询超时，保留CUDA_VISIBLE_DEVICES用于解释配置。现有gpu_index仍是配置值，不作为物理归属证明。

先对真实provenance方法构建CUDA_VISIBLE_DEVICES=2的替代nvidia-smi反馈，单卡/无匹配/多卡三例原实现均失败（误报GPU0），修改后三例及2个导入检查共5 passed。未改正在运行的冻结放置回归，未宣称已验证新版本真实模拟器；下轮启动需对照PID实际设备。源码哈希与结果见groc012_egress_candidate_20261024/gpu_provenance_repair_review.json。

### 2026-10-24：GROC-012 退出通路候选物理回归失败，未采用

候选目录groc012_egress_candidate_20261024已终止，原前25动作反馈及post-action步数全部匹配。第26动作event-00099旁放失败：沉降前候选通过连通检查，50步沉降后报Settled placement blocks the robot exit，placement context记录rolled_back；第27退出未执行。因此本候选未修好用户问题，不采用，不计模型或任务成功。26动作及2909帧连续录像保留，见该目录outcome_review.json。

仍待区分：沉降改变AABB使通路断开，或全区域保持条件对微小隔离单元过于保守。当前未记录候选前后AABB和断开单元规模，不能断言任一解释。下一轮先加最小私有诊断再改筛选/搜索；不得删除沉降复验或忽略袋子障碍。另发现子进程exit0不表示通过：backend.close可在外层repair_acceptance写出前退出应用，实际validation为prefix_diverged；验收必须看语义结果。

### 2026-10-24：GROC-012 仅增加沉降失败诊断并重放

在已拒绝候选上，仅增加egress检查的私有日志：每次AABB、robot XY、margin、原可达单元数、放置后剩余/可达/断开单元数与面积、最多20个断开单元样本；不改变候选选择条件。冻结到groc012_egress_diagnostic_20261024_r2，历史placement及任务/skill不变，也不混入独立GPU provenance修复。冻结副本37 passed、5 subtests passed，2个empty_root依赖用例排除；工作区对应39 passed。启动一次同前缀私有重放，supervisor3446816，GPU2串行门限及1小时上限；结果待验。appdata复制遇Kit截图目录读取权限，跳过该截图目录，未改变权限或原证据。

GROC-012首版失败候选录像补验：episode.mp4全片ffmpeg解码退出0、stderr为空，SHA与outcome_review一致；仅证明录像可解码，未宣称人工看完全片。见groc012_egress_candidate_20261024/video_decode_review.json。诊断版166文件哈希核验通过，与失败候选仅两个诊断函数和对应测试不同，见diagnostic_delta_review.json。

### 2026-10-24：GROC-012 诊断重放确认沉降显著移位，非少数边缘格误判

诊断版已终止，同样event-00099旁放沉降后失败并回滚。沉降前env2833袋AABB XY=[4.46528,-0.31963]..[4.65164,-0.18878]，剩余可达22729格、断开0格；沉降后env2883为[4.18489,-0.16169]..[4.39763,0.04858]，可达222格、断开22351格（55.8775平方米）。水平中心约移位0.332米。该次失败不是仅隔离少数边缘单元而过度保守；实际几何漂移使导航通路断开。

还不能把漂移归因于具体碰撞或释放：待区分机器人本体/手部碰撞、残留抓取动力学、目标/负载接触等。保持碰撞、沉降与连通判据，不删除后验检查。完整前后数据和录像SHA见groc012_egress_diagnostic_20261024_r2/outcome_review.json。仍未修复、未采用候选、无自主任务成功结论。

GROC-012沉降假设进一步约束：本回合provenance明确controlled_pose_carry、fixed_joint=false；最后env2827抓袋记录contained_objects=[]、payload_relations=[]，因此不能继续把固定抓取关节或被追踪负载闭包作为主要解释。未排除机器人/固定设施/未追踪物体接触。代码旁放候选显式跳过robot及非目标fixed_base物体是已确认检查缺口，但尚无实际接触证据，不能直接宣称其导致位移。证据见settling_hypotheses_review.json；下次定位记录link AABB与物理接触，并保存操作前状态供同进程有界对照，减少整段重放成本。

### 2026-10-24：GROC-012 接触诊断探针冻结并启动

保留上次已拒绝候选的判断和动作，仅私有脚本在event-00099前保存sim.dump_state及部分carry字段，记录沉降前与前5、10、25、50步的袋子AABB/位置、所有link AABB相交项和实际RigidContactAPI current_only接触对。第0步接触可能是采样移位前的旧缓存，明确标注；AABB相交不直接当物理碰撞。检查点尚未验证独立恢复，不声称完整回合快照。

冻结目录groc012_contact_diagnostic_20261024，11相关测试通过、2个empty_root用例排除。supervisor3591010及子进程存活已核验，GPU2串行门限、1小时硬上限，无模型。原任务/skill/评分及判断规则不变，不计benchmark成功率。结果待审阅。

### 2026-10-24：GROC-012 接触诊断结束，候选与机器人link边界重叠

第0步候选AABB与robot_r1/base_link重叠；第1至5步仍与base_link及wheel/steer_motor_link2重叠，第10/25/50步无AABB重叠，袋子逐步漂移到此前失败位置。实际RigidContactAPI current_only记录只报告地板，未报告机器人，因此只确认几何边界重叠，不能声称物理碰撞根因已证实；机器人接触报告覆盖尚未证实。event-00099仍失败回滚。

操作前sim状态和部分carry字段已保存（84KiB），独立恢复尚未验证，不当作完整回合恢复能力。下一项单变量对照：候选拒绝当前机器人link AABB重叠，保留原支撑/关系/沉降/退出判据，检验是否避免漂移；若使用检查点，先以原代码重现相同候选和沉降轨迹，不能直接把恢复后通过当修复。证据见groc012_contact_diagnostic_20261024/outcome_review.json。

### 2026-10-24：GROC-012 机器人link重叠拒绝候选（物理待验）

先给真实旁放采样器加入复现：以第一次接受位置作为机器人base link AABB，再从原物体位姿重试。原代码三类目标均再次选中重叠位置，3 failed/3 passed。候选仅在原碰撞过滤增加当前robot各link有效AABB的相交拒绝，沿用原0.005米相交容差；地面ray仍可忽略机器人用于找支撑，不再因此允许释放进本体。其余支撑、官方NextTo、沉降和退出后验不变。工作区17相关测试通过，冻结历史副本15通过、2个empty_root依赖用例排除。

冻结目录groc012_robot_overlap_candidate_20261024，相对前诊断版仅placement机器人link筛选和对应测试改变；未混入独立empty-root guard或GPU provenance修复。完整原前缀物理对照启动，supervisor3679369及子进程已确认存活，GPU2串行/内存门限、1小时上限，无模型。尚未采用或宣称修复；单测证明边界筛选行为，不证明沉降或全任务成功。未直接使用未验证恢复能力的检查点作为通过证据。

GROC-012接触诊断录像全片ffmpeg解码通过，SHA及验证范围保存在contact目录video_decode_review.json；机器人link候选已验证五项任务/场景输入哈希与原诊断完全一致、demo_motion=false，见候选input_comparison_review.json。两者不构成候选物理通过证据。

### 2026-10-24：GROC-012 机器人重叠筛选物理对照仍失败，搜索耗尽

robot_overlap候选已终止，前25动作反馈和步数全部匹配。event-00099未选择沉降位置，返回sampling_error并回滚；100候选拒绝统计：无地面22、重叠43、固定主体过远28、阻断退出7、not_next_to0。退出动作未执行。增加机器人link筛选避免原重叠候选，但当前采样范围未找到同时合规的位置，因此不是用户问题已修复，也不是证实场景无解。未采用为完整修复。

下一步区分合法位置采样覆盖不足与当前机器人站位限制，保持原碰撞/官方NextTo/支撑/退出判据及评分。不能为通过而忽略本体或删除沉降复验；若确需换站位，应由动作协议明确返回可恢复失败并由模型导航重试，而非私下把底座移走。证据和录像SHA见groc012_robot_overlap_candidate_20261024/outcome_review.json。

### 2026-10-24：GROC-012 操作前检查点单步恢复验证启动

新增私有probe_checkpoint_placement.py，以原无robot筛选的失败版本加载保存的sim状态、持物对象和carry相对位姿，恢复steps并令相机缓存失效；只执行event-00099。必须通过原像素grounding门限、重现postcondition_error，且沉降前后AABB各坐标误差<=0.0001米、accepted及disconnected_cells完全相同。未恢复全部Python任务/回合状态，因此通过也只验证这条局部诊断链，不作为自主或整回合恢复能力。

独立冻结groc012_checkpoint_validation_20261024，仅新增私有脚本，原候选设施不变；py_compile及3项prefix测试通过，实际恢复尚待验。supervisor3760833及子进程存活已核验，仍GPU2串行/内存门限和1小时上限，无模型。

### 2026-10-24：GROC-012 检查点物理等价验证失败，禁止当验收捷径

checkpoint_validation已终止，grounding通过，初始egress候选的完整诊断与原记录一致；但沉降后先报Object is no longer next to the selected target，未到原来的Settled placement blocks the robot exit判据。只有1个egress检查，参考有2个；私有验收明确private_acceptance_failed。即使错误code同为postcondition_error，也不是同一故障复现。

该sim+部分carry检查点不足以证明物理续跑等价，不能用于修复通过或完整回合恢复声明。未定位缺失状态，不推断唯一原因；后续验收继续完整前缀。保留检查点和失败证据，见groc012_checkpoint_validation_20261024/outcome_review.json。

### 2026-10-24：GROC-012 CPU周边覆盖筛查不能替代地面可放判据

使用原首次退出静态网格及其他家具遮罩，移除袋遮罩作为放置前基底，以已捕获冰箱主体bounds和沉降前袋XY尺寸，在三种边缘间距、19个分数点、四侧共228位置做相同0.38米margin连通检查（25.54秒）。178个不切断通路，但这些位置的中心全在机器人膨胀后不可行走格子。不能由此宣称无合法放置点：机器人可行走区域与小物体地面支撑不是同一判据，且未验证本体/墙面碰撞、ray support或官方NextTo。

原采样仅每边三分数点且基于含门扇的目标union；下一步考虑有界增加固定主体边缘候选，保持原候选顺序及所有关系/支撑/碰撞/沉降后验，完整物理前缀决定结果。CPU脚本和全部位置保留于robot_overlap候选cpu_perimeter_scan.py/json。

### 2026-10-24：GROC-012 固定主体边缘补充采样候选启动（待物理验）

原采样后追加有界固定主体边缘候选：约5厘米分段、每轴最多32段、含端点、四个间距(.02/.06/.12/.20米)，最多528个额外候选。优先顺序和全部碰撞/支撑/官方NextTo/固定主体距离/沉降/退出判据保持。真实采样器窄支撑条用例原3 failed/3 passed，新增覆盖后工作区17相关测试通过。

独立冻结groc012_body_perimeter_candidate_20261024；初次移植测试捕获历史代码没有live的body_bounds变量，未启动仿真；改用同一历史root_link.aabb直接取边界后15 passed、2个empty_root用例排除，保留初次失败日志。未混入独立empty-root guard。supervisor3814308及子进程存活已核验，仍完整原前缀、GPU2串行门限和1小时上限，无模型。候选未采用，物理表现及是否需要改站位待结果。

### 2026-10-24：GROC-012 主体边缘候选旁放通过，退出在最终转向耗尽预算

body_perimeter候选完整前缀已终止，validation为prefix_diverged，不能凭进程exit0宣称通过。event-00099旁放成功：官方NextTo、地面支撑、空手成立；沉降前后退出连通检查均通过。随后event-00106规划8.804163米路径，实际走到目标XY；3535步首次进入位置容差，3573步预算耗尽，位置误差7.87e-7米，最终朝向误差0.076165弧度（要求0.001）。690运动步对应700动作预算预留10步沉降；最后记录仍按约2度/步收敛，未见此次门口停滞证据。按30Hz标称速率还需约3个转向控制步，但这是估计，不是物理验收。

因此原“旁放堵路”在此候选中已避开，整条退出及退出后袋子关系验收仍未通过；不改原分数、不静默加预算，也不把该私有脚本当自主成功。证据：experiments/groc012_body_perimeter_candidate_20261024/outcome_review.json、probe/validation.json、navigation_plans.jsonl、base_motion.jsonl。下一步以原预算验证可见中间导航点或检查路径转向成本，区分应由agent分段的长路径与执行器缺陷，再做其他任务回归。

### 2026-10-24：GROC-012 导航预算分解，未证实新的跟随器缺陷

对body_perimeter原冻结跟随器执行CPU timing-only重放，输入原8.804米路径及每步实际姿态。重建命令与记录最大误差9.99e-7米或弧度；690步分为508 advance、182 turn。理想反馈全路径需要693步（508 advance、185 turn），同样超过690运动步额度。实际最后朝向仍收敛；没有据此增加转速、放宽朝向或静默提高原700动作预算。

此重放有意以无障碍stub替代grid，只用于命令与时序，不构成碰撞、退出后物体关系或物理通过证据。脚本和分路点成本保存在body_perimeter候选analyze_navigation_budget.py、navigation_budget_review.json。当前视觉skill已有短段导航及看新图再继续，无证据支持新增重复条文。后续物理验收应包含合法的超时后新图恢复；模型试验须保留原预算及所有失败调用，不能将此近终点失败删出统计。全片录像ffmpeg解码、SHA及原前25动作反馈/步数一致性已验证，分别见video_decode_review.json和full_prefix_comparison_review.json。

### 2026-10-24：GROC-012 原预算超时后当前RGB恢复探针启动

新增私有probe_placement_recovery.py，保持body_perimeter设施字节和原前27动作不变；第27动作明确预期action_timeout，保留失败，不把预算加到成功。超时后重新observe，从右侧新图点(.35,.60)续走700步，要求grounding选中floor，否则立即拒绝；随后复查袋NextTo、地面支撑、空手及底座离开原位置超过0.5米。该点由人工查看上轮末尾RGB选择，绝非模型自主决策，新的视图/物理状态不同可能使选择失效。

冻结目录experiments/groc012_timeout_recovery_20261024，清单仅增加该脚本，冻结11项相关测试通过、2个独立empty_root用例排除。首次工作区测试误导入另一editable checkout而收集失败，改为明确PYTHONPATH=src后冻结测试通过；不将失败运行冒充测试通过。supervisor3963285启动，GPU2串行门限、1小时墙钟上限，未调用模型。物理结果待验；不能由此推断全任务成功或skill收益。

### 2026-10-24：GROC-012 沉降后阻路拒绝与回滚测试加固

真实旁放采样器增加settled_blocker分支：初次egress接受、沉降后拒绝，必须抛出对应postcondition_error，经placement_transaction恢复物体原位姿，并且日志没有committed/next_to_verified。覆盖开门铰接、刚性及empty-root目标。私有副本只删除沉降后egress guard时3项全部红；恢复原代码9项全绿，工作区含连通检查13项全绿。证据experiments/groc012_settled_rollback_regression_20261024/{mutation_red.log,original_green.log,mutation.json}。本轮仅加强回归测试，运行中的冻结物理副本未变；mock模拟器只验证事务恢复调用及位姿，不证明真实sim完整持物状态回滚。

超时恢复探针的实际child3963290 GPU进程归属及全部冻结源码哈希已核验，见对应runtime_source_gpu_review.json；原backend GPU字段仍有此前记录的历史元数据缺陷，未因此改写快照。跨任务真实回归仍未完成，餐后收拾任务目前只有requirements设计，不能列作已可运行的迁移验证。


### 2026-10-24 HOT-005：无 skill 对照 seed0 未完成及审计拒绝调用缺陷

任务prepare_and_serve_hot_drink，house_single_floor/301，seed0；任务哈希4be5469c5eff25f59504c90c4d78d04312057353ae35a0bf007012c2d0a9b915，独立high_level_hot_drink_state_v1。用户要求无workflow skill但每次tool前公开意图；保留相同规划工具，移除skill加载与读取。本次108/120动作、48失败，最终2/11，约100.4分钟策略墙钟。135次模型工具调用均有decision，无skill调用或加载bundle。

模型多次提前blocked被共同规则拒绝，在配置允许的续跑后仍未正式finish，最后监督器aborted。归类为未完成且收尾失败的尝试，不冒充完整闭环；全调用对齐因额外监督器finish失败，剔除此明确监督器调用后模型序列严格对齐。录像通过。

审计另遇event-00169：act携带finish形状参数缺primitive，被正常拒绝；审计在检查ok之前索引primitive触发KeyError。仅修复实验目录postrun_analysis副本：失败非法调用不提交语义，成功但缺primitive必须报错。最小拒绝/成功两例回归通过，真实轨迹18条私有状态重放全部一致；原始冻结源码哈希未变，原始故障与综合审计失败均保留。未更改任务、执行器或skill，未重跑替换失败。

证据：experiments/hot_drink_no_skill_20261024_s0/{REPORT.md,result.json,efficiency.json,audit_repair_notes.json,trial/}。后续审计应覆盖非法tool参数，区分模型意图记录、监督器收尾和全任务成功；单seed不能推出skill收益。

### 2026-10-24：GROC-012 旁放→退出超时→当前图续走私有链通过

超时恢复探针已结束，validation为recorded_feedback_prefix_reproduced，共28动作。前27动作反馈及仿真步数与body_perimeter原候选完全一致，包含原event-00106的action_timeout；未增加每动作700步预算，也未删除失败。新observe的rgb-00028-right人工选点(.35,.60)实际grounding命中floor，新增navigate_to成功，240仿真步、57.505秒工具时间。最终底座距旁放位置4.951米，袋NextTo=true、floor_supported=true、hand_empty=true；证明此次旁放后的合法RGB动作恢复链可执行。

该单次私有链使用人工目标，不证明模型会自行恢复、完整杂货任务成功或skill收益。设施仍未采用：新版本两袋开关门关系、其他目标/任务、固定策略完整自主回归和留出验证仍待做。原历史分数保持。证据experiments/groc012_timeout_recovery_20261024/{outcome_review.json,probe/validation.json,video_decode_review.json}；完整录像ffmpeg解码通过且SHA匹配，仅做完整性验证，不宣称人工看完全片。

### 2026-10-24：GROC-012 两袋共存与开关门保持回归启动

在已冻结body_perimeter设施上新增私有probe_bag_next_to_suite.py，复用GROC-003私有卸载/底座预置、同一冰箱选中点和真实抓取/旁放/门状态helper。依次旁放bag81、bag82，每次做placed-open/close/open/close四次快照，第二袋阶段同时检查第一袋；每次要求空手、所有已放袋NextTo和floor OnTop。保留旁放器沉降/egress判断，未忽略另一袋障碍。不宣称RGB模型执行、导航或全任务成功。

独立冻结experiments/groc012_two_bag_retention_20261024，仅新增探针，脚本及launcher语法检查通过；同一GPU2串行门限、独立appdata、1小时上限，supervisor4043495已启动。旧两袋独立探针不能替代此版本共存回归。结果需查probe/result.json及每条快照而非exit code；物理表现待验。

### 2026-10-24：GROC-012 两袋共存开关门机制回归通过

two_bag_retention已终止，probe/result.json明确passed；8阶段共12次bag-stage检查，所有官方NextTo、floor OnTop、空手均真。bag81 placed-open/close/open/close后，bag82旁放并重复开关门，后一阶段同时复查bag81，未见关系失效。168冻结文件哈希核验、录像SHA及整片ffmpeg解码通过。仅为同场景私有预置/直接helper机制回归，无导航、模型、留出或跨任务成功声明。

证据experiments/groc012_two_bag_retention_20261024/{outcome_review.json,probe/result.json,video_decode_review.json}。该短探针未及时保存实际PID的nvidia-smi GPU归属，冻结backend仍有历史GPU元数据缺陷，不能把其UUID当已验证实际卡；保留此来源限制。主体采样候选已具旁放/退出恢复及两袋共存关门保持证据，但其他目标、固定策略完整自主回归尚未完成，仍未宣布设施版本正式采用。

### 2026-10-24：GROC-012 同场景桌/椅目标形状回归启动

probe_bag_next_to_suite增加独立targets模式，已通过的doors快照保持不变。使用同一候选设施和私有空袋/底座预置，依次在breakfast_table_xftrki_0及straight_chair_uofiqj_0旁放bag81，检查放置后及再推进100真实控制步后的官方NextTo、地面OnTop、空手。保留执行器全部碰撞、支撑、沉降、egress判据；选点为目标AABB中心，明确是私有几何helper回归，不是RGB自主选点。

独立冻结experiments/groc012_other_targets_20261024，仅修改探针模式，任务/场景和候选设施不变；syntax检查通过，supervisor4059591启动，GPU2串行、独立appdata及1小时上限。两目标顺序测试使用同一场景，不声称状态完全相同或旧新因果对照；不计作跨任务迁移，物理结果待验。

### 2026-10-24：GROC-012 桌/椅保持回归通过，单轮固定策略自主设施回归启动

other_targets探针已终止，四阶段均通过官方NextTo、floor OnTop、空手；桌/椅各放置后及再推进100步保持。168冻结文件、录像SHA及全片ffmpeg解码通过；运行中实际PID4059594属于GPU2 UUID22ab12a0-af14-fa8c-44a7-a869af82e244已捕获。仅同场景目标形状机制回归，非跨任务迁移。证据experiments/groc012_other_targets_20261024/outcome_review.json、video_decode_review.json、gpu_process_evidence.json。

预注册单轮完整自主设施回归experiments/groc012_autonomous_facility_regression_20261024，冻结v3原11个skill文件和bundle5fd7031b、用户指定gpt-6.1-sol low、301/seed0、原BDDL/场景/评分、120动作/20000步/14400秒及700每动作预算。只评价GROC-012设施候选，不宣称skill ablation；旁放退出恢复、两袋门状态保持、桌椅回归的review和录像证据哈希均绑定facility_review。没有安排第二模型cell。

supervisor4084695启动，GPU2串行及20000MiB门限、独立appdata、清除OMNIGIBSON_GPU_ID覆盖，沿用完整正式finish/独立评分/调用对齐/录像审计。模型失败不删除，候选尚未采用。原冻结backend GPU元数据缺陷仍须用实际进程归属单独记录。结果待验，不能以已启动替代自主成功。

GROC-012自主回归启动实证补充：simulator4084843实际GPU2 UUID22ab12a0-af14-fa8c-44a7-a869af82e244，168冻结文件和本轮task_spec哈希匹配；运行profile planning、bundle5fd7031b，controller实际model gpt-6.1-sol、model_reasoning_effort=low已核验。模型已initialize并自主读取skill。相对原v3运行源码仅placement.py、omnigibson_rgb.py、gt_navigation.py改变，其他新增为私有探针/测试，见baseline_delta_review.json。startup_runtime_review.json与controller_startup_review.json分别记录初始化和控制器就绪阶段，不将前者暂时null的bundle误报为未加载。

### GROC-013：杂货完整自主回归在首次错误抓取后提前blocked（2026-10-24，未解决）

任务/场景carrying_in_groceries/house_double_floor_lower、301/seed0，原双柜BDDL b38c56b6、固定v3 bundle5fd7031b、gpt-6.1-sol low，设施为GROC-012候选。trial groceries_v2_groc012_facility_gpt61sol_low_20261024_r1正式结束：独立评分false、3/12；3预算动作、1失败、288仿真步、导航2.780米。两次navigate成功后，event-00020在rgb-00003-front选(.09,.28)抓取，grounding命中car_ssxsje_0、fixed_object；画面确认该点是红色车身板件。模型随后直接finish blocked，理由为遮挡及不能可靠识别，无恢复动作。

分类：已确认错误视觉选点和早停；未证实抓取器/仿真器错误。四路末尾图有可见地板，不等于已验证换位能成功，不据此声明完整可恢复性。未进入旁放，因此不能用本轮宣称GROC-012候选有效或无效；失败保留计入本次自主设施尝试，不补跑后挑选成功。调用对齐、无demo、显式导航和正式收尾完整性通过；审计overall=false包含任务/目标动作失败，不能称全审计通过。连续录像完整解码及SHA核验，运行时src/skill快照核验通过，人工只检查关键四图。

证据：experiments/groc012_autonomous_facility_regression_20261024/{outcome_review.json,workflow_efficiency.json,runtime_final_source_review.json}、trial/{summary.json,demo_audit.json,pre_grasp_four_views.jpg}及原始事件/grounding。防复发造任务检查：区分“当前像素命中固定遮挡物”和“所需物体不可发现”；评估应保留视角恢复与诚实blocked，不靠私有选点或更改场景替模型完成。后续按实际选点/恢复失败审阅已有skill是否被读取/执行，再决定候选；本轮尚未改skill。

GROC-013候选补充：read_skill调用缺少视觉探索/恢复资源，已有system prompt恢复要求但未执行；仅准备主流程触发语候选，未改执行器。单文件差异、bundle、结构验证与待验行为见skill_iteration_20261021.md最新条目及experiments/skill_recovery_routing_candidate_20261024/plan.json。未声称根因或修好。

### 2026-10-24：GROC-013复用统计补充实际skill资源交付

analyze_workflow_efficiency.py新增skill_read_timeline及成功/拒绝/未返回计数，保留call/response ID、资源名和bundle；成功响应仅证明资源交付，不证明遵循或因果效果。新用例在原实现KeyError红、修改后含原动作/恢复窗口计数共5测试绿；真实GROC-013复核成功读取3项、无failure-recovery读取。首次临时monitor将拒绝读取记null的问题已单独纠正，原事件不变。

后处理工具/测试及SHA冻结于experiments/skill_recovery_routing_controlled_20261024/{analysis_tools,analysis_version.json}，红绿日志和prior_failure_exposure_review.json保留。运行中的模型两组源码/skill/设施未改，新增后处理不改变评分或本批实验变量。以后报告skill收益须分别记录bundle装载、实际资源读取、行为遵循及独立任务成绩，避免用已装载bundle冒充读过某条规则。


### 2026-10-25：仅消融长程skill的三场对照完成

完整skill历史参照3/3成功，目标均11/11；其他四skill保留、仅去长程skill的补跑0/3，seed0/11/22分别1/11、0/11、0/11。平均动作112.3对114.3，平均失败动作15.7对60.7（约3.87倍）。三场均加载冻结四skill bundle，其他skill字节不变、九个规划工具保留，机器人调用前decision检查通过，录像与独立状态重放通过。按需读取不等于全部采用：补跑没有读failure-recovery，不能声称四项全被使用。

协议限制：seed0在103动作后未正式模型收尾，由监督器aborted；seed11有两次读取不存在服务器x资源（均报错）和一次资源枚举（空列表），是机器人接口外的客户端调用。两场原始全调用对齐审计失败保留。剔除明确监督器/客户端额外调用后，三场机器人调用严格对齐。seed22全调用对齐且正式收尾，仍0/11。所有失败保留，不补抽。

结论仅为本组描述性证据支持保留长程skill，不是严格因果证明或泛化结果：历史参照、每组3个开发seed、共享主机及服务时间未控制，并有上述协议偏差。全部关闭skill单场不进入本次分母。本轮未改生产skill、任务或执行器。报告与逐场录像：experiments/hot_drink_long_horizon_ablation_20261024/REPORT.md；核验：final_evidence_review.json。报告中发布链接前缀已纠正为实际发布目录。


### GROC-014：杂货袋放置失败后被误记为已放下（2026-10-25，未解决）

任务：carry_in_and_put_away_groceries/v2_design，原生BDDL301/seed0，planning协议，固定gpt-6.1-sol low；恢复触发语四格的baseline/r2，bundle5fd7031b，设施为未晋升GROC-012版本。独立4/12、36预算动作、9失败、正式blocked，不是成功回合。

复现证据：experiments/skill_recovery_routing_controlled_20261024/baseline_r2_state_drift_review.json保留原始调用与结果、私有grounding及已人工查看的baseline_r2_placement_targets.jpg。event-00107成功抓到paper_bag；00110和00116放置均out_of_reach；00122决策却称“袋放在桌面上”，其选点实际落在冰箱而非袋内；00125继续抓取返回hand_occupied。首次放置选点在桌面轮廓外的地板，第二次确为桌面但超距。RGB中袋子与桌面重叠不证明已经释放。

分类：已确认agent视觉选点及状态更新失败，未确认harness/仿真器缺陷。现有long-horizon-planning第5条已要求依据反馈更新手和放置状态，因此不能声称缺少所有相关指导；目前不重复添加通用提示，不放宽reach或成功判定。未实施行为修复，完整自主恢复未验证，案例保持未解决。

造任务复用：验收工具时检查失败放置的持物状态与实际反馈一致；保留逐调用证据，区分视觉接触、成功放置和仍在手中。任务/skill回归须检查失败后的belief更新，不能用截图贴近支撑物替代释放证据。私有grounding仅用于事后诊断，不反馈给策略。

### 2026-10-25：GROC-013恢复触发语四格收尾补充

两组均0/2成功；candidate两场均未成功读取被改workflow，四场均未读failure-recovery，不采用候选。五个拒绝read_skill请求都只有decision；四场真实MCP预检schema哈希均与冻结完整planning目录9a411311一致，其中明确要求name/resource/decision。此证据排除MCP目录漏字段，不能证明模型注意力或客户端内部未知转换。详见schema_delivery_review.json、rejected_skill_arguments.json。四场主episode录像SHA256及完整ffmpeg解码均通过（full_video_decode_review.json）；不等于逐帧语义审查或整体task audit通过。


### 2026-10-25：恢复触发语四格的导航失败离线排查（未认定修复）

experiments/skill_recovery_routing_controlled_20261024/replay_navigation_failures.py重放20份保存的无可见性过滤器导航快照；20/20均复现原navigation_unreachable。以各自static_grid移除动态遮罩后，20/20仍navigation_unreachable；因此此批失败不支持“袋子动态占用造成不可达”的解释。输入为实际冻结规划器及原current/hint/standoff/snap/search限制，结果保存在navigation_failure_replay_review.json。它只验证规划输入与输出一致，不能证明静态地图正确、目标选点合理或物理上无可行路线；下一步需核对RGB选点、静态连通区域及真实通道。未改地图、执行器、评分或动作预算。

四格终态补核：每场99运行时源码/skill文件匹配对应冻结清单，模型gpt-6.1-sol low、planning、bundle和BDDL均一致，见runtime_validation_all_cells.json。复现与来源核验是诊断证据，不是全任务成功。


### 2026-10-25：用户调整优化重点，冻结skill，优先执行器设施

用户明确“现在应该不太需要改动skill，只需要改执行器等等”。后续停止新增skill候选/实验；已经启动的持物状态四格保持输入不变并保留结果，作为后续设施诊断材料。当前仓库skill内容哈希及诊断顺序保存在experiments/skill_hand_state_controlled_20261025/executor_focus_20261025.json，保留此前未采用候选，不将其晋升。

设施排查先复核同一目标导航成功后仍超距，以及搬运/暂存期间物品位移。当前代码已有公共对象导航终点1.35米上界与操作选点1.4米gate，不能把GROC-010旧问题的同一方案重复声称为新修复；需检查真实导航终点、后续像素对应身份/位置变化与失败抛错位置。搬运问题需区分在手容器内容稳定、落地容器被底盘碰撞和放置后静置不稳定。只有经复现确认设施根因才改实现，分别冻结设施与策略版本，保留红绿回归和独立任务验收。


### 2026-10-25：GROC-008 / GROC-010 当前设施排查的首个超距样本

仍在运行的hand-state candidate/r1：event-00027导航成功，选点grounding为car_ssxsje_0的trunk；event-00030抓取选点为paper_bag_81。操作前最后实际底盘XY[-6.0,5.55000019]；距导航表面0.75885米，距后续袋表面1.65915米，超过1.4米操作gate。该对动作是目标身份/表面改变，不复现同对象导航终点超距，不支持放宽reach或自动替模型接近。证据experiments/skill_hand_state_controlled_20261025/candidate_r1_first_reach_failure_review.json含原私有grounding和底盘采样；仅事后诊断，不向策略反馈。模型回合继续，未改冻结输入；设施修复仍待真正红色复现。


### 2026-10-25：GROC-010排查补充——同一大地板对象不代表同一表面点

当前hand-state baseline/r1 event-00067导航后00070旁侧地板放置超距：二者grounding均floors_zqjkvm_0，但所选XY点相隔1.48543米。操作前最后记录底盘[1.35000014,3.14999962]，距原导航点0.20362米，距放置点1.41419米，后者超过1.4米gate。证据experiments/skill_hand_state_controlled_20261025/baseline_r1_first_placement_reach_review.json。这不是已证明的导航终点约束失败，未放宽reach。复用诊断必须同时核对对象身份、所选表面点及操作前实际底盘，尤其地板/大台面；只按同实例合并会误判设施bug。底盘记录为动作前最新运动采样，非新物理反事实实验。首次诊断读取混合base_motion记录遇到无at行，已按有时间戳且actual_position字段筛选；未修改原日志或运行代码。


### 2026-10-25：执行器步进耗时诊断拆分（仅可观测性，不宣称提速）

当前hand-state candidate/r1一次navigate_to总114.974秒，其中physics_and_metrics合并75.883秒/236次；现有合并计时不能判断env.step或指标更新的耗时。修改src/manipulation_agent/omnigibson_backend.py::_step，在原环境步进和指标循环分别记录environment_step、evaluation_metrics_step；保留原physics_and_metrics外层、env.step→steps+=1→所有metric.step顺序、全部实参/结果及异常传播。没有跳过物理、渲染、评分或指标，没有声称瓶颈根因已确认或速度改善。

真实适配器_step的CPU fixture检查通过：调用顺序和对象参数不变、计步正确、环境异常不吞且不额外计步、两个私有timing正常记录。源码快照、哈希与范围见experiments/executor_step_timing_20261025/review.json。计时嵌套不可相加；后续正常新运行即可获得分项，当前冻结回合不变，不专门启动模型实验。此项属于benchmark执行设施诊断增强，不是任务失败修复。


### GROC-015：place_on_top把在手物体自身当作支撑面送入采样器（2026-10-25，前置校验已修）

任务：carry_in_and_put_away_groceries/v2_design，301/seed0，planning、gpt-6.1-sol low，hand-state baseline/r1，原冻结bundle5fd7031b、BDDL b38c56b6、未晋升GROC-012设施。event-00085误抓bag81（仍含2件），00088意图放回桌面，但rgb-00025-front[.78,.69]实际选中手里的bag81；00089返回sampling_error。随后换选真正桌面，采样与postseat均通过，证明不能把此前失败归为桌面没有任何可行位置。

已确认工具/执行器校验缺口：_checked_place_on_top没有held is target校验，将“自己放到自己上面”交给采样器，导致误导性采样错误；place_inside已有对称检查。修改src/manipulation_agent/executors/placement.py，在进入采样前返回unsupported_relation，changed=false，保持持物、原选点与任务不变。移除该wrapper不使用的两项OmniGibson import。没有自动改点、扩大采样或放宽碰撞限制。

回归：新增tests/test_placement_self_target.py自支撑请求用例，原实现未提前拒绝而进入模拟器import导致红；真实任务日志另外提供实际sampling_error证据。修改后self-target、placement、surface_seating、placement_egress共15测试通过。单测验证前置拒绝且不改状态，不证明整场成功或更高抓取率。当前运行快照不变，无专门模型实验。逐事件、grounding、源码/测试快照哈希见experiments/groc015_self_surface_guard_20261025/review.json。

造任务复用：动作工具应在采样前拒绝物理不成立的自关系，错误分类应区别无效目标与合法目标采样耗尽；同时保留held container选点可能遮住真实支撑面的失败，不以oracle自动替换目的地。
