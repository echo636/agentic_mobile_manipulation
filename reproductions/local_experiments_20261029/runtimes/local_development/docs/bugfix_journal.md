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
| GROC-018 | 近期杂货回合遗漏工具参数、无finish文本结束后丢失原会话 | 上游返回漏参；harness续接缺口 | 核对实际发出的schema、上游参数与本地参数；恢复同一episode及完整线程 | 同线程恢复后54动作、3/12失败保留；直连排除观察器干扰进行中，深层原因未定 |
| GROC-019 | “开门”点中地面/墙，却报超距或占手 | RGB执行器前置校验顺序 | 分别验收能力不匹配、合法目标超距和占手反馈 | 28测试/4子测试通过；完整回归独立冻结 |
| GROC-020 | 无primitive的无效act导致失败实验审阅页无法发布 | 发布器动作列表显示 | 保留缺失项及原始失败审计，展示“缺少 primitive” | 真实发布和回归先红后绿；6轮审阅入口已核对HTTP200 |
| GROC-021 | 不同结束监督使热饮“跑满”、杂货“提前停”看似模型退化 | 实验协议/归因 | 比较首次finish及所有拒绝，固定结束监督与完成反馈 | 热饮24步首报blocked、共6次被拦；杂货54/4步正式blocked直接接受 |

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


### 2026-10-25：持物状态候选r1提前结束，独立目标未较初始状态改善

杂货v2_design，301/seed0，planning、gpt-6.1-sol low，hand-state candidate/r1，bundle2c4ac95d、BDDL b38c56b6。27动作、5失败、导航28.679米，模型正式finish(blocked)；独立task=false、3/12。四个等价绑定的最终goal_options均与initial_goal_options完全相同，因此3/12来自初始已满足条件，不能当作搬运/收纳进展。finish反馈尚余12684.7秒，模型理由仅列未完成义务，不能据此确认不可恢复的设施阻断；归类为agent提前终止，未确认新执行器bug。

证据：experiments/skill_hand_state_controlled_20261025/candidate_r1_terminal_review.json保留初末评分、预算、原finish事件及源文件SHA256；candidate_r1_workflow_efficiency.json保留动作序列。视频完整解码通过，整体审计因任务失败及未声称achieved而不通过，接口/动作来源检查通过。原失败保留，不补抽，不宣称少动作是效率提升，也不为skill补丁新增专门实验。

造任务复用：同时保留initial_goal_options与final_goal_options，区分初始已满足的安全/关闭条件和真正完成的任务进展；诚实报告未完成与有证据的外部阻断分开评估，不能用blocked标签替代根因。


### REPRO-HOT-001：热饮长程对照的实验整体依赖清点（2026-10-25，历史缺口保留）

范围为完整五skill历史seed0/11/22与保留四共同skill、移除长程skill的三场。不是新的skill候选、任务修复或模拟器实验；当前工作区较新的杂货修改没有追溯应用。两组254/252个冻结文件及六场run/controller的实际src快照核对通过，真实prompt和九工具集合已提取；四共同skill9份文件相同，六场298项包版本相同。

发现实验基础设施追溯缺口：BEHAVIOR-1K提交b1979916ec1549b10a4e65e630bc6504a9af1b00之外，当前存在17个tracked修改与5个未跟踪代码/任务文件。包括上游env_base的GPU水初始化后转CPU开关、接触缓存/设备兼容，以及tasks/__init__.py直接导入的未跟踪partial_order_behavior_task.py。已保存当前patch及22个文件、SHA256并在基准文件上验证补丁应用，未修改上游。六场只保存commit、没有当时dirty patch；当前mtime早于运行不是历史内容同一性证明，缺口未解决。

修复后的实际configured_scene与六场记录哈希匹配并保存；场景地图/metadata部分仅能当前捕获，大型USD/材质/纹理/机器人/系统资产闭包仍需冻结。runner继承os.environ，历史完整生效环境缺失；298包版本不是可安装锁，原绝对路径不是便携入口。保留seed0监督器收尾及seed11额外客户端调用的原始失败审计，不替换为全通过。

材料：experiments/reproduction_inventory_hot_drink_20261025/REPRODUCTION_INVENTORY.md、INVENTORY.json、SOURCE_LINEAGE.json、SIX_TRIALS.json；package_staging含756文件/12,697,219字节，RAW_EVIDENCE_INVENTORY记录9,367文件/3,477,706,619字节及逐文件SHA256。代码暂存包全文件哈希、非法act缺primitive审计回归、已记录结果表3/3对0/3重算通过；未执行干净机器安装、完整日志重新独立评分或新物理实验，未上传。publication_ready=false。

预防：今后每次启动冻结我们自己的源码、上游base+dirty patch+untracked、任务和修复后场景、地图及资产闭包哈希，显式记录启动生效开关及安装环境；分别冻结分析版本和原始证据，不以commit、符号链接或当前最新版代替当时完整运行输入。完整清单亦存docs/hot_drink_reproduction_inventory_20261025.md。


### 2026-10-25：GROC-008 / GROC-010 同一汽车不同关节表面的超距补充

hand-state baseline/r2，杂货301/seed0、planning、gpt-6.1-sol low、bundle5fd7031b、BDDL b38c56b6。event-00016导航选中汽车base_link表面，00019开门选择trunk表面并out_of_reach；开门前底盘[-4.25,5.44999981]，距两选点XY分别0.87344米和2.02154米。两次均同一汽车实例，但选点和关节不同；不构成原导航选点终点超距证据。未改执行器、操作距离或冻结输入。原始grounding、调用及底盘记录见experiments/skill_hand_state_controlled_20261025/baseline_r2_first_open_reach_review.json。复用检查需比较选中关节及实际表面点，不能仅匹配物体实例；尚未证明完整开门链成功。


### 2026-10-25：杂货提前结束案例促成统计工具补充初始状态对照

依据hand-state candidate/r1初末目标完全相同但最终3/12的案例，scripts/analyze_workflow_efficiency.py新增goal_progress_review，输出初始/最终最佳等价绑定满足率、净变化和原始目标列表是否相同。缺少初始证据保留unknown；净满足率变化不等于完成子目标数（绑定可变化，得失可抵消）。原评分字段和独立评价器未改。tests/test_workflow_efficiency.py共6项通过，覆盖最佳绑定不取最后一行、初始已满足条件不计新增进展、缺失初始状态不猜测。原始candidate日志重新分析输出candidate_r1_progress_aware_efficiency.json，初末均0.25、净变化0；旧报告保留。属于benchmark分析设施增强，非策略或仿真器修复，无新增模型实验。首次编辑命令因系统无python未执行，已用behavior解释器完成；第一次5项通过仅是旧测试，不计新功能验证。


### 2026-10-25：GROC-013补充——两次open成功但所看静态车身面未变化

hand-state candidate/r2，杂货301/seed0、planning、gpt-6.1-sol low、bundle2c4ac95d、BDDL b38c56b6，4动作后blocked。两次open均工具成功，模型因所看红色面板未变化判断后备箱仍关着。人工检查三张连续右视图，画面确近乎不变；私有grounding两次均为汽车base_link视觉网格，而非trunk关节。当前证据不能把静态面板不变等同尾门关节未打开，也不能证明全部物品无法从其他视角发现。未确认仿真器开门缺陷，未更改执行器或评分。证据candidate_r2_early_stop_visual_review.json、early_stop_trunk_views.jpg及原事件；未重放关节轨迹，物理状态仍需另证。复用检查应分别记录模型选中的可见表面、实际操作关节与新视角证据；open工具成功只证明操作层反馈。


GROC-013开门路径补核：冻结执行器ControlledCarry._ideal_state_action为真实RGB路径，Open.set_value(wanted,fully=True)后同步诊断视觉，等待最多30步，再检查Open.get_value；上游fully=True操作所有metadata relevant joints。由此排除“当前RGB执行路径只随机开部分关节”的源码假设，但不能证明该场物理尾门轨迹。首次检索命中的high-level语义路径不用于本批结论。源码/哈希及检查范围保存在hand-state cohort/open_action_contract_review.json。四场终态各187文件重新核验全部匹配，详runtime_validation_all_cells_final.json。未改开门执行器，案例仍未确认设施故障。


### 2026-10-25：GROC-013执行器关节证据补全（诊断增强，未认定物理修复）

此前候选r2两次open成功但所看base_link不变，没有逐动作关节位置可供判断。现修改executors/carry.py，在Open操作前、setter后、settling后将metadata相关关节名称、位置、上下限及Open状态写入私有placement_diagnostics.jsonl；读取失败显式snapshot_error。保留原setter、步进、校验和返回字段，不向agent暴露关节或实例状态。后续普通任务运行即可获取证据，不新增skill实验。tests/test_open_joint_diagnostics.py与test_carry_particles.py共6检查通过；新测试只验证记录内容和缺失数据处理，不能证明真实关节物理行为。源码/测试快照与哈希见experiments/open_joint_diagnostics_20261025/review.json。复用任务设施应保留关节阶段证据，避免仅靠静态面板截图认定操作失效。

关节诊断补充验证：调用真实_ideal_state_action、替代外部模拟器依赖，检查fully=True、精确步进次数、三阶段快照、成功后验及失败事务回滚；相关测试现共7项通过。仅CPU控制流验证，不代表物理执行成功。源码与测试快照/哈希已更新到open_joint_diagnostics_20261025。首次追加测试命令因工作目录重复前缀未写入，随后已纠正；此前6项输出不是新增集成用例结果。


### 2026-10-25：普通执行器完整回归提前blocked，目标补丁未触发

executor_full_regression_20261025 / groceries_v2_executor_full_gpt61sol_low_20261025_r1，301/seed0，原BDDL/场景/预算，gpt-6.1-sol low，当前工程skill bundle ec59c02f；187运行快照文件及来源配置已核对。模型3次导航成功、1次open超距，4动作后因遮挡提前blocked，独立task=false、3/12且相对初始净变化0。未实际操作关节，未抓取/放置，因此既不能确认新关节诊断物理有效，也不能证明GROC-015改善。仅环境/指标分项计时已实测；首导航141步环境11.238秒、指标0.100秒，不支持削减评分更新提速。

录像完整解码及SHA256、原失败原因、评分和覆盖边界见terminal_review.json；final_efficiency.json保存动作成本。失败保留，不补抽，不将本轮作为skill对照。准备阶段复制曾跟随共享cache软链接，已停止该准备进程，保留部分目录，改为保留软链接后才首次启动仿真；无被替换的模型尝试。下一步应调查可发现性/恢复和提前终止依据，不通过改评分或自动选物掩盖失败。

GROC-013工程指导修补：visual-manipulation第7步明确局部遮挡后的可达视角恢复及整任务blocked证据标准。依据最新普通回归和两场4动作失败；不更改finish工具接受规则或评分，不强制耗尽预算。工程判断采用、行为未验证，未新增专项实验；skill_termination_repair_20261025保留快照与依据。案例自主恢复仍未解决。

2026-10-25造任务复用增量：针对尚无独立位置组合的覆盖缺口，杂货overlay生成器支持两袋六食品整体受限XY偏移，保证构建层相对位置不变并拒绝非有限/过大偏移。候选8厘米平移与原overlay三JSON结构对比仅八货物变化。5项CPU生成器测试通过，物理装载/可发现性未验；不是已修复仿真故障或有效留出。证据groceries_v2_cargo_offset_candidate_20261025/variant_review.json。


2026-10-25位置变体机制验收启动：run_readiness_probe.py新增cargo-offset-xy传递与manifest，补存backend/carry/placement源码，清除继承的OMNIGIBSON_GPU_ID强制渲染覆盖。冻结179文件、独立appdata（保留cache软链接）、GPU3互斥锁与内存门槛；启动证据groceries_v2_offset_mechanics_20261025/launch.json和status.json，supervisor2397519、launcher2397522实查存活。原seed0与任务保持，8厘米货物偏移；私有geometry helper和显式重定位，仅验证机制，不涉及模型、skill实验或自主发现/导航结论。结果尚待检查run/result.json，退出码不代替逐项验收。

位置变体探针终态：22/22检查通过、进程exit0；原始result.json和录像保存于groceries_v2_offset_mechanics_20261025。新增私有关节诊断取得真实冰箱/吊柜三阶段记录。验收审查发现probe未显式设置inside_placement，报告symbolic_raycast；不能将私有helper通过直接认定与official_volume自主协议等价。未更改冻结探针或回填配置，保留此差异，未宣布留出收录。


2026-10-25探针放置协议差异已定位：冻结probe逐件直接调用_checked_place_inside；该helper不读取inside_placement，真实RGB official_volume分支也调用此helper。评分记录195个volume sampling物理步，支持实际体积采样而非symbolic dispatch。差异为构造参数默认标签，不证明helper算法不同；仍不能外推RGB选点/reach/导航/托管deadline等价。placement_protocol_review.json保存源码哈希和范围。生产probe显式inside_placement=official_volume，并保存逐件helper返回值，避免未来报告歧义；不改已完成22/22回合或补写其原始元数据，不因标签单独重跑。

2026-10-25评分测试跳过已复核：之前4项因解释器PYTHONPATH未加入固定bddl3而skip；使用固定源码路径重跑grocery_task_goals与grocery_overlay_variants，9项及10个子测试全部通过，无跳过。覆盖两个等价吊柜/分柜、逐件牛奶饼干必需、非法目的地和关门反例，以及平移边界。不是评分逻辑修复或模型成绩；命令与范围见groceries_v2_cargo_offset_candidate_20261025/compiler_and_overlay_test_review.json。

2026-10-25 GROC-013位置迁移恢复观察：8厘米候选评估中先抓paper_bag并携物导航，持物open被hand_occupied拒绝；首线程文字结束未正式finish，按原--unfinished-restarts=3自动新线程继续，同sim PID2457999无reset。新线程重复一次open拒绝，随后自主release成功、open成功。door_hand_occupied_recovery_review.json保留公共调用/结果；此恢复只证明手占用约束解除后门可开，不证明释放安全或袋内食品保持，也不证明skill改动收益。运行继续，冻结输入不变；最终审计需标明fresh-thread续接，不当作单线程运行。


### 2026-10-25：8厘米位置迁移自主评估终态（失败保留）

固定179文件、skill b2da7969、gpt-6.1-sol low、原BDDL/预算；301/seed0位置偏移[.08,0]。11预算动作、2次hand_occupied失败、9.843米导航，正式blocked，初末最佳目标均3/12，净变化0。第一袋由真实RGB选点抓起；持物开门失败后首线程仅文字结束，原配置自动续接一次，新线程重复开门失败后release并open成功。门关节设置与沉降后均1.56997弧度、Open=true，仍未继续进入厨房；最终理由是食品/目的地不可见，不能视为不可恢复设施阻断的证明。没有完成逐件食品或第二袋发现，不能宣布自主迁移通过。

四路RGB/动作来源完整性检查通过，整体audit因任务失败及blocked而失败；187个终态源码/skill/控制器快照匹配，主录像完整解码exit0。terminal_review.json、final_efficiency.json、controller_continuation_review.json和door_hand_occupied_recovery_review.json均在experiments/groceries_v2_position_transfer_20261025。evaluation_split.json已标记评估完成但任务失败；冻结前split另保留原副本。没有补抽或运行中调参，单次失败不证明场景不可行，也不证明skill补丁无效/有效。


### REPRO-HOT-002：无长程对照配置复核与canonical跨线程漏记（2026-10-25）

用户要求复核原六场是否配置错或模型服务变差，并重跑。实际冻结执行器/导航/后端/独立评分器字节相同，六场src运行快照、九工具MCP schema9a411311、gpt-6.1-sol low/provider参数及298项依赖一致；配对任务/场景/机器人/预算一致。差异仅vision_policy长程读取段、run_demo_trial消融元数据和audit_demo_trial拒绝非法act容错。模型真实首轮工具返回各有四张512x512 RGB。上游历史dirty内容及完整继承环境、远程模型不可变build仍缺失，不能由本地一致推断服务始终相同。

另确认记录导出缺陷：clients/events.py对合并多个Codex线程的item ID全局去重，但新线程会复用ID。真实无长程seed0/11/22原始模型调用137/155/176，对应canonical仅85/88/86，漏52/67/90；完整skill单线程三场没有漏记。真实parser最小两线程同item_0夹具应导出2调用/2结果，实际1/1，red日志已保存。它影响canonical轨迹阅读，不改变原始model_events、仿真events或11目标评分；不能以此解释0/3，亦不可用canonical缺失判断模型没调用。分析已改用完整raw MCP完成事件。未修改生产/冻结parser，缺陷修复另行评估。

两组均有gpt-6.1-sol本地metadata缺失/fallback警告；无长程seed0首线程零工具且零token，是空turn异常，不是降级证明。保留所有原始失败。新复核单seed0两臂同GPU2串行，254/252源码原样新冻结，当前上游base+patch+22文件/hash保存；清除继承MAS/OG/OMNIGIBSON开关后重建两组共同环境，其他四skill、规划工具、公开decision、120动作/20000步不变。首个无长程回合已启动；结果未出，不宣称修复或新成功。

证据：experiments/hot_drink_long_horizon_recheck_20261025/{historical_configuration_review.json,historical_image_delivery_review.json,canonical_export_defect_review.json,canonical_restart_red.log,plan.json,status.json}。防复发：多线程/续跑日志的调用ID必须按执行session命名空间关联，分析应核对raw与派生调用数量；配置核验须用实际controller/MCP记录，不只看plan；服务质量和本地代码等价分开判断。


### GROC-016：新线程续接未携带已公开的动作反馈（2026-10-25，协议修补，行为待验）

杂货v2位置偏移[.08,0]，301/seed0、gpt-6.1-sol low、bundle b2da7969、BDDL b38c56b6。首线程paper_bag抓取成功、open因hand_occupied拒绝后仅文字结束，无finish；配置允许自动新线程续接。冻结run_codex_controller.py只重发任务与initialize提示，未转交此前公开动作反馈；新线程随后重复open。未证明上下文缺失是重复尝试的唯一原因，模型也可从新RGB推断，但已确认harness的信息连续性缺口。

修改scripts/run_codex_controller.py及新增clients/recovery.py：续接附上最多240条已完成act/look的公开primitive、ok、effect类别及错误代码；不携带旧图、私有状态、评估结果、目标实例或操作建议，未知反馈不当成功，截断显式记录。每次续接保存public_action_history.json及metadata哈希；这是单独版本化的续接协议变化，旧成绩不重算。原始首线程7条动作离线提取保留paper_bag与hand_occupied。20测试及32子测试通过，测试与源码快照在controller_public_history_repair_20261025；尚无新模型行为收益证据。

造任务复用：协议需声明续接是否保留公开动作历史，包含已公开的抓取/释放/错误，而非让新线程只凭局部图重建全部状态。历史反馈不是当前状态或独立评分。因该修补利用8厘米评估结果，evaluation_split已将此配置转开发，原评估失败与冻结split保留；下次独立评估须另立配置。


2026-10-25 GROC-012导航成本下界补核：原失败退出路径长8.3958米，按0.5m/s、30Hz不变，仅平移至少504命令，实际advance508；turn182，理想反馈全程693仍超过690运动预算。path_cost_lower_bound_review.json保留计算及范围，不能视为全局最短物理路径证明。该样本不支持仅凭最后转向超时就提高速度/预算或放宽1e-3rad容差，也未证明182次转向均为冗余。保留原action_timeout；已有当前图新增导航恢复是另一次动作，不抹去超时。未改执行器。造任务时记录动作内路径成本和跨调用恢复，区分预算限制、执行抖动与选点规划问题。

GROC-016普通开发回归已冻结排队：experiments/groc016_harness_regression_20261025，181文件（包含新增recovery模块和测试）、skill b2da7969不变，原8厘米开发场景与预算、gpt-6.1-sol low，单次无补抽。supervisor2668638实查存活，当前GPU3 free16797MiB低于20000门槛，状态waiting_for_gpu_memory，未启动模型；后台30秒复查，满足后再核对源清单并启动。无降低显存门槛或重复回合。续接若未触发需标覆盖不足，不当作GROC-016行为已验；这不是skill对照或独立留出。


REPRO-HOT-002修复补充：当前维护工作区clients/events.py已为后续Codex CLI stream的item ID加入独立命名空间；同thread resume也按新CLI stream区分，首stream原ID保持，开始/完成事件仍只配对一次。新增tests/test_client_event_sessions.py覆盖新线程重号、同线程resume重号和首段缺header三个真实模式，修前3失败；修后连同原client测试20通过。原六场raw日志重解析分别完整保留130/137/132/155/118/176个调用且call_id全局唯一；没有改写旧canonical、raw、评分或当前两臂冻结源码。红绿日志、修复源码/测试SHA和逐场复核存hot_drink_long_horizon_recheck_20261025/analysis_revision/validation.json。此修复改善日志完整性，不是已证明的机器人能力改进。

历史boil诊断补充：无长程seed0与11的32+15次通用execution_error全为boil。按每次调用前保存的高层状态，以真实HighLevelHotDrink.apply重放47次均invalid_precondition，水壶语义water字段均无水；其中部分也未在灶上。公共boundary未保留invalid_precondition，统一归并execution_error。未发现这些调用中有执行器崩溃证据，不能把execution_error计数直接当设施崩溃数；通用反馈恢复成本的设施问题应另立版本评估，不在本次重跑中改变。证据historical_boil_precondition_review.json及historical_executor_error_review.json。

### GROC-016：补充离线适配器输入证据（2026-10-25）

从原失败回合 `groceries_v2_position_transfer_gpt61sol_low_20261025_r1/controller/model_events.jsonl` 截取第二个 thread.started 之前的事件，提取7条公开反馈，并使用真实 CodexAdapter.prepare_project 生成输入；断言 paper_bag 抓取、hand_occupied 拒绝以及完整历史JSON均进入 PreparedProject.stdin。证据、原事件哈希及输入原文保留于 `../experiments/controller_public_history_delivery_20261025/review.json`（相对工作区仓库根的兄弟 experiments 目录）。未启动客户端、模型或仿真器；该检查仅证明真实适配器准备后的输入传递，不证明模型读取、恢复行为改善或任务成功。排队回归的冻结源码保持不变。

### GROC-016关联诊断：终止声明与剩余预算分开展示（2026-10-25）

离线分析器新增termination_review，逐一保留finish请求的声明、拒绝/关闭结果及公开剩余时间，另外按明确启用的实际budget和终态计数报告剩余动作/环境步；缺失或停用限制保持未知，不从blocked声明推断不可恢复。8项CPU测试通过，原8厘米失败回合重分析确认终态剩余109动作、18941环境步，finish响应剩余14101.4秒且deadline_expired_at_finish=false。完整新报告、源码/测试快照及哈希见工作区 `experiments/termination_analysis_20261025/`。只支持未耗尽这些限制的结论，不证明任务必然可行或全部剩余动作冗余；未改原评分、原报告、运行中冻结快照或模型策略。

### GROC-017：私有readiness探针保存源码却从工作区导入（2026-10-25）

- 任务与范围：`carry_in_and_put_away_groceries/v2_design/run_readiness_probe.py`；通用私有机制探针入口的harness可复现性缺口，不是新模型回合失败，适用于该入口的所有scene/instance/seed。目标定义和评分未变。
- 复现：启动器保存平铺源码文件，但子进程PYTHONPATH仍包含工作区`src`。真实main入口的CPU边界测试先失败（1 failed），证明源码记录不能锁定实际导入路径；后续工作区编辑可能改变执行代码，是否影响某个旧回合须逐个检查，不据此认定旧结果污染。
- 修复层级：harness。完整复制src包（排除字节码缓存）、对实际副本计算哈希，子进程改从副本导入，并记录executed_package_root。已有排队/运行快照不改。
- 验证：`tests/test_readiness_probe_snapshot.py`与`tests/test_grocery_overlay_variants.py`共6 passed；测试经过真实启动器，并另开真实CPU Python子进程确认carry.__file__位于冻结目录。模拟器启动被替代，未作GPU物理或自主成功声明。
- 证据：工作区`experiments/groc017_probe_source_freeze_20261025/review.json`及源码/测试快照。上游OG、BDDL和资产仍依赖外部路径，不是完全封闭环境。
- 后续造任务复用：不仅保存副本，还检查实际子进程导入路径与所记录哈希一致；禁止仅因目录名为source_snapshot就声称执行已冻结。旧22/22探针等历史结果按各自实际执行树/哈希审查，不改分数或补写假设。

GROC-017历史范围核对：已完成的8厘米22/22私有探针由外层冻结execution树启动，其旧source/src实际配置路径落在该冻结树而非活动工作区。179个外层文件哈希及11个探针源码记录全部一致。证据见 `experiments/groc017_probe_source_freeze_20261025/historical_offset_probe_review.json`（工作区）。这是配置导入链重建，没有补造历史module.__file__观测；保留原机制结果与限制，不因通用入口修复重跑或改分。

GROC-016并行调度补充（2026-10-25）：用户明确要求在其他空闲GPU新增一轮，同时保留原GPU3队列。新增 `experiments/groc016_parallel_regression_20261025/`，监督PID2980739等待GPU0/1/2中任一空闲显存≥20000MiB的卡，仅分配一个独立r2回合；原PID2668638不变。181个冻结源码文件全部匹配，两回合使用相同scene/instance301/seed0/model6.1sol-low/skill/budget，仅GPU、appdata、输出路径独立。新增回合是在结果产生前安排的普通重复回合，不替换失败、不作为skill专项比较或留出结果。当前两队列均未启动模型；以各监督PID与状态文件为准。

### GROC-018：同模型近期杂货回合异常提前结束，根因未定（2026-10-25）

任务：carry_in_and_put_away_groceries/v2_design，instance301、seed0、货物偏移[0.08,0]，冻结GROC016执行树、model gpt-6.1-sol low。r1终态22动作、r2终态7动作，均blocked、原生3/12，未耗尽预算。不得仅因finish来自模型便认定根因是规划能力。

对照历史同模型with_skills成功回合83动作：初始developer提示、任务指令、工具schema哈希一致，Codex CLI均0.157.1，冻结客户端适配器源码diff为空；抽查初始工具响应均实际传递4张JPEG。近期首线程均5条motor工具记录后直接文本结束且无finish，其中r1含两条参数拒绝、实际仅3个预算动作，r2为5个预算动作；此现象发生在fresh restart之前，所以GROC016公开历史传递修补不能解释首次提前结束。重启后的finish与未完成任务状态为症状，尚未定位服务端终止/路由、其他skill/场景/设施差异或模型波动的因果。

证据：工作区experiments/early_stop_differential_diagnosis_20261025/comparison.json，原始两轮日志与视频不改。当前分类：未决，优先核对模型服务与实际上下文；不据此调松评分、不屏蔽finish、不声称模型必然做不了。修复层级未定、尚无对应修复或因果验证。

GROC-018新增确认与修补（2026-10-25）：原生会话核对显示历史成功回合与异常回合的内置基础指令、全部4个developer块哈希完全一致；首线程末次输入23944 tokens，窗口258400，没有原生限流/警告/中止证据。公开预算仍为118等正值。历史两轮视频完整解码通过，续接历史文件哈希均匹配；独立审计仅任务成功和模型achieved两项为false，不能称为传输审计失败。

本地已复现的harness缺口：普通对话turn在无finish时结束，控制器使用prepare_project新建线程，丢弃完整图像/计划/对话；已有有限动作摘要不能等价保留上下文。新增真实controller-main边界测试旧版1 failed（创建项目2次、未恢复原线程）。修改scripts/run_codex_controller.py与run_demo_trial.py，默认同线程resume，沿用原unfinished恢复次数，正式finish后不续跑；--unfinished-recovery=fresh_thread可复现旧协议，服务端畸形历史仍独立处理。21项/32子测试通过；扩充后5项专项检查涵盖正式关闭、次数上限、旧模式及真实HTTP字节保真。

首次为何提前输出文本仍待服务端证据，不把上述本地续接缺口称为首次提前停止的唯一根因。仅记录元数据的透明转发器已加入未修改行为版本完整诊断回合，GPU0监督4151770；修复版独立冻结并排在其后，监督25770。新版本仅两处controller/launcher源文件变化，skill/场景/评分/执行器/预算不变；没有增加恢复次数，也不拒绝模型已正式提交的blocked。证据：工作区experiments/early_stop_differential_diagnosis_20261025/continuity_repair_review.json、groc018_transport_diagnosis_20261025及groc018_same_thread_regression_20261025。完整自主结果未产生，尚不能宣称修复效果。

GROC-018完整传输诊断结果：28次请求均HTTP200且response.completed，无不完整响应、断连或超时；最大单次输入49481 tokens。该轮只有一个线程，13预算动作后正式blocked，原生3/12、与初始相同，未复现首次无finish文本结束。56张实际交付JPEG与记录图片逐一哈希相同；运行/控制器共178份src快照匹配冻结文件，录像全片解码通过。因此只能排除本轮观察到的传输截断，不能据此排除历史上游问题或宣布正常长程回合完成。证据：early_stop_differential_diagnosis_20261025/groc018_transport_terminal_review.json。

同线程完整回归已实际复现首次无finish结束：首turn多次read_skill/look/finish仅传decision、遗漏工具必填字段；恢复原thread_id后look和后续导航/抓袋/暂放已成功，场景未重置。初始turn为何遗漏参数仍未定，正在独立回合核对上游schema与返回参数键，不能把本地续接修复当作参数问题已解决。原监督25770只在显存等待且无子进程时终止；可用显存从20GiB门槛以下调整为16GiB准入后，监督93657运行原冻结回合，资源修订另存，不增加或替换模型尝试。

GROC-018上游边界新证据：独立GROC019回合至少两次read_skill在请求端schema已含name/resource/decision时，上游response.completed仅给decision；上游参数规范化SHA256与本地MCP调用逐一相同。由此确认这些缺失发生在上游返回边界之前，排除本地CLI/bridge转交时删参；尚不能区分模型自身输出与供应商内部转换。证据groc018_upstream_argument_checkpoint.json。另做4格最小离线格式检查：flat两次参数完整；namespace两次分别上游限流/暂不可用，不能据此认定命名空间兼容性缺陷，也不采用格式改写。该检查不含图像/完整上下文，不计自主任务或skill收益，全部结果保留groc018_tool_format_probe_20261025。

诊断观察器限制更正：原版本仅转发4种请求头和3种响应头，虽然请求/响应实体字节保持，不能称为完整HTTP透明；User-Agent及会话/turn元数据可能丢失。真实双HTTP服务测试在缺失X-Codex-Turn-State处红1，修为转发端到端头、过滤hop-by-hop后绿1，保留字节与敏感值不落日志检查。已有4个冻结观察器不改，并各加observer_transport_caveat.json；这些回合不能证明与直连供应商路由/会话行为等价。GROC016首次异常发生在观察器加入之前，不能将其归因于该诊断缺陷。证据early_stop_differential_diagnosis_20261025/observer_header_fidelity_review.json，候选脚本与测试另存。

历史应用/skill/零偏移配置复现终态：groc018_historical_config_reproduction_20261025为11动作、3次执行失败、1531模拟步，正式blocked，剩109动作；原生初始和最终均3/12。模型抓袋后尝试开门，收到占手反馈；一次暂放请求引用过期图片、下一次点选地面超距后主动结束，并明确承认尚未证明无恢复办法。不能将该声明当作不可恢复设施故障。24次上游响应均HTTP200且completed，调用参数顺序/哈希与本地相同，23次机器人调用均无必填参数遗漏；因此上游漏参也不能解释全部短回合。154冻结文件、176运行src副本及56张交付JPEG核对通过，两份录像全片解码通过。该轮并非近期skill/应用代码才会出现的现象，但观察器请求头限制和未留存的历史上游dirty源码仍妨碍完整环境归因；不宣称供应商降级或skill回退是已确认原因。完整失败保留于该目录terminal_review.json，分析位于early_stop_differential_diagnosis_20261025/groc018_historical_efficiency.json。

同线程回合进行中的逐点核对：第44动作宽台面暂放成功；46动作点在台面下柜体侧面，49动作点靠水槽窄边，两次采样失败暂不构成放置器缺陷。45与51动作想取食品却报告paper_bag，实际交付RGB选点分别位于袋身及开口后侧袋壁，视觉射线亦命中paper_bag_82；后者的黄色物品虽部分可见，所选点未落在该物品上。这两次不是已确认的食品选点被错误解析，不据此增强抓取器或替模型修正像素。证据early_stop_differential_diagnosis_20261025/groc018_counter_staging_review.json、groc018_counter_staging_pixels.png、groc018_content_grasp_pixels.json及同名png。以后验收小件/容器任务，应核对所选像素、首个可见表面及实际抓取类别，区分遮挡、选错物体与执行失败；私有命中数据只用于作者诊断。

同线程回归终态：54动作、11次执行失败，模型正式blocked，仍余66动作。第53动作已经成功取出butter_cookie，最后持有该物品，未完成所需收纳。原生初始及最终均3/12，但原子目标数组不同，分数抵消不能说状态毫无变化。第一次无finish文本结束后恢复的thread_id与返回ID均为01a17273-9a49-72e0-90a5-68f6022f41f3，无新线程/场景重置；85次上游响应均HTTP200且completed、无观察到的断连，304张交付JPEG及178份运行src匹配，双录像完整解码通过，审计仅整任务/achieved两项失败。本轮冻结观察器尚无schema/参数元数据，不能作上游参数哈希对照；terminal_review初版误把缺测当不匹配/空漏参列表，已保留并以terminal_review_v2.json和terminal_review_correction.json更正。本地实际有18次invalid_arguments拒绝。证据groc018_same_thread_regression_20261025/terminal_review_v2.json及early_stop_differential_diagnosis_20261025/groc018_same_thread_efficiency.json。

为排除已确认的观察器请求头干扰，预登记一轮groc018_direct_provider_regression_20261025：与上述回合181份冻结应用/skill/任务、8厘米场景及预算相同，只去掉本地诊断HTTP转发器；GPU0显存准入20GiB、独立appdata。无成功条件的自动重试队列，正式blocked仍接受；不是skill对照。最终按原生评分、原生客户端/MCP对齐、RGB/快照和录像检查记录，不声称获得不存在的上游观察器数据。

直连终态：4动作、1次导航不可达后又成功导航，模型正式blocked；初末目标数组相同、3/12，剩116动作。实际客户端命令base_url为https://api.gpt.ge/v1，本地诊断代理关闭，仍有8次仅decision的invalid_arguments（7次read_skill、1次act）。因此观察器不是漏参/短回合的唯一原因；本轮没有上游参数截获，不能将本地8次遗漏伪称为已截获的上游调用。181冻结文件、178运行副本、52张交付JPEG与双录像检查通过；审计除目标动作执行、任务成功及模型achieved三项外通过。证据groc018_direct_provider_regression_20261025/terminal_review.json。关于“其他实验正常跑满”的新确认，见GROC-021，不能再忽略结束监督的差异。

用户补充（2026-10-25）：对于近期api.gpt.ge的gpt-6.1-sol路由或账号配置是否调整，用户回复“没有已知改动”。这是用户提供的运维背景，不是供应商部署/路由审计；不能据此排除供应商内部变化，也不能认定服务降级。上游漏参的内部原因仍未定位，既有已确认结论不变。补充证据单独保存为early_stop_differential_diagnosis_20261025/provider_context_reply_20261025.json，不改动已完成报告及其哈希。原提问发送时回合尚在进行，收到本次回复时七轮计划实验均已终态。

### GROC-019：杂货任务错误“开门”像素被报成超距或占手，误导恢复方向（2026-10-25）

任务：carry_in_and_put_away_groceries/v2_design，301/seed0、货物偏移[0.08,0]，BDDL b38c56b6，gpt-6.1-sol low、planning、skill b2da7969，GROC016冻结执行器。失败回合groceries_v2_groc018_transport_gpt61sol_low_20261025_r1以“两个接近点均无法开门”blocked，13动作、原生3/12且无净目标变化。

复现证据：event-00050 open点击rgb-00009-front/[.9,.2]命中墙面，却先返回hand_occupied；event-00056的rgb-00011-back/[.42,.48]命中地面，event-00062的rgb-00013-left/[.67,.39]命中墙面，却返回out_of_reach。原始视觉射线记录、交付图片与选择点对照保存于early_stop_differential_diagnosis_20261025/groc018_wrong_door_pixels.png，图片交付哈希已核对。模型选错像素是能力错误；执行器先检查占手/距离，后检查Open能力，导致反馈不能区分错误对象与正确对象超距，是单独确认的设施缺口。未发现本轮导航或门关节无法通过的证据，不能声称“门本身坏了”。

修复层级：自有RGB执行器src/manipulation_agent/executors/omnigibson_rgb.py，在非高层语义分支先检查选中对象是否支持Open/ToggledOn；不支持时返回已有invalid_visual_target与重选提示，再处理占手和距离。沿用原1.4米距离门槛、物理状态设置和评分，不自动替换像素、不暴露实例或位置、不修改上游仿真器。已有合法目标的占手和超距限制继续有效。

验证：PYTHONPATH=src运行tests/test_visual_state_target.py，旧实现5失败/5通过，明确复现错误的out_of_reach/hand_occupied；修复后联合visual_manipulation_navigation、open_joint_diagnostics、high_level_visual_protocol共28通过、4子测试通过。更新旧距离用例的目标以真实支持Open，并修正其sys.modules恢复后补丁打在旧模块引用上的夹具问题。首次未指定PYTHONPATH的检查误导入相邻editable安装包，且缺output夹具；该次不作为缺陷红灯或修复证据，正确路径重跑才产生上述红绿结果。

完整自主回归：groc019_target_feedback_regression_20261025已独立冻结，相比同线程候选仅改一份RGB执行器；skill、任务、场景、评分、预算不变。透明观察器另记工具schema结构、上游参数键与摘要哈希，保留原始转发字节，不记参数值。自主收益和正常长程完成尚待终态，不因CPU通过便宣称修好整个任务。

GROC-019自主终态补充：6动作、1次误抓固定物体后恢复抓袋成功，两次门口导航均操作成功；模型以门关闭为由blocked，未尝试任何open或腾手操作，仍余114动作。原生3/12与初始相同，故新能力拒绝分支未覆盖，自主收益未验证。16次上游响应完整、调用参数顺序及SHA256均与本地一致，包含两次上游read_skill漏参；28张交付图片与原文件一致，181冻结文件及178运行src副本匹配，两份连续录像全片解码通过。任务/模型完成两项失败保留，其他独立审计项通过。证据groc019_target_feedback_regression_20261025/terminal_review.json；不因该轮短而增预算、禁止blocked或重抽成功。

GROC-018下一项诊断使用历史83动作/12项全成功回合的154文件冻结应用与skill、原零偏移场景，只加入字节保真的观察器，独立回合groc018_historical_config_reproduction_20261025。这是原配置完整复现检查，不是skill收益实验；当前上游dirty源码无法证明与历史未留存dirty内容完全相同，边界写入plan。与GPU0同线程修复回合并行，保留各自结果，不能把重复中的好结果挑出覆盖失败。

后续造任务防复发：针对错误背景像素、合法目标超距、合法目标占手分别检查错误码；能力不匹配应引导重选目标，距离不足应引导接近。审阅“门/容器无法操作”失败时，把模型声称目标与该像素实际视觉命中分开记录，再决定修导航、场景或状态执行器。

### GROC-020：无效动作缺少primitive，阻断失败实验审阅发布（2026-10-25）

任务：carry_in_and_put_away_groceries/v2_design，301/seed0；根因调查汇总包含GROC016及GROC018/019各完整失败。模型有仅含decision的act，被执行前参数校验拒绝，审计忠实保留model_act_sequence中的null。scripts/publish_demo_trials.py直接join该列表，真实发布TypeError，不能生成入口；这属于benchmark审阅工具缺陷，与仿真物理、评分及模型动作执行无关。

修改同一发布器：将null展示为“缺少 primitive”，其他记录按原顺序保留；不删错误调用，不改审计JSON、不改通过状态。tests/test_publish_demo_trials.py走真实main，旧版1失败、修复后1通过，同时核对发布副本及源审计保持相同。原六轮重发成功，index、五视角HTML、连续视频及审计JSON均HTTP200。入口：http://10.130.140.115:8769/groc018_root_cause_review_20261025_index.html 。证据与源码快照存groc020_publication_invalid_action_repair_20261025。以后造任务/工具验收应覆盖缺参拒绝后的记录、审计与发布链，失败证据必须同样可交付。

### GROC-021：不同结束监督导致“其他实验不会提前停”的错误比较（2026-10-25）

对照对象：杂货v2原生BDDL、301/seed0，与hot_drink_long_horizon_recheck_20261025/without_long_horizon_s0热饮高层视觉回合，均gpt-6.1-sol low、120动作上限，但任务/skill/评分和结束协议不同，不是受控模型或skill对照。

实际日志复现：热饮第24动作后已首次finish(blocked)，被premature_finish拒绝；第34、54、116、118、119动作后又被拒绝，最终才在120动作后关闭。源码VisionHarness._premature_blocked_finish以backend.high_level_harness为条件，剩余动作/调用/模拟步预算且无不可恢复错误时拦截blocked；高层achieved还会收到私有完成校验的二元反馈。杂货native不走此高层guard，同线程回合第54动作及直连回合第4动作的正式blocked都被直接接受。故“热饮未提前停”有明确的监督机制原因；不能把120步等同模型主动持续，也不能把这些差异归到GPU、skill或仿真器故障。

修复层级为实验工作规范与benchmark契约：AGENTS.md、long_horizon_task_workflow.md、套件与候选卡补充结束监督；不追溯改评分，不把拒绝blocked凑满动作称为已修好原生任务。以后造task必须同时固定动作/评分和结束规则；分析时报告最早结束意图、所有拒绝、续接方式及拒绝后的有效进展。证据early_stop_differential_diagnosis_20261025/termination_policy_review.json，包含各源快照哈希与原始finish事件。上游为何生成不完整参数仍是独立未决问题，本案不声称解释其全部原因。

### GROC-022：工具目录有必填字段，但实际请求关闭严格模式（2026-10-25）

任务与症状：carry_in_and_put_away_groceries/v2_design，301/seed0、货物偏移[0.08,0]，BDDL b38c56b6、planning、gpt-6.1-sol low、skill b2da7969。GROC-018直连回合read_skill仅传decision，漏name/resource；此前已排除本地CLI/bridge删参，但只核对required，未检查生成端是否启用严格模式。本案补充定位这一可控协议缺口，不能解释全部早停或认定供应商退化。

复现：`experiments/groc018_argument_deep_diagnosis_20261025/replay_missing_arguments.py`将真实失败调用交给当时冻结校验器，退出1、明确缺name/resource。用纯本地HTTP与真实MCP目录截获Codex初次请求，确认机器人函数均strict:false；read_skill的required仍正确。随后向同一供应商重放失败前8个公开消息/工具项及4张原始RGB，模型/low、schema及其余上下文固定。原格式4次有3次漏参（8个read_skill调用中3个）；flat两次、补参数提示两次也都漏参。仅改read_skill.strict为true的4次均完整，逐请求比对确认只有该标志变化；全机器人严格模式再做两次也完整。共20个接口探针，全部结果保留，不是自主任务或skill收益实验。后段前缀的2+2次未复现原act漏参，严格组只选look/finish，不宣称已单独验证act修复。

修复层级：自有Responses传输层`scripts/trace_responses_transport.py`增加显式`--strict-mcp-tools`，协议名strict_mcp_tools_v1；默认原样转发保持。只改机器人函数schema的strict与required声明，保留字段类型、约束、任务/历史/图像、原生工具和上游响应字节；本地参数校验继续生效。不填模型遗漏的动作/像素、不改执行器、仿真器、评分、skill或结束监督。日志同时记原始/转发请求哈希、schema与strict，避免把转换后的请求称为字节透明。`tests/test_transport_strict_tools.py`走真实双HTTP服务，旧版1失败/1通过；修复后的转换、默认保真、头转发、敏感值不落日志及开放schema拒绝共5项检查通过。源码/测试快照及红绿输出在上述诊断目录。

供应商语义边界：额外两次故意缺additionalProperties的strict负例没有呈现官方预期的明确schema拒绝：一次HTTP200但无可识别的标准终态事件（出现type=*****），一次HTTP200/completed且返回函数缺namespace。不能据官方strict文档保证这个第三方路由绝不漏参，也不能把少量通过外推为稳定成功。原生session_meta的基础指令还包含捕获到的实际wire指令已移除的内置Planning章节；重放使用已捕获的wire指令及原公开消息，不宣称精确重建历史HTTP会话或供应商内部状态。用户已回复无已知配置改动，远端内部原因仍无直接证据。

完整自主回归：`groc022_strict_mcp_regression_20261025`固定同一181份应用/skill/任务文件，在GPU2独立appdata上只加入上述协议，120动作/20000模拟步/14400秒及原最多3次同线程续接保持，正式blocked仍接受。11次上游响应全部completed，10次模型工具调用无漏参、无工具错误，与本地按顺序哈希一致；实际转发的9个机器人函数均strict:true。机器人look、navigate_to、open三动作后主动blocked，原生初末均3/12、目标数组相同，剩117动作；没有取得完整成功。16张实际交付JPEG及178份运行src副本匹配，双连续录像完整解码通过。证据`groc022_strict_mcp_regression_20261025/terminal_review.json`及上述诊断目录`review.json`。

画面核对：模型所选导航点在车后侧车身，随后开后备箱点中已打开的舱盖；四个记录视角仍未露出袋内物品。仅看不到物品不能证明环境无可行恢复，本轮没有尝试第二个导航落点或出现任何动作拒绝；不能以此认定导航/抓取设施已失效。RGB序列保存在`strict_trial_rgb_sequence.jpg`。本案支持“生成约束能缓解已复现漏参”，同时证明“没有漏参也会早停”；首次自然文本结束和供应商内部成因仍独立未决。

以后造task：把实际模型请求schema、strict、本地校验和无效调用的可恢复反馈纳入工具准入；按真实公开失败前缀复现，包含图像，不只做单句协议探针。协议修补与skill/场景版本分开，完整任务、参数完整性与结束监督分项报告，保留失败分母。

审阅已发布：http://10.130.140.115:8769/groc022_strict_mcp_20261025_index.html 。包含历史直连失败和本次严格协议失败，附20次接口探针报告及实际四图选点；入口、报告、核对JSON、图片、两轮审阅/视频/审计共10个链接均HTTP200。证据`groc018_argument_deep_diagnosis_20261025/publication_review.json`；本轮已终态，无成功条件重试队列。

### REPRO-HOT-002：2026-10-25 无长程复跑完成、对照待资源

同seed0无长程回合完成：其他四skill保留且实际读取，120动作、47失败、7404模拟步、6/11目标，正式finish，无线程重启。137原始MCP调用与137导出调用对齐；源码/schema/模型参数/依赖/场景/独立状态重放及逐调用decision检查通过，任务成功与模型达成两项失败保留。两份7525帧录像完整解码通过。已保存第98/101次公开意图与柜门/地面选点不符的RGB审计图，不能用这些局部错误证明远端模型降级。

有长程新回合尚未启动：共享GPU均低于预设20000MiB可用门槛，原GPU2队列继续等待；考虑GPU0后启动前复查也不足，未应用换卡偏差。历史3/3对0/3与本轮结果分别保留，不合并或覆盖。完整报告和证据：`/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_long_horizon_recheck_20261025/REPORT.md`、`partial_evidence_review.json`、`comparison.json`、`resource_wait_review.json`。本轮未修改skill/执行器/任务/评分器，唯一维护代码修改是独立日志导出修复（20测试通过），冻结运行时未改。


### GROC-023：杂货后备箱局部遮挡后过早结束（2026-10-25）

任务：carry_in_and_put_away_groceries/v2_design，原生BDDL b38c56b6，301/seed0，货物偏移[0.08,0]，gpt-6.1-sol low，strict_mcp_tools_v1。基线为groceries_v2_groc022_strict_mcp_gpt61sol_low_20261025_r1：3动作、无工具错误、3/12，模型只尝试一次导航到车后侧车身、打开已抬起的舱盖，随后因当前四图看不到内容物而blocked。实际图像仍有可见地面，但未尝试第二个平移观察位置。

复核：导航终点距所选车身表面0.80772米，低于1.35米门限，端点可见性开启且未放宽。记录没有证明执行器选错终点，也没有证明可达内部视角不存在。已确认的行为缺口是把一个局部遮挡视角直接当作整任务阻断；尚未确认场景/相机是否还存在额外限制。因而不凭此改导航器、抓取器、仿真器或结束拦截。

修复层级：skill工程判断修补。visual-exploration增加凹入容器的外表面/开口区别，借可见空地平移以消除遮挡；visual-manipulation第6步在遮挡时触发该指导。模型仍自主选择路线、动作及像素，没有坐标、对象ID、固定通关路径或最低动作数。两份quick_validate通过，只证明文档格式。

普通任务运行：experiments/groc023_occlusion_recovery_20261025，冻结原181文件，仅上述两份skill变化；同GPU2、BDDL/overlay、120动作/20000模拟步/14400秒、same_thread最多3次续接、正式blocked接受及私有评分不反馈均保持。不安排skill专项对照，不因失败自动追加。启动时尚无结果，不声明已修复或成功。原失败不覆盖。

以后造task：针对容器内容发现，单独检查允许RGB下的开口视角与可见空地换位，不把“到达容器外表面”当成“已看清内部”；记录第一次blocked前实际换位与执行错误，区分代理推断和设施不可达。证据baseline_review.json、plan.json、source_manifest.json及完整新回合将随运行保留。

### SCENE-HOT-001：热饮场景补回地板后仍被房间过滤（2026-10-25）

任务prepare_and_serve_hot_drink，house_single_floor/301，历史seed0/11/22两组及新seed0无长程；高层热饮协议high_level_hot_drink_state_v1，BDDL哈希4be5469c5eff25f59504c90c4d78d04312057353ae35a0bf007012c2d0a9b915。用户报告地板/墙面像透视、部分几何空缺。确认场景JSON补回固定地板后，InteractiveTraversableScene._should_load_object仍对GROUND_CATEGORIES执行三房间过滤；22块地板只可加载4块，排除18块，147块墙和17块顶则通过结构例外全部保留。旧restore_static_floor_geometry的JSON修复未闭环到运行时加载，不能称几何问题已完整修复。七场环境配置及日志中同一组4地板USD路径佐证。

用实际加载器AST构建无GPU复现：check_floor_admission.py失败（4/22），--all-rooms离线反事实通过（22/22），不构成安全修复或自主任务回归。父类假定放行是上界；warning不是完整运行对象清单。具体墙面透视是否还包含单面材质/相机问题未定位。本次只诊断与存证，未改冻结场景/skill/执行器/上游；无空闲GPU，未做新渲染。修复层应为场景加载/建筑外壳保留策略，另版本检查可视完整性、碰撞、导航及任务，保留历史失败与分数。预防：不能仅验JSON结构数，必须验实际加载对象及跨房间相机视域。证据目录：/mnt/data/home/sunyujie/robot_manipulation/experiments/hot_drink_scene_visibility_review_20261025。


GROC-023终态补充：10动作后正式blocked，已成功抓取一只paper_bag；在第4动作后的rgb-00005-right可见两只袋子，第10动作grasp成功，证明允许RGB下可发现并取出载具。仍3/12，未完成搬运/收纳；不宣称整体早停修好。结束decision明确task remains feasible，reason却是Unable to complete full transfer within this session；当时14239.4秒、110动作剩余，无客户端超时。另立GROC-024修复虚假会话限制的接受缺口。

### GROC-024：原生杂货回合错误接受虚假会话时限（2026-10-25）

真实复现来自GROC-023上述finish，模型刚成功抓袋、仍有14239.4秒/110动作，却声称本会话无法完成。已有_false_time_exhaustion_claim仅demo_motion生效，native关闭；request_finish提前设置停止标志，因此后续不能纠正。不是已触发超时、仿真器崩溃或抓取失败；模型为何作出该声明仍未知。

修复层级：harness结束协议，不是执行器/仿真器。vision_harness.py新增显式MAS_REJECT_FALSE_TIME_CLAIMS=1，clock_claim_correction_v1；仅blocked的显式时间/本会话耗尽声明，在动作/调用/模拟步预算均剩余且墙钟>600秒时纠正。HTTP预关闭与owner-thread finish使用同一判定，拒绝时不取消episode；反馈仅公开剩余时间/动作，无私有目标。真实技术阻断、预算耗尽、近截止仍能结束，默认关闭保持旧native规则。run.json记录具体监督契约。非通用拒绝blocked，不强制满预算。新成绩不得与无纠正旧回合混作相同协议，也不能据延长步数声称自主坚持提高。

回归：tests/test_finish_clock_claims.py使用真实原始reason经request_finish→call→后续act链路，修复前1失败/6通过，修复后连同test_finish_response.py为12通过、2子测试通过。覆盖关闭开关、真实技术阻断、动作/调用/模拟步耗尽、近截止及旧demo/高层结束行为。环境有既存requests依赖版本警告。原始日志clock_claim_red.log/clock_claim_final.log保存在experiments/groc023_occlusion_recovery_20261025。

一次普通设施协议开发运行已冻结于experiments/groc024_clock_claim_correction_20261025：在GROC-023同181文件基础只改vision_harness.py，显式启用开关；同两份skill、strict wire、任务/overlay/模型low/预算/原生评分。启动时行为与任务结果未验证。失败保留、不按成功追加重试。

以后造task：结束工具不能先取消再判定可恢复错误；资源状态与任务状态分离，纠正虚假资源断言无需读取评分。版本化结束监督并记录每次首次结束意图、纠正、续行动作及独立成果。词组识别是有限启发式，不提供自然语言阻断真实性的全面保证。


GROC-024终态补充：9动作、999模拟步、3/12失败。成功发现并抓起paper_bag、到达室内门前；持袋开门hand_occupied，随后仅一个place_on_top地面点out_of_reach后正式blocked。19次HTTP均200/completed、无漏参、上下游调用哈希一致；40张JPEG与178份runtime源码核对，双录像完整解码通过。run.json明确clock_claim_correction_v1、无通用early_blocked guard、无private achieved feedback。本次没有time_budget_available纠正，故不能称已验证其自主续行收益；CPU真实调用链回归有效。任务仍失败、整体早停未解决，无自动重试。

### GROC-025：单个地面放置点超距被当成无处可放（2026-10-25）

任务及输入同GROC-024。模型抓袋到关门前，持袋open失败后在rgb-00009-front/[0.2,0.76]放置；私有首表面grounding确认为floor，选点XY距底座1.47957米，超过1.4米门限，out_of_reach正确。模型未尝试另一个放置点就称无更近清晰点并blocked；四图保存在staging_failure_views.jpg，可见其他地面，但其操作可行性尚未经探针验证，不断言全体可达。

修复层级为公共工具错误文案observations/boundary.py：所选point超距不等于整个surface不可达，提示选择同一目标表面更近可见点或移动后重选。没有改执行器、仿真器、距离门限、像素、场景或评分；无私有距离/坐标反馈新增。该后续文案不在两次冻结运行中，行为效果未验证，不专门追加文案/skill模型实验。

以后造task：距离错误应描述模型所选点的局部失败，避免暗示整个容器/支撑面不受支持；保留原像素/grounding和距离核验，再区分模型选择失败与设施缺陷。不能靠扩大reach让错误选点自动成功。


GROC-023/024/025汇总核验：tests/test_rgb_boundary.py、test_finish_clock_claims.py、test_finish_response.py共28通过、2子测试通过；覆盖公共反馈边界与真实结束调用链。两回合双视频均完整解码，冻结源码及实际交付图像核验通过。报告experiments/groc024_clock_claim_correction_20261025/REPORT.md；公开审阅http://10.130.140.115:8769/groc023_024_grocery_repairs_20261025_index.html，8个入口/报告/审阅/视频/审计链接HEAD200。两个监督器/launcher/proxy均退出，无后续排队回合。两场整体任务均失败；后备箱发现/抓袋链路有实际成功，整体早停仍是未解决的代理恢复问题。


### GROC-026：门前持袋暂存与开门再取回链路诊断（2026-10-25）

任务同GROC-024/025：carry_in_and_put_away_groceries/v2_design，house_double_floor_lower，301/seed0，BDDL b38c56b6，货物偏移[0.08,0]。原失败为抓paper_bag到闭门前，open因hand_occupied拒绝，随后在front/[0.2,0.76]的地面点放置因1.47957米>1.4米拒绝，模型没有第二次放置就blocked。

本案先暂停skill修改/完整模型重跑，以私有诊断检验“更近的RGB点放袋→开门→同袋再抓”的链路。冻结GROC-024原181应用文件和资产输入，GPU2共享锁、独立appdata，先重放9动作并核对每次模型像素的首表面对象、命中位置<=0.05米与成功/错误类别；偏离即停止，不把不同初态叫复现。到达原失败点后，人工从实际四图选择编号命令，最多12次有界动作、每次等待命令最多600秒，记录袋身份、原袋内物品Inside关系、地面支撑、门状态及连续录像。没有模型，没有任务成绩，不计自主成功。

待检验解释：原选点单独超距；更近点仍被放置器错误拒绝；或暂存成功但门开启破坏袋/内容关系或再抓取。启动状态仅记调查中；尚未确认设施缺陷，不扩大reach、不改scene/评分、不以手工动作注入自主模型。实验根experiments/groc026_door_staging_probe_20261025，scope.json/prefix_plan.json/launch.json/progress.json/replay/保留证据。


GROC-026诊断终态：9动作完整重放至原999模拟步，首表面/位置/反馈门禁均通过。相同底座位置人工RGB选back/[0.5,0.92]地面，命中距0.76353米，放置成功；同原front/[0.59,0.52]门板点在手空后open成功，袋AABB完全未变、floor OnTop保持；back/[0.5,0.93]袋棕色前壁再次grasp成功，确认同袋81。牛奶/番茄/奶酪Inside三件全程保持。3步96模拟步至1095，总录像完整解码通过。当前状态的链路可执行，未发现执行器缺陷，原选点超距拒绝正确；模型把单点失败扩大为无恢复是已观察到的代理决策错误。没有新改执行器/仿真器/skill/评分/距离。

复用资产：experiments/groc026_door_staging_probe_20261025/REPORT.md、recovery_recipe.json、recovery_acceptance.json、prefix_feedback_review.json、checkpoint_views.jpg/after_open_views.jpg及replay/episode.mp4。recipe仅给设施诊断，禁止放入模型上下文；单状态通过不证明跨场景鲁棒。模拟器close会退出应用，调用方关闭后字段未更新；独立审阅读取已持久化validation.json，未篡改原过程记录。

普通任务回归已独立冻结experiments/groc026_boundary_feedback_regression_20261025，在GROC-024原181文件中只改observations/boundary.py的一条公开out_of_reach消息（GROC-025已修补文案），明确所选点超距、其他近点可能可达。保留gpt-6.1-sol low、strict_mcp_tools_v1、clock_claim_correction_v1、同skill/场景/评分/预算；模型不收到探针像素/对象/路径。一次预定普通回合，不追加skill专项实验，不按成功重试。启动时结果未定。


GROC-026普通任务回归终态：gpt-6.1-sol low，8动作/636模拟步后正式blocked，原生初末3/12且goal_options未变。公开out_of_reach新文案实际交付3次、无时间声明纠正；未抓袋/未到门前，因此不证明自主暂存恢复或文案收益。末次grasp确实命中paper_bag_81，但距底座1.68156米>1.4米，拒绝正确；此前靠近的是舱盖而非袋身，没有进一步对袋身接近就结束。16次上游HTTP200/completed，无漏参，上下游调用哈希一致；181冻结文件、178份runtime源码与36张JPEG核对通过，双录像完整解码通过。证据groc026_boundary_feedback_regression_20261025/terminal_review.json、terminal_grasp_geometry.json、action_review.json、REPORT.md。进程均退出，无重试队列。门前设施链路可执行已确认，完整任务/代理早停仍未解决。


GROC-026审阅已发布：http://10.130.140.115:8769/groc026_door_recovery_20261025_index.html 。两次自主失败（历史参考+本次回归）与私有三步诊断分开，私有录像不计自主成绩；13个报告/视频/审阅/审计/图片链接HEAD200。原日志/快照保持，无任务执行器修补或skill新增；本轮交付是已定位的单点超距/过早结束原因、关键恢复链准入证据、错误反馈的一次普通回归及其失败结果。


SCENE-HOT-001修复进展（同日，用户授权修房间）：新增 CPU-only `scene_geometry.py`，适配器惰性注册 `CompleteShellInteractiveTraversableScene`。高层热饮配置恢复完整模板中的固定建筑外壳及原状态，外壳独立于家具房间筛选，启动后校验真实加载对象。未修改上游库、冻结资产、skill、任务定义或评分。版本 `complete_static_shell_v1` 冻结于 experiments/hot_drink_scene_shell_fix_20261025；`complete_static_shell_v2` 另冻于 experiments/hot_drink_scene_shell_fix_v2_20261025，后者再补齐原裁剪模板遗漏的一个可开窗、四段围栏及泳池，共恢复52个构件。v1为46个。

回归证据：真实加载器 AST 原过滤4/22，新过滤22/22，非外壳放行行为和已有对象/状态/metadata不变。最初地板/窗测试2失败后5通过；v2新增构件测试在冻结v1上1失败、维护v2上6通过，配合18后端与5执行器检查共29通过。v1私有GPU3探针实际244外壳对象在场，22块地板碰撞射线全通过；厨房到客厅及返回厨房两段连续行走通过（537模拟步），录像完整解码通过。第一次探针device API错误主动中止，第二次目标封装/冰箱绑定名错误退出，两次记录保留；第三次完成，不作为模型重采样。v1两张花园AABB中心图落在凹地板之外，不算渲染验收通过，v2改用射线确认的地板点取景。GPU3显存14529MiB未达20000门槛，v2首次启动被拒、没有仿真；转GPU2后验收运行中，结果待补。证据含冻结源、plan、真实GPU归属、配置、图像、导航和录像。

这是场景/加载层设施修复，不证明自主热饮或所有墙面材质通过。后续长程消融两组必须采用同一新场景版本，不能与历史几何结果混作同配置；旧分数与失败原样保留。预防：完整模板构件清单→实际运行对象清单→有效地面上的取景→碰撞→正式执行器行走逐层验收，不能以JSON数或非空画面代替。


SCENE-HOT-001第二版终态：complete_static_shell_v2在GPU2（UUID GPU-22ab12a0-af14-fa8c-44a7-a869af82e244）/301/seed0验收通过；250/250外壳对象实际存在，22/22地板碰撞与已确认地面上的取景点通过。人工复核22帧，花园地面与泳池实体可见；机器人四向图另留。厨房→客厅→厨房两段正式执行器连续行走均成功，共537模拟步，完整视频解码通过、launcher退出0。维护库两项场景修复代码与实际冻结执行代码字节一致；上游真实加载器/常量与离线AST冻结件一致。最终acceptance_review.json、REPORT.md、robot_views_contact_sheet.jpg、floor_render_contact_sheet.jpg及probe_run_v2/episode.mp4位于experiments/hot_drink_scene_shell_fix_v2_20261025。仅场景设施验收，不宣称自主热饮成功或穷尽所有墙面视角；历史失败/冻结回合保持原样。


### PROTO-LH-001：新版有无长程对照双方公开decision一致性（2026-10-26）

按用户在另一会话更新的docs/replay_configuration_review.md，两组分别为长程文档+三个计划记忆工具、四个基础skill+无三个工具；双方每次调用同需公开decision。默认skills模式原schema和prompt不要求decision，不能仅切换profile就称匹配。工具目录新增显式MAS_REQUIRE_DECISION=1，仅为skills模式开启与planning相同的decision；system_prompt同步说明，VisionHarness监督器收尾依据实际catalog必填项处理，旧默认模式不变。mcp_preflight保存真实tool_schemas，审查HTML据实际schema展示decision_required；不从当前默认值反推历史。

验证tests/test_comparison_decision.py/test_long_horizon_planning.py/test_build_review_metadata.py/test_transport_strict_tools.py共20通过。实际冻结预检六个共有工具schema完全一致，额外仅update_plan/remember/recall；应用代码180文件、仿真相关源码及资源1393文件分别冻结。预定一对热饮301/seed0、gpt-6.1-sol low、120动作/20000模拟步、无整场墙钟截止、高层评分、same_thread最多3次续接；complete_static_shell_v2。实验根experiments/hot_drink_lh_tools_pair_20261026，运行结果待补；不按结果追加。资源门槛在任何仿真前由20000改19000MiB，选定GPU3后两组共用。

### PKG-HOT-001：冻结上游遗漏启动图标（2026-10-26）

上述热饮无长程首次启动尚未到模型/MCP，_launch_app因缺upstream/docs/assets/OmniGibson_logo.png退出。冻结只复制OmniGibson/omnigibson包，漏了simulator.py按相对目录引用的启动资源，属于实验打包缺陷。原始启动失败、summary、trace、计划和哈希全部保留startup_failure_before_model_01；仅从同一上游补图标并固定哈希，源代码/skill/任务/评分未变后重新启动。没有模型任务结果，不作为按失败成绩重采样。预防：冻结包应覆盖代码外相对资源，运行真实启动链检查；不能用CPU导入通过替代完整启动验收。当前已越过原失败点并完成环境加载/250构件清单校验，相机预热中，模型成绩未定。


### SERV-HOT-001：长程对照被服务过载截断（2026-10-26）

热饮301/seed0，high_level_hot_drink_state_v1、complete_static_shell_v2、gpt-6.1-sol low、双方decision、无长程6工具与有长程9工具。无长程有效回合：120动作/26失败、4/11、正式blocked、2次same_thread续接，79分钟；首finish在54动作同4/11，7次拒绝后续66动作无额外目标增益，20wait含明确耗尽预算意图。高层拒绝blocked的监督造成部分非任务等待，不能称自主坚持。原始录像及调用/语义状态审计通过。

有长程原场11动作/7失败后连续8条Our servers are currently overloaded，CLI退出1、无正式finish；监督器aborted收尾，0/11只是前缀状态，不是有效skill失败结果。模型与仿真前缀在排除明确的supervisor finish后对齐；原审计false保留。控制器的--rate-limit-resumes只识别429，未覆盖此次过载，属于恢复范围缺口；本轮没有改冻结控制器。两组实际场景字节完全一致、环境配置仅输出路径不同，4基础skill相同、CLI均0.157.1、GPU3一致，不能归因为这几项配置偏差。

保留原无效配对及所有日志/录像/HTML，显式标注服务中断不计skill成功率。服务可用性单探针HTTP200/completed后，提前记录一次且仅一次基础设施补跑修订：只补有长程，使用原冻结应用与仿真代码/指令/skill/模型/预算/结束协议，同GPU3，新子目录infrastructure_retry_01；不按任务分数重采样，再中断则停止。当前补跑初始化中，结论待完成。所有额外尝试必须并列公开，原中断场不得被成功场覆盖。恢复机制后续若扩展到过载，须另版本验证same-thread、预算/像素保持和未完成续接后的过载情形，不直接改本轮样本。

证据experiments/hot_drink_lh_tools_pair_20261026下infrastructure_interruption_review.json、service_availability_probe.json、infrastructure_retry_amendment.json、first_finish_review及baseline_cost_manual_review；原paired_configuration_audit为false，不宣称当前已有有效组间结论。


### HOT-HIGH-001：高层烧水前置条件被误包装为执行异常（2026-10-26）

热饮当前无长程冻结回合动作24/25，private_executor_error均为invalid_precondition（先把有水水壶放灶上），public_execution_error因allowlist缺此码降为execution_error，提示“执行失败或部分完成”，掩盖实际失败阶段。不是未捕获崩溃。unsupported_relation在同回合用于grasp灶具/台面，却提示placement/attachment，动作语义也不一致。

维护库修复仅错误反馈边界：invalid_precondition保留类型并用静态、无私有状态的准备提示，unsupported_relation改成所请求action不受支持；VisionHarness记录action_error_feedback_protocol=rgb_error_feedback_preparation_v1。tests/test_precondition_feedback.py用真实私有错误复现，修复前1失败；连同test_rgb_boundary.py修复后17通过，保留私有detail不外泄。没有放宽reach/可执行动作、没改任务目标或skill。证据experiments/hot_drink_lh_tools_pair_20261026/analysis_feedback_repair。冻结180源文件哈希复核全部不变，此补丁不在正在跑的对照中，行为收益未验证。

### HOT-HIGH-002：高层热饮目标白名单限制普通探索/暂存（2026-10-26，未修复）

同无长程回合26失败中，9 invalid_visual_target、7 out_of_reach、6 unsupported_relation、2前置条件、1hand_occupied、1navigation_unreachable。逐像素日志显示动作6/29/31的open实际命中bottom_cabinet_fancyy_0、top_cabinet_lkxmne_1、bottom_cabinet_no_top_rkgjer_0，初始化有3/2/4个关节，但高层execute_visual仅接受_semantic_targets任务绑定对象，其他柜子在开柜执行前就invalid_visual_target。确认的是接口覆盖拒绝；并未由GPU探针证明放开后每个柜子的物理open都成功，不能把它们全归为模型选错像素，也不能直接称执行器物理开门失败。

动作78 place_on_top命中floors_uzsntg_0，当前高层接口没有地面暂存支持；该次模型实际意图仍是放茶几，所以既有选点偏差也暴露恢复能力覆盖限制。三次sofa命中与wall/台面/灶具grasp也不能混作同对象重试。高层out_of_reach使用目标或容器AABB的1.5米检查，原生分支按所选点的检查不是同一契约，仍需逐场几何核对才可归因。

当前对照代码保持冻结，不在两组之间扩充操作空间。下一设施版本应先做普通柜open/close及暂存的有界复现，明确哪些动作只改变环境、哪些进入任务状态与独立重放；回归后再冻结发布。证据failed_target_grounding_review.json、without_private_error_review.json、without_failure_inventory.json及真实scene初始化/grounding_diagnostics。修复房间加载/碰撞/往返不等于完整抓放/探索链验收通过。

HOT-HIGH-001补充验证：显式PYTHONPATH指向维护库src，precondition/boundary/真实Harness decision检查共20通过（explicit_import_green.log）。未指定路径的额外测试曾误导入另一editable checkout而2失败；纠正导入后通过。实际活动实验launcher/proc环境已独立核对，指向本轮冻结source/src，未发生该测试导入污染；证据active_launcher_import_scope.json。HOT-HIGH-002仍未修复，当前对照继续冻结运行。


### PARTY-001：派对后清理任务无现成实例，原生采样在毛巾布料加载失败（2026-10-26，未解决）

用户指定 `clean_your_house_after_a_wild_party`。原始 BDDL SHA256 `6916c430efa6c7d3d0aa1be094d83cb27d3ad7c9d9d72d9f86b1bf9b778e6169`，共14项终态目标；知识库含此任务，但本地公开100任务注册与场景JSON均无该任务/实例。采用 house_single_floor、seed0 的隔离原生采样探针，完整固定外壳及厨房/客厅/浴室和连接通路，GPU0（GPU-2f2ab185-9827-23e7-6e54-ca06c0f214f3）、GPU dynamics，保留原始水/液体皂/布料机制。本轮未调用模型，不产生原生整任务分数或skill成绩，不计为官方公开任务实验。

真实完整采样在场景导入后、任务对象导入时失败：EntityPrim._post_load 的 cloth_mesh_prim 为 None，GetPath 抛 AttributeError。无机器人、无任务的最小真实空场景单独加载 hand_towel/klevhj（CLOTH）再次复现。其真实视觉网格存在于 base_link/visuals/klevhj__base_link，而加载器只查 link 直接子节点，确认资产层级与加载器不匹配；不是模型选点错误。

隔离层级兼容候选 nested_single_visual_cloth_mesh_v1 仅调整运行时USD副本中唯一视觉网格的层级并保留相对变换，随后调用原加载器；越过原GetPath错误，但在 ClothPrim._remesh→mesh_prim_mesh_to_trimesh_mesh 读取normals后的张量转换处抛 numpy.object_ TypeError。后者的属性/转换根因尚未确认，布料仍未成功加载，候选未采用。维护库执行器、上游冻结源码、原资产、BDDL、skill与评分均未修改；没有删掉毛巾或将其改为刚体。

证据根 `experiments/wild_party_feasibility_20261026/`：REPORT.md、TASK_REVIEW.md、sampling_result.json、towel_loader_original/、towel_import_result.json、towel_candidate.log、attempt_registry.json及三个源码/输入manifest。首次探针准备因验证回调签名适配主动中断，独立保存在 sampling_setup_attempt_01，不计任务失败。静态24原子动作的目标覆盖通过，不代替物理验证；166维护库源文件、1389上游文件及2输入文件哈希终检未变。所有本轮模拟器已退出。

防复发与下一验收：先在最小资产场景跑真实布料载入和短物理步，兼容候选通过后再重跑原始完整采样；逐项检查初态、重载、RGB可发现性及工具准备→清理→独立谓词链。新研究实例单独冻结并标注来源；现有运行器还依赖公开100任务的人类参考数据，本任务未提供该数据，不能伪造归一化指标。不得将此初始化失败计作模型失败或skill对照分母。诊断页 http://10.130.140.115:8769/wild_party_feasibility_20261026/index.html （启动失败，无连续自主录像）。

### PROTO-HOT-002：高层blocked结束守卫诱发耗预算行为（2026-10-26，未修复）

当前冻结热饮对照中，高层finish仅在动作/调用/模拟步预算耗尽或特定不可恢复仿真错误后接受blocked，反馈明确要求耗尽预算；这与原生任务的结束契约不同。无长程首finish54动作4/11，后续66动作无最终目标增益，含20次wait且明确耗预算。有长程补跑首finish34动作2/11，后续确实抓盘/送蜂蜜/送餐垫到5/11，说明早停不能直接叫无法恢复；但95动作起也出现公开意图“消耗剩余仿真预算以关闭回合”的wait，随后又尝试导航。

不能把守卫后的全部动作叫自主坚持或纯恢复成本，也不能把首次blocked等同终端不可恢复。当前对照保持相同守卫冻结，首次finish前缀独立重放与终态分列；有长程补跑仍在进行，本条不预判最终分数。下一协议版本需单独设计/验证合理blocked准入与恢复证据，避免用耗预算代替任务推进；不在当前两组之间改结束监督。证据VisionHarness.finish/_premature_blocked_finish、baseline_cost_manual_review.json、retry_interim_first_finish_review.json及原始events。

2026-10-26终态补记（PROTO-LH-001/SERV-HOT-001/HOT-HIGH-001/002/PROTO-HOT-002）：唯一基础设施补跑正式结束。本轮有效两场：无长程120动作/26失败/4目标，有长程120动作/43失败/5目标，均整任务失败；首次finish分别54动作4目标与34动作2目标。守卫后续分别66动作净增0目标、86动作净增3目标，wait分别20/9；有长程update_plan2、recall1、remember0，未读取可用visual-exploration。不能据一个开发seed宣称成功率收益，不能把强制续接全部叫自主坚持。

两场配置、同GPU3、四基础skill、实际场景/环境、运行时各78份源码、180份冻结源、全部线上model/effort请求、独立语义重放、首次停止前缀、调用对齐及整段视频解码通过。原有长程11动作的服务中断仍单列，不能将其前缀0/11计skill失败；补跑仍有69条overload与2条stream_disconnected客户端事件，无长程6条overload，均非失败请求数或服务商模型身份鉴定。墙钟78.94/108.15分钟不作因果归因。高层评分非原生BDDL成功。

HOT-HIGH-001补充实际pour117：空手倒水被正确拒绝empty_hand，但公开提示只列place/attach/release，维护边界改成通用持物前置要求；反馈版本rgb_error_feedback_preparation_v1、manifest保留修改前后，20项回归再次通过。当前冻结两场仍使用旧反馈，补丁无已验证自主收益。HOT-HIGH-002柜门/暂存限制、PROTO-HOT-002结束守卫未修复，待独立版本验证。105 open任务柜体成功；106/107实际是grasp台面失败，不归白名单拒绝。记录澄清避免将滚动状态当逐请求证据。

报告experiments/hot_drink_lh_tools_pair_20261026/REPORT.md，manual_terminal_review.json/all_attempt_failure_inventory.json/completed_pair_audit.json/attempt_registry.json保存复核。发布http://10.130.140.115:8769/hot_drink_lh_completed_pair_20261026_index.html，19项链接及公开IP入口HEAD200。原中断/失败录像、原无效配对/报告不覆盖；无后续实验队列，未进行新的skill修改或push。


### PARTY-001 补充：原生布料加载与缺失法线已通过最小物理回归（2026-10-26）

用户进一步明确目标为把本任务实验成功跑起来，继续修复完整链路。新证据根 experiments/wild_party_runtime_20261026。源与复制后毛巾属性摘要确认原资产 normals 本来就是 None，points/faceVertexCounts/faceVertexIndices/UV 均存在且复制前后一致；不是复制丢失法线。nested_single_visual_cloth_mesh_normals_v2 在运行时副本保留唯一视觉网格及相对变换，并由原三角面计算缺失法线；原资产未写回。最小真实 hand_towel/klevhj 已以 ClothPrim 完成原生重网格化与10物理步，证据 cloth_normals_pass/。维护库新增 opt-in cloth_compatibility.py；仅在显式研究实例协议要求时安装，普通及已有冻结回合不受影响。此证据只验证载入和短步，未证明完整抓取/清洁或任务成功。

### PARTY-002：原生 GPU 初态采样中的设备与几何读数不一致（2026-10-26，调查中）

相同原始任务、house_single_floor/seed0 的 native_sampling_v2 已越过全部毛巾导入，在 OnTop 初态采样的 quat_multiply 抛 Triton CPU pointer 错误。真实 CPU/CUDA 四元数最小调用中，同设备两格通过，混设备两格复现；关闭 torch.compile 虽能做乘法，quat_apply 混设备仍失败。设备边界统一后，含180度旋转的六格数值检查通过，无数值/动作/像素补选。

真实 DatasetObject 盘子→茶几的最小 OnTop 探针再次复现同一原始异常。统一 quaternion、get_base_aligned_bbox 和采样结果的设备后不再异常，但 setter 仍 false；启用原生接触缓存也未通过，不能认定设备修补已修通放置。进一步记录显示射线候选有效，而 sampler 返回时盘子物理位置和几何包围盒位置明显不一致。正在用 pose_bounds_probe 独立区分 Fabric/PhysX 同步、局部网格变换与真实动力学问题，未确认最终根因。最初 PrimitiveObject 最小夹具在自身GPU序列化提前失败，另存 kinematic_fixture_attempt_01，不当作本任务新缺陷。

生成研究实例的显式入口已实现于 research_native.py 与适配器：验证输入哈希/任务/实例身份，保留原生谓词独立评分；无公开100任务人类参考数据时不调用归一化聚合，不伪造公开成绩。相关新检查与既有 RGB/导航/runner 检查27通过、2子测试通过。实际完整实例、初态验收、自主模型和录像仍未完成；当前不宣称 benchmark 准入或目标成功。

### PROTO-LH-002：无长程组移除当前子目标字段（2026-10-26）

用户明确无长程组不写当前子目标，但保留调用意图。此前MAS_REQUIRE_DECISION=1给skills/planning同样四字段，给无长程引入了子目标记录要求。新profiled_public_intent_v2：skills比较仅state/reason/expected，planning保留active_subgoal；三个计划记忆工具与长程文档仍仅planning开放。tools/__init__.py与vision_policy同步，VisionHarness按真实schema生成监督器收尾，run.json保存每工具必填字段；build_review读取保存schema、缺失字段不渲染空子目标栏。AGENTS与replay_configuration_review同步，历史四字段回合不改名/重跑/改原始证据，旧分数不与新协议混合。

真实Harness回归验证3/4字段、缺参拒绝、有效initialize、监督器cleanup、规划工具与skill可用性和默认legacy skills无decision；审查元数据另覆盖已保存三/四字段不从当前profile反推。未改任何机器人skill。

PROTO-HOT-002维护修复（同批协议）：新热饮默认MAS_HIGH_LEVEL_BLOCKED_POLICY=accept，带事实理由的blocked可在预算仍有剩余时正式关闭，独立评分保持失败，不泄漏评分；exhaust_budget显式保留旧守卫。termination_contract记录策略/版本/guard，prompt同步说明。原旧冻结源在真实finish边界重现两组blocked不关闭，另检出无效配置未拒绝，共3失败；维护代码修复后相关检查52通过、2子测试通过，新增审查元数据检查亦通过。最初测试夹具误用已存在输出目录的失败日志保留，修正后在旧冻结源重新获得真实症状red，不将夹具错误算成修复证据。

证据experiments/hot_drink_benchmark_repair_20261026下red.log/green.log（夹具错误保留）、red_real_finish.log、final_green.log、review_green.log、冻结source/manifest。仅CPU契约证据，不证明自主失败动作减少；接受更早失败也不代表执行器提升。HOT-HIGH-002普通柜门/暂存覆盖仍待私有有界复现，下一GPU实验配置等待用户确认，尚未启动。

PROTO-LH-002/PROTO-HOT-002最终冻结复核：以原配对180文件为底，仅叠加五处已验证的协议/反馈文件，所有skill保持原字节。其他会话四处原生research支持修改另存捕获件和排除清单，未回退维护工作区。冻结候选实际导入路径下53项测试、4子测试通过（frozen_candidate_green.log）。私有柜门诊断proposed_probe_configuration.json待用户确认，未启动新仿真；不宣称失败动作已减少。


### PARTY-002 补充：原生位姿与几何同步已通过真实 OnTop 回归（2026-10-26）

pose_bounds_probe 独立确认 PhysX 已移动盘子，Fabric 几何仍留在旧位置；仅同步 PhysX 再更新 Fabric hierarchy 后两者一致。显式研究协议 native_gpu_pose_and_geometry_v3 在原生私有物理步后刷新该几何，不增加物理步、不改变采样点；并统一四元数及包围盒/采样结果设备。真实盘子→茶几探针在原生接触缓存开启时 setter、Touching、OnTop 全部 true。证据 wild_party_runtime_20261026/kinematic_geometry_sync_pass/；此前失败目录保留。仅证明本关键采样链，不证明完整任务或模型成功。

### PARTY-003：GPU 物理重启预热期读取失效接触视图导致原生崩溃（2026-10-26）

原始任务/house_single_floor/instance0/seed0，BDDL SHA256 6916c430efa6c7d3d0aa1be094d83cb27d3ad7c9d9d72d9f86b1bf9b778e6169。完整 native_sampling_v3 在布料加入后的物理重启阶段以 -11 退出；栈为 SimulationManager.initialize_physics → post-physics callback → RigidContactAPI.add_contacts_from_physics_step → get_contact_force_matrix。最小真实夹具先建立盘子/茶几接触视图，停止、加入原资产毛巾 ClothPrim 后重启，完全复现相同崩溃，证据 contact_warmup_original/。不是模型、任务目标或策略失败。

维护层新增显式 opt-in simulator_contacts.py，仅 SimulationManager._warmup_needed 为真时推迟读取，预热完成继续原生采集；不永久关闭缓存，不填造接触。相同原生夹具通过：前后各1接触视图、毛巾 ClothPrim、重启后10仿真步；统计10次预热回调推迟、15次正常采集完成，证据 contact_warmup_guard_pass/。研究实例 backend 可显式选择 native_contacts_after_physics_warmup_v1，既有冻结源/上游/资产不写回。完整 native_sampling_v4 正在验证，仍未启动自主模型。当前维护源 CPU 相关回归44通过、4子测试通过；单独通过 CPU 或最小夹具不等同完整 benchmark 准入。


### PARTY-004：GPU 布料的 PhysX Fabric 输出与原生 USD 状态不兼容（2026-10-26）

相同任务/场景/seed及原始定义哈希沿用 PARTY-003。完整 native_sampling_v4/v5 分别暴露非 DatasetObject 的 AABB 设备边界、CPU 射线采样函数读取 CUDA 包围盒的问题；两份错误栈与当时源码分别保存 native_sampling_attempt_v4/、native_sampling_attempt_v5/。simulator_devices.py v4/v5 为 opt-in 调查适配，不宣称完整任务通过。v5 的动态茶几探针无设备异常且最终 Touching/OnTop true，但 setter 返回 false，仍不是完整采样链验收。探针包装与适配入口冲突的首次夹具失败另存 kinematic_dynamic_fixture_failure/，未算作任务缺陷。

布料自由落体最小实测：同一毛巾虽为 ClothPrim、质量0.125、GPU动力学开启，私有物理步及普通步后几何和接触未变化。分别排除了质量为零、变形体 beta 模式、单独 USD 读回开关、单独 CPU 张量管线、Fabric 开启时显式 update_transformations；原始记录均保留。另造原生 API 四顶点布料作对照，亦静止；这将问题定位到仿真/读回链，不能归因于模型或清洁 skill。

真实通过配置：GPU dynamics 保持 true，CPU tensor pipeline（不是 CPU dynamics）、关闭 PhysX Fabric 输出、开启 updateToUsd/updateParticlesToUsd，并在物理步后将原生 USD 状态同步到渲染用 Fabric stage/hierarchy。cloth_no_fabric_result.json 记录两种布料均运动、原毛巾 Touching/OnTop true；进一步干净的 cloth_runtime_pass/ 使用 simulator_particles.py 的 native_gpu_particles_usd_readback_v1，原生通用 sample_kinematics 后毛巾 Touching/OnTop true，另120物理步后仍 true。未替换布料类型、目标、粒子、评分或采样返回；适配器额外物理步为0。关闭的是 PhysX Fabric 输出，渲染 FSD 仍同步真实 USD 状态，禁止把此配置写成 CPU 物理。

维护库仅在研究实例 particle_compatibility 与 tensor_pipeline_device 显式声明时安装该协议；其他回合保持原协议。完整 native_sampling_v6 正在以此配置重新采样，不安装 CUDA 混设备调查适配。模型回合仍0，完整场景初态/关键清洁动作/自主运行/视频尚待验收。速度接口警告另经真实写入读回确认功能有效，未因警告修改速度实现（gpu_velocity_result.json）。

HOT-HIGH-002私有诊断终态（2026-10-26，用户确认后执行）：热饮301/seed0、complete_static_shell_v2、GPU3 UUID同原场。原前5动作反馈、模拟步、底座位置和任务语义状态匹配，原像素首表面身份/位置<=0.05米门禁通过；第6动作同柜invalid_visual_target重现。相同位置/像素原生open、fresh-frame close均成功，bottom_cabinet_fancyy_0三门0→1.919862→0弧度、Open False→True→False，任务状态未改。共8动作388步，未触发watchdog，连续视频完整解码、launcher退出0、180源/上游/资产/诊断脚本哈希保持。人工复核开关对照图；仅一柜一状态的无模型私有诊断，不计自主成功，也不证明原模型把柜当冰箱的识别正确。

维护修复：RGBBackend对未绑定任务的open/close按Open能力调用既有原生状态执行器，保持空手/所选点1.4米reach/显式导航/后置验证；不扩展grasp或放置，也不改任务11目标。返回仅私有non_task_open_close_v1元数据和未变任务状态摘要；独立重放按保存任务绑定、像素/图像/对象根路径、空手/操作种类/摘要验证，拒绝用标记跳过任务物品或隐藏状态变化。build_review记录执行协议。真实路由red4失败后，维护及隔离冻结source_v2均96测试、4子测试通过；scope包括公开身份不泄漏、reach/手占用/失败传播、审计篡改拒绝和历史协议。source_v2相对已测source再改3文件，未纳入其他会话research支持，也未改skill。

证据experiments/hot_drink_benchmark_repair_20261026/PROBE_REPORT.md、probe_01/diagnostic_result.json/acceptance_review.json/cabinet_contact_sheet.jpg/run/episode.mp4、router_red.log/frozen_v2_green.log/source_v2_manifest.json。新高层路由尚无GPU验证，自动任务失败下降未证实；接口新版本回归配置proposed_interface_regression_configuration.json等待确认，未排队新实验。离线新审计信封适配仅作用真实记录副本，不冒充新GPU结果。


PARTY-004 完整场景后续：native_sampling_v6 在机器人控制的 pre-physics 回调报 expected device -1, received device0。原安装源 backend_utils._numpy_to_torch 由 gm.USE_GPU_DYNAMICS 推断 CUDA，把 GPU 动力学与张量传输设备混为一谈；本实例 PhysX 视图明确为CPU。显式粒子兼容 v2 仅将该桥的 to_torch 转换对齐实际 sim.device，数值和目标不变。真实 R1 小场景10步后加入原毛巾，再30步已无控制异常、机器人有限且布料下落；首次探针“30步毛巾最高点必须低于0.5m”的额外阈值未达到，证据 robot_particle_first_window/ 原样保留，不将阈值失败当作控制桥新缺陷。已延长同一实际物理窗口到120步验证最终下落，完整 native_sampling_v7 同时继续。尚未启动自主模型，所有前次完整初始化失败保持可追溯。


### PARTY-005：完整布料场景在备选台面失败采样中发生 PhysX GPU 求解器错误（2026-10-26，未解决）

PARTY-004 的控制设备修复经 R1 原生130个 env.step 与布料共存验证通过，毛巾落至地面，证据 robot_particle_result.json / robot_particle_extended*。完整 native_sampling_v7 首次通过浴室毛巾地面、厨房 agent/地面，以及 countertop_kelker_0 上盘子与毛巾的全部初态；继续验证另一个备选 countertop_kelzer_0 的毛巾放置时，最早错误为 PxgTGSCudaSolverCore GPU solveBlockUnified fail to launch kernel，随后700 artiPropagateImpulses2，与更晚 torch.compile 的 CUDA illegal memory access 区分。后者是损坏CUDA上下文的迟发报告，不能直接称 torch.compile 缺陷。完整原始证据 native_sampling_attempt_v7/。目前尚未确认是失败状态恢复、布料碰撞还是GPU求解器的具体根因，不宣称普适修复。

研究实例构造 v8 仅将 countertop.n.01_1 的家具候选固定为同一原房屋中已通过的 countertop_kelker_0；仍调用原生谓词采样并检查全部原始初态，不修改目标、资产类型、评价或采样返回。construction_binding_selection 进入结果记录。构造实例无需穷举每块台面，这是一项显式实例选择与避开不稳定构造路径的措施，不证明运行期失败恢复已经可靠。其他家具候选保留，完整初态/保存重载/原生14目标的关键动作与自主实验仍待验收，暂不宣称 benchmark-ready。


PARTY-005 后续反证：v8 固定已通过台面后仍在下一柜内放置搜索出现 GPU700，故“只由备选台面触发”假设排除。原进程确认已损坏后仅终止本回合 PID，stop.json 留痕；所有原始日志保留。v9 仅改原生 PGS 求解器，仍出现非法访问，未将其写成修复。小场景 R1+原毛巾+柜子+拖把，10次原生全状态恢复和一次完整 Inside 搜索未复现GPU错误，不能据此宣布整房间恢复可靠。关闭实际 PhysX GPU dynamics 的对照明确报粒子/布料仅支持GPU，排除以CPU刚体偷偷替换本任务物理配置。

### PARTY-006：USD 粒子运行时缺少刚体瞬时位姿的 PhysX→USD 读回（2026-10-26）

使用同一原生盘子的最小复现：粒子运行时v2中，set_position_orientation([3,4,2]) 后 PhysX 已在新位置，包围盒仍在[0,0,2]；第二次瞬移到[6,7,2]也仍读[3,4,2]。不是物体未移动。显式调用原生 PhysX.update_transformations(False,True,False,False) 后，无额外物理步即得到正确包围盒。证据 pose_readback_original/。

v3在原生 USD→渲染Fabric 同步前先flush待处理USD编辑并读回当前PhysX变换，使用原生命周期标记绕过仿真自身写回的USD编辑守卫，不增加物理步、不更改位姿数值。完全相同的两次瞬移在 setter 返回时包围盒均正确；同版布料的通用原生 OnTop + Touching 仍通过，120物理步后保留接触，证据 pose_readback_pass_v3/。v2小场景 Inside setter=false 却 getter=true 的回滚读数也纳入后续回归；完整 v10 正在验证，本条仅修复已证实的读数缺陷，不宣称GPU求解器根因已解决或模型实验成功。


HOT-HIGH-002 新高层接口GPU回归通过（2026-10-26）：用户确认后执行 interface_regression_01，冻结source_v2，热饮301/seed0/complete_static_shell_v2/GPU3同UUID。原5动作grounding/反馈/步数/位置/状态匹配，高层act open与close均成功，全程未关闭high_level_harness，无native回退。7动作0失败/388步，同一非任务柜Open False→True→False，任务状态不变。原始事件独立重放2任务提交+2环境操作，2条任务日志匹配；13项验收通过，396帧审计视频完整解码并人工复核开关图。源/上游/场景/脚本哈希保持。证据experiments/hot_drink_benchmark_repair_20261026/INTERFACE_REGRESSION_REPORT.md及interface_regression_01/。仅一柜接口修复验证，不是自主成功；普通物品暂存/全柜覆盖仍未扩展，原模型误认冰箱仍是识别问题。历史失败、probe_01与旧配对未改。用户最新授权后续实验不再逐场确认，直接运行并记录冻结配置和全部失败/审计录像。


### PARTY-007：整屋 GPU 搜索仍不稳定；场景约简与柜内初态可达性调查（2026-10-26）

PARTY-006 的读回修复经小场景验证：Inside失败回滚后 getter=false、物理位姿及包围盒均恢复，不再出现原v2的假阳性；原130步R1+布料及10恢复周期保留通过。完整v10在数个柜内失败搜索后仍报GPU700，故不能把读回修复视为GPU求解器问题已解决。v11使用原生采样器允许的最小动态缩放0.5（mop/qclfvj）及已知家具候选，仍报GPU700；失败及本会话终止PID证据独立保留。

真实小场景同柜模型、同0.5拖把已获得原生Inside setter/getter true；R1+两条原毛巾的100次全状态恢复和柜内采样亦通过（two_cloth_restore*）。因此“双毛巾本身必然崩溃”假设排除，不把完整场景故障归因于skill或模型。

另制 task_rooms_curated_native_v1，保留原房屋固定地板/墙/天花/固定窗和任务家具/照明，595对象中保留267；删除无关家具及活动门窗以缩小复现规模。保留对象的资产/几何/位姿原字节，原始BDDL与14目标/布料/粒子类型不变；curated_scene_v1_manifest.json 保存完整增删与哈希。这是明确的新研究场景变体，不是原整屋修复或公共实例。原屋导航图暂沿用，模型导航/可见性尚未验证，不宣称准入。v12没有GPU错误，但柜内拖把初态setter失败，反馈明确拒绝厨房绑定，未启动模型。

正在v13记录Inside内原CAD根点与对象AABB中心的原生可达性结果，并以对象中心作为构造时的可达性检查点；原生Inside物理采样、谓词本身及最终全初态检查不变。此举只涉及离线构造启发式；当前尚无完整初态或自主成功结论。审查元数据补充scene_variant、拖把尺寸/家具选择与CPU张量/GPU动力学的独立字段，相关CPU回归49通过、4子测试通过。


PARTY-007 后续：v13没有任何Inside可达性回调，故本次拒绝发生在该检查之前，“根点/中心可达性造成此拒绝”未获支持，中心点改动未进入后续实例。v14在原定义未限制的清洁柜打开后，约简场景的拖把、洗洁精瓶、喷壶均通过原生Inside；原整屋仍GPU失败并单独保留。最终阶段再次随机采样拖把时300次失败。v15在最终阶段先逐项检查原生初始空间谓词，实测18项全部已真、真实拖把scale=[0.5,0.5,0.5]，故保留有效布局而不再破坏它；没有将false判定改为true。六处污物与洗洁精Filled均成功设置，随后才遇到PARTY-008。新构造协议validated_layout_reuse_or_clear_prefilter_v1：只有原生已真关系可复用，否则清理候选筛选留下的样本摆放后按原采样器重采；最终仍要求完整原始初态复核。

### PARTY-008：ParticleRemover默认Saturated条件未转换物质synset（2026-10-26）

约简原始BDDL构造v15在已完成18空间关系、Filled、六Covered污物与两个回收桶Open之后，第一次普通仿真步报System water.n.06 is not a valid system name。栈为ParticleModifier._update → check_conditions_for_system → SATURATED条件 → Scene.is_system_active。原生postprocess_ability_params只转换conditions显式项，漏了default_non_fluid_conditions/default_visual_conditions等默认列表，导致water.n.06流入期待实际系统名water的函数。不是用户任务目标、模型策略或水系统缺失。

维护库新增显式material_conditions.py协议native_default_saturation_system_names_v1，仅用原生get_system_name_by_synset转换默认SATURATED的参数；保留条件类型/逻辑、显式条件及其他值，复制参数以免污染共享能力定义，零或多系统映射沿用原生严格拒绝。真实同型号mop/qclfvj对照：原代码默认非流体/视觉条件均复现AssertionError；适配后干工具两项false，原生Saturated水/皂状态设置后两项true，随后3个原生步无异常。证据native_material_conditions_pass/，未放宽清洁条件或制造任务成绩。backend仅显式研究spec声明时安装，普通/冻结回合不变。v16继续完整构造，尚无模型回合或benchmark准入结论。

PARTY-007/008 后续（v16→v17）：默认材料条件最小回归通过，但v16同一打开柜/0.5拖把的原生初态筛选仍随机失败，柜候选为空；未进入模型。v17给原生Inside失败增加最多3次重试，位置仍全部由原生采样器生成与检查，独立最终原初态验收不变，协议validated_layout_reuse_native_inside_retry_v2。另核查发现v15/v16 before_final_kinematics列表实际为空，先前“逐项预检查18关系”表述不准确；18项证据来自最终阶段每个原生谓词实际求值后复用的记录，而非该空列表。该空预检查不可当验收证据，最终完整初态及重载检查仍必需。material/backend维护改动后相关CPU回归52通过、4子测试通过，尚无完整任务或自主成功。

PARTY-007/008 构造验证：v17最终通过原始30项初态，4组替代绑定各14目标初始均false，原生full/TRO状态与四路512RGB已保存；instance0_v1/spec.json冻结scene/TRO/原始BDDL与哈希。柜内拖把在首个原生调用即通过，本次未实际触发额外Inside重试，不宣称重试使其成功。首次四路RGB初始化耗时约320秒后完成，非已确认死锁；无堆栈证据，不据此前等待修补渲染器。construction_v17_evidence保存证据与源码哈希。完整实例重载、动作链及自主模型仍待验证。

### PARTY-009：内嵌机器人被场景配置过滤，重载任务为空机器人列表（2026-10-26）

instance0_v1/原始BDDL哈希6916c430efa6c7d3d0aa1be094d83cb27d3ad7c9d9d72d9f86b1bf9b778e6169，executor_probe_r1尚未执行任何动作即在BDDLSampler访问env.robots[0]时报IndexError。environment_config.json同时robots=[]及include_robots=false；已保存模板含Robot，适配器据此不再配置另一机器人，但继承generate_basic_environment_config禁止内嵌机器人标志。原生Scene._should_load_object据该标志过滤Robot。确认harness配置错误，非布料状态重载或模型失败。维护omnigibson_backend._config检测到内嵌机器人时显式include_robots=true，沿用原Robot资产/控制器/状态及后续真实初态检查；新冻结execution_source_v7，旧r1与v6保留，r2在相同冻结实例做真实回归。

PARTY-009 后续：r2 include_robots=true仍无机器人。第二个独立过滤在InteractiveTraversableScene._should_load_object，原生保存的Robot无in_rooms，任务房间过滤将其拒绝；不是布料重载错误。test_scene_geometry新增真实配置语义回归在旧策略1失败6通过（robot_room_filter_red.log）。CompleteShellAdmission只对已规范化Robot且缺失房间标签时遵循显式_include_robots，家具及已有房间对象仍走原过滤。r3冻结execution_source_v8同一实例验证。

PARTY-009 r3真实回归通过：机器人实际加载、31必需绑定齐全、full/TRO恢复成功、原始30初态全部true、四路RGB初始化通过。CPU相关检查22通过/2子测试。首次checked open dishwasher物理步却触发PhysX GPU artiPropagateImpulses2 700，后续None.view是CUDA上下文失效后的次生错误，不归因于目标评分或模型。属于PARTY-005 GPU故障在约简完整动作链中的新复现；native_open_probe_r1省略相机，顺序区分基线env.step、无anchor原生Open、带anchor的checked动作。原始r3证据保留；无模型启动。

### PARTY-010：原生布料系统的粒子计数接口不适用于刚体抓取容器检查（2026-10-26）

r3后续私有盘子抓取在_grasp_particle_payload触发NotImplementedError：循环先读system.n_particles，再检查is_physical_particle_system，而原生cloth系统继承抽象计数接口。维护carry.py交换守卫顺序，先确定原生物理粒子系统，再读计数；不会搬运或忽略原本应随容器移动的水粒子。已有容器水跟随回归加入抽象计数cloth混合系统，旧逻辑1失败3通过（cloth_counter_red.log），修复后抓取/倒水检查9通过（cloth_counter_green.log）。这是独立已确认执行器错误，未解决r3首次开门GPU故障，也未证明自主成功。

### PARTY-011：布料重网格拓扑未随粒子状态冻结，实例重载不稳定（2026-10-26）

同一instance0_v1的r3完整重载曾30初态全部通过，但native_open_probe_r1在场景首次加载即报粒子数6232对6147不匹配，未运行任何基线动作。原生ClothPrim在非单位scale时每次_remesh，通过世界空间pymeshlab重划网格；保存full/TRO只携带粒子位置/速度，不含当次面拓扑。至少粒子数变化已确认；同顶点数而面拓扑变化是否参与r3 GPU非法地址尚属假设，不能宣布已解释或修好GPU故障。

新增显式cloth_topology.py协议native_remeshed_cloth_snapshot_v1：构造时调用原生_remesh后立即保存其原始点/面/法线/UV/插值/scale；重载在原生clothify之前恢复同一网格，随后仍使用原生布料物理、状态恢复与初态验收。manifest及每份网格独立哈希，不丢弃粒子、不截断位置、不重新猜测面索引。v18重新构造独立实例并捕获原生网格，旧v1及失败全部保留；backend仅研究spec显式启用。CPU输入契约覆盖同顶点数但面变化的哈希拒绝；实际两次重载/物理步尚待验证。

HOT-HIGH-002 / PROTO-HOT-002 自主回归启动（2026-10-26，非结果）：用户要求继续且明确后续实验不再逐场确认配置。新目录 experiments/hot_drink_lh_repaired_pair_20261026，共享180文件source_v2、四基础skill、完整外壳v2，热饮301/seed0，astra/low，120动作/20000步；高层11目标，非原生官方成功。无长程state/reason/expected，有长程另含active_subgoal和3计划记忆工具。blocked接受且未完成计失败。先无后有，同GPU0串行；GPU3锁被其他会话占用，首回合前记录gpu_schedule_amendment，未干扰其他会话。旧sol组模型不同，跨轮不能视作执行器单因素因果比较。当前仅启动，结果待独立审计、连续录像和首次finish/失败成本复核；禁止将私有接口通过计为自主成功。

PARTY-011 v18构造通过30/30原初态，冻结instance0_v2及两份原生6147顶点/12290三角面网格。native_open_probe_r2重载记录拓扑SHA与构造完全相同，但约120秒的定时faulthandler输出在material_prim.py帧处中断、进程-11；未获得完整本机栈，未进入普通控制步，原因未定，不能当作粒子计数失败复现或拓扑已解决GPU的证据。native_open_probe_r3省去诊断定时堆栈采集（保留fatal handler），其余源码/实例相同，再验证。所有旧结果保留。CPU相关检查43通过、4子测试。

PARTY-005/011 关键链验证：native_open_probe_r3（无定时栈、无相机、source_v10/instance0_v2）通过完整重载和30原初态、10普通env控制步、原生DW Open及10步、checked Close/Open各30步，均无GPU错误。根坐标仍300，实际机器人世界位姿正常，因此不能把大根坐标本身认定为该链必现根因。该证据只证明新实例上的开关门链；新实例布局也有变化，未单独隔离拓扑对旧v1 GPU故障的因果效应，不声称全局GPU修好。executor_probe_r4继续相同v2的全部native抓放/清洁链，不加载相机，仍为私有motor探针，无自主成绩。

### PARTY-012：清洁/浸湿枚举全部系统，过早向原生cloth询问材料状态（2026-10-26）

PARTY-010同类边界检查扩展到material_actions：Covered/ContainedParticles的原生实现也先读system.n_particles，故不能在工具支持检查之前向cloth询问。removing分支原先先筛Covered，再调用eligible_systems支持守卫；Contains来源也先问所有系统。两个兼容材料+不兼容cloth的边界回归均复现（material_cloth_red.log，2失败8通过）。修复为清洁先走已有eligible_systems（其先supports_system），浸湿先核对工具支持再读Contains，不放宽任何原生清洁准备条件；相关检查36通过/4子测试。冻结v10正在运行的r4不改，后续v11验证原生清洁链。

### PARTY-013：Inside理想位姿保持晚于env内部评分，临时非有限居民状态使动作崩溃（2026-10-26）

executor_probe_r4/sourcev10/instance0_v2：三个盘子均放入成功；第三盘官方采样耗尽后使用已有verified_fillable_grid_fallback，最终Inside与2居民验证通过（约165.65秒）。随后首个杯子放入的env._post_step→task.step→原生谓词读plate_282姿态NaN报错；placement_transaction恢复，下一杯子仍能通过。无GPU700，不能归为模型选点失败，也不能据此证明fallback几何本身必然相交。

确认时序：环境内部4物理ticks之后先做任务评分，原有preserve_payload_poses仅在self._step返回/抛错finally恢复，晚于该读取；原生采样的sim.step_physics包装不会覆盖env.step内部tick。新增project_before_post_step上下文，仅在Inside的既有理想payload保持阶段，先恢复此前已验证的居民/新物品位姿，再调用原始env._post_step作观察/原生评分；保留原物理ticks、接触/重力、原始评分函数、失败回滚。provenance明示verified_residents_before_env_post_step_metrics_v1，源码v11另冻，不回写旧记录。顺序/异常清理边界检查及材料/抓取19通过；真实多物品链尚待回归，不将单元通过当物理稳定证据。

PARTY-012/013 r4完成：独立原生最好绑定7/14（3盘、2杯、2啤酒瓶），1杯在临时NaN读取失败回滚；其余6终态清洁及两次浸湿实际原生调用均复现cloth的抽象n_particles异常（Covered/Contains路径），与边界回归一致。native cloth抓取亦通过，未见GPU700。executor_probe_r5在同一instance0_v2上用已冻结v11验证两项修复，仍无模型。

PARTY-013 r5局部真实回归：同instance0_v2/v11，前三盘再次通过（原生采样物理步数23/79/78与r4相同），随后r4失败的第一个杯子已完成Inside及原居民验收，无NaN读取错误。其余餐具与清洁链尚在运行，不能提前把此局部通过写成14/14。

PARTY-013 r5进一步失败：三盘三杯均完成，瓶1也放入；瓶2抓取时plate_283在env内部评分读出NaN，后续抓取因奇异矩阵失败，未得到最终评分。说明只在Inside动作局部恢复早于评分仍不覆盖后续普通控制步，不能声称r5完整通过。新增中央RGBBackend._step同样在原始env._post_step前应用已有base/object anchor、已提交容器居民、carry projection；原来before/after控制步的理想状态保持不移除，物理tick和评分器不改。provenance版本ideal_motor_state_before_env_post_step_metrics_v2。

新增真实控制步边界回归以物理后瞬时NaN模拟已确认读取时序，旧冻结v11上明确失败（control_projection_red.log）；维护修复相关36检查通过（control_projection_green.log）。v12独立冻结，r6同instance0_v2先清洁后归位，并在浸水后关闭水源，仍为私有无RGB动作链验证。此执行器依赖显式理想姿态保持，不据通过宣称餐具自然动力学稳定或真实机器人低层操作成功；全链实际评分尚待。

PARTY-012/013 r6实际结果：两处vacuum、浴室water擦除、water/soap浸湿、厨房adhesive擦除均通过，支持守卫的原生回归已确认。toilet/countertop的stain拒绝准备条件；随后开DW再次GPU700，不能说全局GPU已修复。中央project v2尚未完成多物品链验证，模型仍0回合。

### PARTY-014：工具把原生洗衣规则的替代清洁剂清单当作11项同时必要条件（2026-10-26）

r6同一mop已真实Saturated(water/liquid_soap)且对应soak返回成功，stain仍不可清。冻结BDDL知识库mop/hand_towel的stain显式条件有11个Saturated清洁剂；ParticleModifier._parse_conditions将其逐项AND。原生WasherRule.cleaning_conditions来自同一知识库，同一11项明确用any，任一种溶剂满足即可；原任务只提供soap/bleach，不能满足11项AND。这是工具/知识库语义失配，不通过增加任务外10种药剂或放宽goal解决。

新增显式solvent_conditions.py协议native_washer_alternative_solvents_v1，仅当多项全部SATURATED且值集合严格等于该材料原生WasherRule清洁剂清单时替换为一项any函数；原生容量限制、其他显式/default条件与目标谓词不改。这是明示的工具条件语义修正，不能称未修改原生工具行为。真实mop前后对照：原逻辑干/仅水/水皂均false；修正后false/false/true，Saturated(soap)实际true，原12子条件变为替代溶剂+原生容量2项。native_solvent_condition_pass保存原生结果/源码/日志；首个最小夹具未推进物理步造成同tick依赖缓存旧值，失败已保留attempt_01，修正夹具在每次状态改变后推进原生一步。

PARTY-005 后续：r7/source_v13/instance0_v3在完全相同v2场景/TRO/原BDDL/布料拓扑下明示测试原生PGS配置，真实GPU动力学仍开。TGS在r6材料变化后再次GPU失效，PGS是否改善尚未验证，不宣称新配置解决崩溃。独立记录requested/实际PhysicsContext solver，旧配置和结果保留；两项独立问题分别有材料最小对照及GPU实际失败证据。provider只读模型目录HTTP200且列出gpt-6-astra，未发模型策略请求。

PARTY-005 r7结果：实际PhysicsContext配置PGS且30初态通过，但首次vacuum floor清除dust后即GPU700；PGS单独无效，保持失败证据，不作为修复宣称。particle_edit_rebuild_r1在相同v3/PGS/源码上，仅诊断Covered材料删除后用原生stop/play重建物理，再恢复删除后的完整native state，核对R1位姿误差与各有效物理/视觉材料粒子数相等，继而两次vacuum+20真实env步。此前失败与粒子组变化相邻，但尚未获得底层GPU索引根因；重建假设未验证，维护代码尚未接入该逻辑。

PARTY-005 粒子删除假设受限：particle_edit_rebuild_r1在toggle_vacuum（尚无Covered删除、rebuild记录为空）已经GPU700。因此删除不是必要触发条件，stop/play假设未实际执行/验证，未接入维护库。之前按时间相邻推断删除触发，不能作为已确认根因。PGS方案未证明更稳，下一回归回到TGS。

新调查变量是原生R1虚拟基座参考系：保存root=[300,300,300]，前6虚拟joint约[-292,-299,-300,0,0,2.375]抵消到真实屋内。原生scene loader确实会调用Robot.set_position_orientation更新world固定关节，不能把固定关节未更新当原因。显式holonomic_base_frame_at_start_pose_v1只在派生配置重定参考原点至同一已记录世界起始位姿、基座6关节归零，其他关节/所有非机器人状态不变；已有backend keep_still同样置初始速度零。CPU对冻结真场景前向运动学核对世界位置差9.16e-5m，旋转dot误差约1e-8（浮点），非base关节与非机器人状态逐项不变，base_frame_cpu_validation.json。数值条件是否与GPU故障相关尚待真实验证，不当确诊。source_v14/instance0_v4 TGS、原几何/TRO/BDDL/布料拓扑不变，r8继续真实初态/动作检查。

PARTY-014 初始化集成修正：r8的native_solvent_conditions.json repairs为空。原生物体能力在Scene初始化WasherRule.class registry之前解析，最小夹具在Env已建后加mop才掩盖该顺序。v2直接调用原生translate_bddl_washer_rule_to_og_washer_rule(get_knowledge_base().washer_rule)取得完全相同权威清单，不依赖晚初始化类变量；r9场景加载期修复记录已有实际对象，不再为空。不得把v1最小通过当完整集成通过。

PARTY-005 r8数值参考系调整仍在后续开DW时报GPU700，未能解决，正式候选v5退回原始根坐标/关节数据，保留v4失败与CPU等价检查。定位v3读回适配器还会在Serializable多对象/关节恢复中间刷新PhysX，并可能递归进入同步。显式native_gpu_particles_usd_readback_v4将原生Serializable.load_state外层与研究TRO整批恢复包入同步事务，只有最外层完成才刷新，增加同步重入守卫；保留所有原生状态数据/物理步，不增加额外仿真步，不重启物理，不变目标。该边界行为已从源码确认，但是否导致/解决GPU故障仍待实测。source_v15/instance0_v5/TGS/原v2几何和原机器人参考系下r9继续验证；不把已否定的PGS或参考系假设混称修复。

PARTY-005 r10空手对照仍在开DW时GPU700，持有拖把不是必要条件；清洁后独立评分6/14及原生after_cleaning_native_state.pt已保存（SHA a77207f139861bf12a3d02bab441f39bc6925f6c2b9deab0b76d7b5d60f886cb）。r11在新进程加载该纯私有检查点、stop/play并恢复同一native state，R1位姿误差0、清洁6目标仍真，开DW与归位链可推进，最终13/14，仅杯1放入奇异矩阵失败回滚；未见GPU崩溃。此为分段检查点诊断，不是原初态自主成功，也未隔离新进程与同进程重建各自效果。

### PARTY-015：原生状态重载的表示规范化被误判为物理状态变化（2026-10-26）

r12/source_v16同一连续回合试验材料编辑→开关门前native stop/play/load屏障v1，因原始序列化值不逐字相等而正确中止开门，未绕过校验。源码核查三类预期原生行为：ModifiedParticles._load_state只保存正计数（零键消失）；MacroParticleSystem._load_state根据存活particle_idns重算next ID计数器；MicroParticleInstancer使用Vt.QuathArray并在场景坐标转换中规范化四元数。r12的差异仅列出这些字段，不能等同粒子数或机器人位姿变化；后续闭门放置超预算及GPU故障仍保留，不认定该连续回合通过。

显式native_particle_edit_articulation_barrier_v2使用同一native snapshot深复制（防NumPy视图别名被play/reset改写），校验所有其他状态值、物理坐标/速度有限性与数值一致；仅接受原生正计数稀疏表示、经存活ID核验的分配器规范值、粒子四元数q/-q及两次半精度ULP范围。额外核对实际ParticleRemover各活跃材料准备条件前后不变，不读任务目标或替模型选动作。记录原生内部时钟/物理上下文可能重建，不宣称求解器缓存逐位保留。状态边界回归覆盖真实计数/位移/大旋转/错误ID与NaN拒绝，相关41检查通过、2子测试通过。r13/source_v17/instance0_v7完整原初态验证；私有脚本允许一次明确记录的失败放置恢复，不能删除失败历史或冒充模型自主性。


热饮维护source_v2自主配对终态（2026-10-26）：experiments/hot_drink_lh_repaired_pair_20261026，astra/low，开发301/seed0，同GPU0，源/场景/四基础skill/结束规则相同，78实际Python文件逐组匹配冻结源。无长程106预算动作（含2look）/11失败/6279步/4417.34秒；有长程96动作（含3look）/7失败/8201步/4885.66秒；均11/11高层目标、首次achieved接受、无续接/监督补finish/拒绝。两组调用对齐、独立语义重放、完整视频解码通过，失败图与最终四视图人工检查。长程update_plan5、remember2、recall0。动作少9.4%、失败少36.4%，但执行墙钟多10.6%、工具时间多25.3%、模拟步多30.6%，不可称全面提效或总体成功率提升。两组实际使用普通柜门新路由，环境操作不计任务提交；仅高层研究评分，非原生官方成功。部分恢复窗口已人工核实，窗口不重叠样例不等于全局可省动作成本。反向门候选1例为误认高柜后的检查/复位，未证明冗余；柜内小物选点和不可达导航仍是共同成本。机器人skill与运行冻结源未修改；先诊断设施，勿用重复guidance替代真实复现。旧sol模型/结束协议不同，不混合或作单一补丁归因。证据REPORT.md、manual_reviewed_results.json、runtime_and_scene_equivalence.json、两组failure_target_inventory.json/视频/配置及审查首页hot_drink_lh_repaired_pair_20261026_index.html。


### PARTY-016：原生粒子四元数写入错误交换分量（2026-10-26）

r13屏障v2拒绝的粒子旋转差异不是单纯半精度规范化。native_quaternion_layout_result.json三组实际USD类型测试证明Vt.QuathArray.FromNumpy接受xyzw，上游PhysxParticleInstancer.setter却先换成wxyz，单位旋转被写成180度X旋转。显式particle runtime v5仅修正该写入边界，空数组保持原生类型，getter/目标/容差不改。particle_quaternion_roundtrip_v2_result.json在真实原生water实例上三组修正写入最大误差0/0/0.00015849，native dump/load旋转误差均0，status passed。首次夹具误用不存在类名已保存native_particle_quaternion_attempt_01，不隐藏失败。PARTY-015所列前三种差异中，计数表示规范化仍成立；大旋转是此独立写入错误，不可用扩大容差接受。源码v18/实例v8冻结，同v7几何、TRO、原BDDL、拓扑、TGS和屏障v2；完整单回合物理链待r14，尚无自主模型回合。

PARTY-016 r14关键集成通过：30/30原生初态成立；六项清洁及释放工具成功。两次屏障均status passed/state_difference_count=0，实际清洁工具准备条件前后保留；仅dust/adhesive/stain的next ID按原生规则规范化。清洁后DW Open已通过，开始餐具链；完整14项终态及自主性仍待，不能提前写成全链通过。

PARTY-016 r14完整连续回合通过：同v18/v8原初态30/30，六项清洁、DW开门、全部8物品归位，原生最好绑定14/14；cup3首次放置奇异矩阵，恢复抓取/再放置通过，status passed_with_recovery，保留原失败及recovered_failures。全链无GPU700，768额外原生volume采样物理ticks单列，不冒充自主成功。

### PARTY-017：研究实例冻结起始点在严格导航网格外（2026-10-26）

model_on_r1/source_v18/instance_v8/astra-low实际自主回合首次navigate_to即navigation_invalid_start，原点[7.85164,1.31277]在静态侵蚀网格内为障碍，非动态家具掩膜造成。模型再尝试两处DW点均out_of_reach，首次blocked接受，无拒绝或续接；独立原生0/14，3动作/0控制步。该初态不可导航为设施失败，不能归为规划能力失败；不改变skill或用提示强迫重试。模型/执行器调用对齐、四帧五视图视频完整解码与哈希通过；无控制步不能声称路径/底盘高度审计通过。

私有CPU重放原导航失败网格，最近free仅0.164m；选择距原点0.263m、在已侵蚀网格上另有0.15m余量的[7.875,1.575]，同连通区域32504格。v9仅改冻结初始机器人XY，几何/TRO/BDDL/cloth/工具/skill不改，不在运行时恢复传送或放宽导航障碍。真实30初态、位姿和严格物理导航待probe_initial_navigation_v9验证；尚不能称benchmark准入。

PARTY-017 v9真实结果：起始30/30，记录的模型选中DW世界点严格导航通过，474运动+10稳定控制步，路径4.724m，终点误差6.54e-5m，无导航传送/放宽。脚本overall failed源于额外要求导航后全部initial谓词仍真：29/30，recycling_bin2的Open因物理推进变False。保留原failed及acceptance_scope.json，不改记为全场景稳定通过；导航边界通过与桶盖漂移分开记录，原目标允许任一bin，不新增目标/锁盖。model_on_r2同source_v18、新v9初态继续探索，准入与实际结果独立。

### PARTY-018：无RGB导航通过，但实际RGB回合首次导航第二步GPU失效（2026-10-26）

model_on_r2/source_v18/instance_v9原生RGB启动成功；首次模型navigate_to第1控制步已记录，第2步PhysX GPU artiPropagateImpulses2/artiPropagateVelocity error700，继而NoneType.view，服务退出。后续模型观察/恢复/finish连接拒绝，无接受的formal finish，独立终态evaluation缺失，不能伪填0/14正式成绩。保留run.failure=AttributeError及完整GPU日志，视频2帧/1已完成控制步完整解码、哈希和ledger检查通过；调用闭环审计失败属服务中断。

相同v9无相机私有导航484步通过，不能把该通过等同完整RGB设施准入；RGB和相机/标注初始化是新增对照变量，尚未确认根因。日志在首次导航开始附近有两cloth mesh运行时编辑不支持警告，不据时间相邻定因。full_rgb_restart_probe_v1候选在真实四RGB+旁观相机和初次观察后，使用既有严格native stop/play/load状态/准备条件校验，再进行3轮各10控制步+新观察；仅诊断假设，尚未接入维护源码或称修复。

PARTY-018 候选对照：full_rgb_restart_probe_v1在真实五相机/首帧后native state checked重建，3轮各10控制步+新RGB均通过，状态差异0；full_rgb_restart_navigation_probe_v2同样重建后重放r2模型选中世界点，479运动+10稳定+10后续步共499步、后续新RGB均通过。后者仍直接内部导航，不能冒充公开像素链路通过。source_v19仅五处相机启动开关/强制带原因状态校验/配置准入与展示delta，instance_v10只加native_state_checked_post_rgb_startup_v1；在首帧observe完成后一次执行，之后不重复，不重选像素/路线，记录native时钟重启。默认与旧实例不启用。冻结v19明确导入路径的相关回归14检查+2子测试通过，日志camera_barrier_v19_regression.log。full_rgb_public_navigation_probe_v3通过公开execute_visual复用r2原像素[.143,.396]验证中；根因仍只定位为初始化后物理上下文问题的候选修复，未获底层GPU确诊。

PARTY-018 v19/v10完整公开像素集成对照通过：full_rgb_public_navigation_probe_v3初始30/30，execute_visual复用r2原像素[.143,.396]，原生目标反投影与记录距离 0.000000000m；479运动+10稳定+10后续=499控制步，导航后新RGB和后续控制通过，状态重建差异0。此修订是实测有效的显式初始化缓解，GPU底层根因未证明；仍非模型自主成绩。model_on_r3同冻结v19/v10从原初态启动，不给诊断轨迹/检查点。


### TABLE-001：原始摆桌目标配对的笛卡尔积阻塞研究实例构造（2026-10-27）

用户当前只要求截图第4、5项。laying_restaurant_table_for_dinner / Beechwood_0_int / instance0 / seed0；原BDDL SHA 39a4c9217de340bca527beccb9931aa5b9bc61d502a5888738d0c34902cd4f75。原始7个目标因子规模[1,1,24,24,24,24,24]，共7,962,624完整绑定。真实ground_options路径在CPU 3秒超时并记录frozen/字面量重复展开堆栈；仅编译单条条件的0.0009秒结果不代表完整展开，原测试夹具domain错误和编译-only输出均保留，不混作有效red。证据remaining_four_20261027/diagnostics/。

维护goal_grounding.py新增显式MAS_FACTORED_RESEARCH_GOALS=1：仅当全正字面量且每因子选项等长时，以精确可索引惰性积保留所有组合/顺序/重复量，用各独立因子最佳绑定合成精确最佳部分分；否定或变长选项退回原穷举。原生条件树、任务目标、终止判定均保留。研究backend不请求未使用的人类归一化TaskMetric矩阵；_goal_options只记录本次最佳绑定，evaluation明确记录完整组合数和代表格式，初末态各自独立评分，不冒称完整矩阵已记录。默认及原公开协议不改变。先新增回归ImportError red；实际BDDL规模、随机真值对穷举的序列/分数一致、否定/变长回退、跨次评分不缓存及原有研究/状态屏障检查18通过、2子测试。冻结新源见table_buffet_runtime_20261027/source_manifest.json；真实重载/完整原生自主仍待。

### SUITE45-001：新建摆桌与自助餐实例在初态采样出现原生GPU700（2026-10-27，未解决）

两任务均instance0/seed0；摆桌场景Beechwood_0_int，GPU3；自助餐Wainscott_0_int，GPU2；原BDDL SHA分别39a4c9217de340bca527beccb9931aa5b9bc61d502a5888738d0c34902cd4f75、608a7925723bd3c555a554b01d10cf488c169d78f6ba72243d49253e5e6c9a38。旧证据remaining_four_20261027各construction.log/launch.json，GPU UUID与环境在launch；源码/原输入冻结。自助餐先有餐巾OnTop成功、橱柜盘子Inside false，再PhysX cuEventRecord/cuStreamWaitEvent error700；末端TorchDynamo RNG读取报错是失效CUDA上下文的后果，不能凭末端栈认定Dynamo根因。摆桌目标展开后亦出现重复原生GPU700，已按完整本轮路径匹配仅停止owned进程组，日志保留。两项模型调用均0；设施失败不计自主规划失败。

原地gdb附加被ptrace限制拒绝，未擅自改内核权限。新采样worker增加逐谓词begin/end/耗时与SIGUSR1主线程栈入口，验证全部对象分配后严格native stop/play/load屏障是否改善；该方法是候选，尚无根因或修复通过宣称。新协议保留所有原生状态校验/有限性，不放宽容差；采样后RGB首帧屏障单独版本化。新版两场仍保持原始目标（folded动作缺口保留）、真实GPU物理及CPU张量、原场景几何与选房记录。禁止仅以进程存活/无新日志宣布正常，须盯到初态验收、RGB/动作及独立评分；下一证据table_buffet_runtime_20261027/。

SUITE45-001 候选v2在sample_states入口dump_state失败：assign_objects阶段已stop，R1关节view未就绪，None.view；两场均未到候选重建，不当作GPU700再次复现或修复失败因果证据。table_buffet_sampling_v3_20261027只将hook移至原生_sample_all_conditions完成play及scene.reset后进入_sample_initial_conditions的边界；其余输入和冻结source不变，source为只读复用。严格状态相等性仍不放宽，真实结果待验。

### PARTY-019：真实RGB导航成功后Open触发GPU失效（2026-10-27）

model_on_r3/source_v19/instance_v10/astra-low：首次RGB后完整state/readiness屏障通过；实际模型先navigate_to左图[.148,.399]，472运动+10稳定步成功，随后选front[.517,.757]开DW。482步前native Open=false、joint约2.19e-7，setter后native Open=true、joint1.57079637；首个后续控制步完成，再推进GPU markActiveSlab/solveBlockUnified等非法访问，服务NoneType.view退出。run记录2动作、483完成控制步，无独立最终evaluation、无接受finish，不能写成0/14或规划失败。PARTY-018首步导航缓解在真实自主回合有效，但不能推成整个任务稳定。

不同于r14远处R1私有清洁→DW链，该回合R1确已导航至门前；开门时R1与展开门面接近/重叠是待查假设，未确认接触根因。现有材料屏障generation=0不重建；full_rgb_nav_open_probe_v4精确重放模型两个像素，增加开门前一次完整state/readiness强制重建候选，并在setter后首步前记录机器人/DW逐link AABB相交候选，仅作广相位诊断，不当碰撞证明。候选尚未加入维护默认或再次模型试跑。

PARTY-019 v4候选通过：同模型原两个像素，开门前独立强制完整state/readiness校验重建，状态差异0；Open操作+30后续控制步/新RGB通过，总542步，无GPU700。setter后广相位候选为R1 base_link/wheel_motor_link2与DW link2 AABB相交；不足以证明真实穿透/崩溃因果。原生30稳定步后joint=1.21955且Open=true，原生接触/关节动态仍实际推进，不宣称最终角度恰等于fully setter的1.5708。

显式source_v20/instance_v11只加native_state_checked_before_articulation_edit_v1：Open/Close前强制同一完整native state/readiness校验；保留后续setter、真实控制步、原目标与选点。初始RGB一次屏障仍启用。其他action与旧实例保留原材料generation门控。内部物理上下文/时钟重启明示；不是原生GPU根因修复宣称，也未确认AABB交叠是必要原因。冻结v20导入路径核对后17检查+2子测试通过，articulation_barrier_v20_regression.log。model_on_r4从全新同初态运行，skill/指令/预算不变。

SUITE45-001 v3：两场play/reset后的原生strict重建均通过、状态差异0，但摆桌第二次Inside和自助餐第四张餐巾OnTop仍GPU700，监视器已及时停止owned进程，失败保留。最小table_buffet_minimal_20261027中无R1四餐巾OnTop全过+100恢复；有R1三过一拒绝+100恢复，无GPU700；夹具未复现整屋故障、两组GPU不同，不推断R1单因素效果。v4保持相同输入/冻结source，只在每个native初态谓词前增加严格重建，验证连续采样上下文假设。fold执行缺口另确认原生Folded setter未实现，仅ClothPrim有native folded configuration生成/缓存，尚未接入动作，不删原目标。

PARTY-019 r4自主前缀通过：source_v20/instance_v11实际模型首次导航和Open均返回ok=true，before_native_open完整state/readiness校验passed，无GPU错误。模型自行remember并开始接近台面第一盘；这证明该边界在实际自主路径通过，未提前宣称整个任务完成或GPU全局根因解决。

### PARTY-020：局部原子链通过不等于长自主RGB回合稳定（2026-10-27）

model_on_r4/source_v20/instance_v11/astra-low，实际导航和Open通过、首次盘子plate_284由官方Inside体积采样42内部物理ticks后放入成功；随后plate_283抓取成功，第二盘返程导航在3023完成控制步、9预算动作时GPU solveBlockUnified/artiPropagateImpulses2 error700，服务AttributeError NoneType.view退出。最后R1=[9.07228565,-0.25799561,0.00531006]，不能把该位置和DW门接近直接当作碰撞根因。首帧和Open强制完整state/readiness屏障均已通过，证明这些局部缓解有效但不够全回合稳定。独立终态evaluation缺失、无接受finish；首次盘子工具Inside通过不能代替最终1/14成绩。

两个导航action_timeout（event-00027/35，capture.sim_step1204/1900）明确world_may_have_changed=true；模型随后成功抓取/放入首盘，原失败不删除，也不等同模型任务失败。当前仍用原屋保守导航图，首盘路径约9.6m，耗时不能直接作skill效率结论。

新增真实五RGB/原始初态5000控制步idle稳定性对照（v5），无任务setter/导航；另v6私有重放r4原9个动作/像素，严格检查相同对象与0.05m内投影，保留预期超时和终止条件。NVIDIA 5.1限制文档提出TGS/D6驱动速度迭代配置问题，本地USDObject默认32位置/1速度迭代；v6仅在诊断进程将速度迭代设0并记录实际值，属于待验证物理变量，未改维护默认或原技能，也未认定D6是R1根因。上游现安装脚本同样pin Isaac5.1，不支持简单归因装错版本。来源与边界见BACKEND_COMPATIBILITY.md。

SUITE45-001 后续证据：v4每谓词重建仍在第二Inside GPU700；buffet在第四谓词前因8个原生is_asleep差异被严格拒绝。可选v3_restore_sleep屏障显式调用原生sleep/wake恢复后仍完整比较；维护research_native仅接纳已知v2及v3协议，回归27通过、2子测试，未以单测冒称真实GPU稳定。v5自助餐第三餐巾GPU700。显式新场景restaurant_cafeteria与house_single_floor仍分别在首盘Inside、第二餐巾OnTop失败，不能把换场景称修复。

最小同GPU0近/远R1对照table_buffet_robot_frame_probe_20261027两组均4餐巾OnTop、盘/勺Inside和100恢复通过；远处机器人单因素不足以复现。整场CUDA_LAUNCH_BLOCKING=1在默认128 CPU线程下长期停于接触缓存；限制torch线程4后，真实接触缓存2500调用总9.1285秒、单次最大0.00856秒，但首盘Inside仍GPU700（table_dinner_thread4_debug_20261027）。同步CUDA/线程限制均不是GPU修复。两项模型实际调用仍0。新table_dinner_velocity0_debug_20261027保持同输入/源码/线程/同步配置，只将原生USD对象速度迭代默认1改0，记录实际属性，待GPU锁后诊断；NVIDIA Isaac5.1已知TGS/D6限制给出该候选依据，不认定是本次根因。所有失败原日志保留；无自主成绩和benchmark-ready宣称。

SUITE45-001 调度修订：原velocity0候选仅等GPU未启动，已保留superseded_before_simulator_launch记录。独立table_dinner_velocity0_20g_debug_20261027将缩小场景的显存准入24GiB调20GiB，取得GPU1锁后实际启动。新GPU UUID/准入与脚本哈希均记录，资源/GPU亦变化，不将结果写成严格同GPU因果对照；未停其他实验、未变GPU物理/张量设备/目标。尚待完整采样验证。

### PARTY-021：私有构造器默认视频节奏与实际模型Runner不同（2026-10-27）

确认配置漂移：RGBBackend默认MAS_VIDEO_RENDER_STRIDE=2，run_demo_trial配置为3；实际r4 backend.video_capture_policy及帧账本证实每3步更新，v5 idle/v6速度0私有诊断为每2步。先前“仅改速度迭代”的反事实表述不成立，不能用该局部通过推成实际模型配置稳定。已核对专属PID/worker路径，对v5/v6发送SIGTERM，保留全部部分证据与operator_abort.json及launch exit=-15，不当GPU失败或完成的稳定性测试。既有完整公开像素局部probe也属于stride2；r3实际导航、r4实际Open通过仍是独立正面证据，原自主GPU失败均保留。

新增v7诊断读取真实r4 render_stride/flushes/pitch并在native动作前核对camera_rig、video_capture_policy、物理/观察/输入hash字段；发现差异即中止。已验证真实stride2差异及错误BDDL哈希被拒绝。速度0重放显式租用与r4相同GPU3，直接getter记录R1 position32/velocity0；只保留该声明物理变量。新run_research_episode_v2.py允许显式GPU租约，旧launcher未覆盖，matched_probe_v7_manifest.json冻结相关源码/计划。5000步idle同样匹配实际stride3另跑。

额外修正posthoc review展示：崩溃记录缺少evaluation时旧build_review回落到“官方指标/独立失败”，不能据run.status推得终态评分。新展示明确终态缺失/独立完成未知，并在视频前展示video_capture_policy和原生屏障选项；不写入或重构任何原生evaluation。历史policy执行源不修改；review_build_v2.py与review_generator_manifest.json单独冻结，维护脚本同修。

SUITE45-001 velocity0_20g候选实测42对象含robot_r1原生速度迭代均0，首个Inside持续约122秒仍GPU700；监视器停止owned子进程，模型调用0。该变量不足以防止本故障。下一最小table_buffet_raw_physics_probe_20261027固定远处R1、无全局地板，比较餐巾原生放置后近处与构造停泊远处两组，各5000连续step_physics，保留每50步机器人关节/布料顶点有限性与范围；此前100次load_state+env.step不覆盖连续物理步进，不能当长稳态证据。

SUITE45-001 raw_physics最小对照两组均完成5000连续原生步、监测有限性均通过、无CUDA700。near组所有初始placement通过；far组有原生placement拒绝（总体placement_failed），不能称整夹具成功，但不改变物理步进无GPU700证据。连续远处R1/场外餐巾仍不足以在此夹具复现整屋故障，未更改构造重力或删对象。下一对照仅将私有初态use_trav_map设false，保留原始全部初态/目标复验及严格运行导航，显式标未完成可达性准入，只有构造通过才调度自主探索。

SUITE45-001 pre-predicate capture成功：table_dinner_presampling_capture_20261027保存真实首盘前完整原生scene/state与binding/model，按计划主动异常退出，没有执行Inside，raw supervisor construction_failed以diagnostic_outcome.json的captured_intentional_stop解释，不能算新GPU故障。实际首盘dnqekb、橱柜top_cabinet/lsyzkh scale[3.531,7.097,3.832]，此前小夹具iawoof/spojpj只是代表资产，未覆盖真实模型组合；快照用于后续缩小真实复现。

SUITE45-001 geometry_sampling两场仍GPU700：buffet两个OnTop接受后第三失败；dinner首个Inside约250秒后失败。kwargs实际use_trav_map=false有逐谓词证据，因此构造地图筛选不是充分修法；两场模型调用仍0。下一显式PGS候选只改原生solver_type配置及spec记录，保留GPU动力学、CPU张量、原始全部初态/目标复验与严格运行导航，文档依据同NVIDIA5.1 D6条目，尚未证明根因。另捕获原真实scene中仅R1+橱柜+首盘+4餐巾的7对象子集，在GPU2重载精确scale/native state/cloth topology执行同Inside；未取得结果前不声称最小复现已成立。

### PARTY-022：低物体理想抓取保留大偏移，底盘净空不能代替载荷运输验收（2026-10-27）

确认代码行为：no-demo ControlledCarry._ideal_grasp仅将物体原世界Z提升0.18m，保存其相对底盘的位置；_carry_follow在导航时维持该偏移。r4第二盘plate_283从地面抓取，结合carry原位置及抓取前后受锚定底盘记录，CPU推算其XY相对偏移1.22797m、末个完成步根位置约[9.07789,-1.48595,0.19190]。不是原生直接测得的碰撞状态；误差范围依赖短暂稳定/抓取期间底盘锚定，不能当GPU根因。资料payload_transport_geometry_analysis.json保存公式、输入与约束，视觉末帧与低位运输一致。

现导航仅用原始静态侵蚀图及0.38m动态底盘余量，不能据此声称1.23m前伸载荷的扫掠体已验证。普通环境碰撞/重力仍启用，pose projection不是实体手部控制。该几何/接口问题需要独立载荷运输验收；不通过增加长程skill提示来遮盖，也未在本次诊断中改抓取姿态或碰撞规则。速度0配置是否缓解原生崩溃是另一变量，不能据通过宣称载荷规划已正确。

r14私有14/14脚本直接调用GT motor helper，native_navigation_distance=0；它验证原初态/材料条件/单件放置及评分一致性，不验证RGB发现、公开reach约束、跨房间实际运输。未来benchmark准入需同时通过公开RGB floor-grasp→loaded-turn→obstacle-adjacent transport→destination placement链，并记录实际载荷与地图几何范围；不能把直接helper十四目标成立当整任务公开链可行。

SUITE45-001 PGS候选仍两场GPU700：buffet前三OnTop接受、第四失败；dinner首Inside约290秒后失败；native记录确为PGS，模型调用0。7对象真实资产/scale/native state/cloth topology私有重放TGS Inside通过；同子集增加v3 strict state/readiness重建仍通过，故该边界单独不足以在小夹具复现。最初子集r1漏传必须的topology manifest SHA导致输入校验拒绝，r2补真实哈希，未放宽校验；该r1是harness preflight失败，不是GPU复现。当前84对象saved-scene重放保留同seed0/Scene/原生Inside/strict重建，只恢复全部对象；原采样前RNG未保存，不能冒称逐随机数精确重放。

SUITE45-001 背景子集定位：从摆桌真实presampling saved scene中保留静态背景+核心对象（61 objects）及真实首个Inside，严格native state重建/睡眠恢复、TGS、CPU张量、GPU动力学；不改采样代码。table_dinner_background_subset_probe_20261027在同Inside路径约243秒复现CUDA700，故不需要完整84对象即可触发，静态背景/碰撞体与核心组合共同构成必要候选。任务对象子集仍加载中，若其通过则进一步指向静态背景；若失败则保留双方证据。模型调用仍0。

### PARTY-023：零速度求解器不能消除第二盘运输时的 GPU 700（2026-10-27）

- **任务/实例/协议**：`clean_your_house_after_a_wild_party`，`instance0_v11`，`execution_source_v20`；私有 `full_rgb_prefix_zero_velocity_probe_v7`，与 `model_on_r4` 使用相同 render stride=3、四相机、输入像素、GPU3、动作序列和物理配置，仅将 USDObject 默认 velocity iteration 从 1 改为 0。
- **复现**：前 8 个动作逐项通过/复现原始预期超时；第 9 个“运送第二只盘子返回”在 3023 控制步失败。`result.json` 的 traceback 表面为机器人 `ArticulationView.get_joint_positions().view(...)` 的 `NoneType`，同一时刻 launcher 日志先记录 `GPU solveBlockUnified`、`GPU artiPropagateImpulses2`、`GPU artiPropagateVelocity` kernel launch 700，随后 CUDA context manager 停止并重建、ArticulationView 未初始化、renderer semaphore 700。
- **对照**：同配置默认 velocity iteration=1 的空闲稳定性探针完成 5000 控制步且无错误。速度迭代 0 的长程回放仍在同一步失败，因此该候选不是修复，不能创建正式 v21 或启动新的模型回合。
- **分类/结论**：已确认是原生 PhysX GPU/上下文失效导致的执行器后置访问错误；`NoneType.view` 是后果，不是独立根因。仍未确认触发 kernel 的具体载荷、粒子系统或 articulation 输入。
- **变更/验证**：没有修改生产源码；保留完整结果、录像帧、启动器日志和 5000 步 idle 结果。下一步只允许做有明确隔离变量的原生诊断；不得用 `keep_still` 空操作或跳过物理来伪造 benchmark 成功。

### PARTY-024：中心载荷运输候选越过 r4 GPU 崩溃点（2026-10-27）

- **变量**：`execution_source_v21` / `instance0_v12` 将 controlled carry 的非 demo 载荷位姿从保留原物体位置上方约 1.23m 前偏移改为机器人底盘上方 0.65m；任务、输入、像素动作、GPU 和其他运行设置不变。
- **证据**：`carry_center_candidate_probe` 完成原 r4 的 9 个公开动作及 1000 控制步尾测，越过 r4/零速度回放在 3023 步的 GPU 700；没有 GPU 错误。该私有回放只验证设施前缀，不是模型分数。
- **结论**：载荷扫掠/运输几何是此前长距离 GPU 崩溃的强候选原因；中心位姿修复进入 v21，且已在后续真实模型回合中验证第一只盘子运输和放置成功。

### PARTY-025：ClothPrim 边界属性缺失与启动相机高度副作用（2026-10-27）

- `model_on_r5` 使用 v21 在约 2977 步遇到 `ClothPrim` 无 `visual_boundary_points_world`。v22 增加 AABB 回退，但错误地把回退用于启动相机高度，导致相机 z=1235m、四向全是天空；该版本作废。
- v23 将回退限制在携带布料的导航自边界，恢复原相机高度。v23 真实模型验证了正确视野、载荷运输和第一放置，但后续放置再次触发原生 GPU 700。

### PARTY-026：放置前 barrier 通过后水粒子长时发散（2026-10-27）

- **版本/复现**：`model_on_r9` 使用 v25/v16；约 10430 控制步、59 动作，在模型把啤酒瓶放入回收桶时失败。v25 的放置入口 barrier 已允许仅 `is_asleep` 的瞬态等价并严格比较其他状态；模型此前已越过 v23 的 2648 放置崩溃并运行到 10k 步。
- **确认链**：`simulator.log` 在 CUDA 700 前记录水粒子 instancer 多个实例 `scale inf`、极大世界坐标，随后 CUDA 非法地址、ArticulationView 未初始化；traceback 的 `NoneType.view` 是后果。没有把此回合标为策略 0/14。
- **结论/限制**：中心载荷与放置状态边界有效，但 native GPU water 粒子长时数值稳定性仍未通过；不能继续以任意 guard 或跳过物理伪造完整成功。保留 r9 全部视频、事件、日志与独立终态缺失证据。

### PARTY-027：减少地面毛巾 settling 仍不能阻止水粒子发散（2026-10-27）

- **版本/变量**：`execution_source_v26` / `instance0_v17`，继承 v25；仅对 hand_towel/cloth 在 floor/ground 上的 `place_on_top` 将 settling 从最多 50 个原生物理步降为 1 步，仍执行选面、位姿、support 与 OnTop 检查。
- **结果**：`model_on_r10` 运行到 6503 控制步、37 动作；未取得终态评分。随后在打开柜门前的原生 barrier 发现 `mop_272/is_asleep` 差异并退出；同一时间 `simulator.log` 已出现 water instancer `scale inf`。因此减少地面毛巾 settling 不是充分修复，不能进入正式 benchmark 配置。
- **结论**：水粒子非有限状态会在更早的长程放置/状态重建链中出现；当前证据不足以把触发点归因于某个单独模型动作。保留完整日志、视频和 barrier 记录，停止继续盲跑模型。

SUITE45-001 静态背景二分定位：真实摆桌保存场景首Inside。任务对象30个通过；背景61个（静态31+任务核心）失败。背景静态先拆为half1 27个/half2 27个：half1失败、half2通过；half1再拆part1 13个（餐桌、天花板、部分门）通过、part2 14个（两门、7窗、4地面、冰箱）单独通过。part1+part2联合即原half1失败，故当前最小化结论是14对象联合的组合效应（或其与任务核心的交互），没有证据指向单个物体。两半均复用真实native state、strict sleep-restoring rebuild、TGS、CPU tensors、GPU dynamics及同Inside调用；模型调用0。

### PARTY-028：有限水粒子 watchdog 的前两处挂点未被实际推进调用（2026-10-27）

- v27 在 `sim.step_physics`，v28 在 `_non_physics_step`，v29 在 `sim.step`，v30 在 `_sim_context.step` 增加有限水粒子检查；独立 20 步 smoke 均正常，但 `native_particle_runtime.json` 的 `finite_guard_checks` 仍为 0。
- 这证明这些对象方法不是当前环境实际物理推进的有效挂点，不能把 watchdog 当作已验证修复。历史回合未被改变；v27 自有长回合已确认停止并保留 operator 状态。
- 后续若继续，必须先在有合法 GPU 租约时用 introspection smoke 找到真实推进入口，再验证计数和恢复；不启动新的长模型回合。

SUITE45-001 后续变量：CCD关闭、静态触发对象强制native sleep均仍在完整背景首Inside复现CUDA700；深拷贝dump_state也仍GPU700（且setter false），排除单纯快照别名。门窗/冰箱碰撞禁用仍GPU700；静态凸包碰撞近似导致读取橱柜DOF backend失败，未纳入修复。CPU dynamics对照被维护native particle runtime明确拒绝（requires GPU dynamics），未冒称CPU成功。第4/5模型调用仍0。

SUITE45-001 Fabric对照：维护粒子运行时默认设置OG_DISABLE_FABRIC=1；诊断中移除此变量让Fabric输出恢复，保持GPU dynamics/CPU tensors/完整背景首Inside不变，仍在约100秒触发SynchronizeStreams/CUDA700。故Fabric开关不是充分修复。

### PARTY-029：确认环境构造后 attach 成功但旧 PhysX callback 不触发（2026-10-27）

- `guard_introspect_v34b` 证明 v34 的 attach 函数和 `sim._finite_guard_attached_after_env` 均为真，但 2 个控制步后 `finite_guard_checks=0`。
- 根因进一步缩小为：Environment 构造后 simulator 使用了新的 PhysX interface callback subscription；旧 `_on_post_physics_step` 方法包装没有被实际 callback 调用。
- v35 已改为在环境构造后直接对当前 `sim._physics_context._physx_interface` 注册 watchdog subscription。由于 GPU3 随后被其他 `run_probes.py` 抢先持有租约，v35 smoke 尚未取得合法资源验证；没有启动新的模型回合。

SUITE45-001 archived particle runtime v3对照：同真实背景、TGS、GPU dynamics、CPU tensors，仅将v5替换为wild_party_runtime_20261026/simulator_particle_candidate.py；首个Inside仍SynchronizeStreams/CUDA700。v5新增restore事务不是充分根因。

### PARTY-030：当前 PhysX subscription smoke 仍不触发检查（2026-10-27）

- v35 通过 `subscribe_physics_on_step_events(callback, False, 0)` 修正了 API 参数，smoke 可以启动，但实际 worker 在 subscription 处理后没有产生有效计数。
- v36 的 native runtime 仍记录 `finite_guard_checks=0`；20 步 smoke 本身通过，说明 subscription 仍未接到当前实际物理推进或回调时序不允许读取粒子。
- 当前 watchdog 不能用于真实模型，保留 v36 作为未验证候选；不再继续增加挂点猜测，需下一轮直接在 native callback registration/state 中做最小 introspection。

### PARTY-031：active system watchdog 已接入但未捕获 waterInstancer 非有限状态（2026-10-27）

- v37 真实模型运行到 8306 控制步，`finite_guard_checks=31439`、`finite_guard_recoveries=0`；日志仍出现 water instancer `scale=inf`，随后 barrier/ArticulationView 失败。说明 watchdog 已进入真实 post-physics 订阅，但 API 读取的 active system 仍没有暴露发散实例，不能恢复。
- v38 扩展扫描 `scene.active_systems`，smoke 通过；真实 r13 在 2164 步早期 GPU kernel 700 失败，未取得水粒子 recovery，不能算修复。
- 当前所有恢复候选均保留；未把完整任务标记成功或伪造终态分数。

### PARTY-032：water 仅存在于 USD PointInstancer，直接 USD guard 候选尚未完成验证（2026-10-27）

- v39 inventory 发现 Python system registry 只有 adhesive/bleaching，未暴露 water；v40 安全处理 `MacroVisualParticleSystem.n_particles` 的 `NotImplementedError`。
- `water_usd_inventory_v40` 只读确认 `/World/scene_0/water/waterInstancer0/instancer` 是 PointInstancer，493 个 positions/orientations/scales/velocities 属性均可读且有限。
- v41 增加直接 USD PointInstancer 最近有限状态恢复；smoke 通过。真实 r14 在 1852 步提前发生 GPU/ArticulationView 失效，尚未到 water inf 阶段，因此 USD 恢复没有取得因果验证。
- 结论：水粒子状态入口已确认在 USD PointInstancer；完整任务仍未跑通，v41 不能标为修复成功。

### PARTY-033：v41 真实模型越过设施崩溃点，但导航超时结束（2026-10-27）

- `model_on_r15` 使用 `execution_source_v41` / `instance0_v32`，真实 RGB 模型运行到 3,292 个控制步、13 个动作；没有出现 water PointInstancer 非有限恢复，也没有 CUDA 700 或 `ArticulationView` 崩溃。
- 模型确认洗碗机打开并成功放入 1 个盘子；随后在导航到水槽的动作上返回 `action_timeout`，模型以 `blocked` 结束。独立终态评分缺失，`task_success=false`，不能计为 0/14 或完整成功。
- 该回合说明 v41 至少没有复现 r14 的早期 GPU 设施崩溃，但没有验证长时水粒子稳定性、完整餐具/啤酒瓶运输或所有清洁目标；因此 v41 仍不能标为 benchmark 通过。

### PARTY-034：延长导航预算后水粒子 GPU 失败仍在首个水槽操作附近出现（2026-10-27）

- v42 将每动作控制步上限从隐式 700 改为显式参数，并设为 1800；`model_on_r16` 的水槽路线实际使用 868 步并成功到达，证明此前 r15 的 `action_timeout` 是预算不足。该回合随后在水槽 `toggle_on` 处出现 CUDA700/`ArticulationView=None`。
- v43 扩展有限状态守护器，缓存并检查 native particle positions、orientations、scales、velocities；`model_on_r17` 运行到 3515 步，仍在水槽 `toggle_on` 首个推进时 CUDA700，守护检查 13306 次、恢复 0 次。日志没有显示守护器可见的非有限 scale，说明 GPU kernel 可能在状态可读回前失效。
- v44 将水源每步生成上限降为 1；`model_on_r18` 在开启水龙头前的携带导航阶段即发生 `navigation_invalid_start` 后 CUDA700。v45 将导航速度设为 1.2m/s；`model_on_r19` 仍在水槽 `toggle_on` 处 CUDA700。两者均无独立终态评分。
- 私有 `probe_toggle_water_v45` 不调用模型，仅构造同一 native 场景并准备水槽 toggle；初始化/settling 阶段约 217 秒即触发 PhysX GPU700，守护检查 68 次、恢复 0 次。当前阻塞属于 native GPU water 场景设施，不能归因于长程 skill。

### PARTY-035：水槽初始化探针在第二张 GPU 上同样复现（2026-10-27）

- `probe_toggle_water_v45_gpu1` 使用与 GPU3 完全相同的 v45、实例、native GPU/TGS/CPU-tensor 配置，仅将排他 GPU 改为 GPU1。
- 它同样在模型未调用、守护检查 68 次的场景初始化/settling 阶段触发 GPU `artiPropagateVelocity` / `solveBlockUnified` kernel 700；没有 guard recovery，也没有生成独立结果文件，因为模拟器在关闭阶段先失效。
- 因此当前水粒子 GPU 失败不是 GPU3 特有状态，也不是模型动作选择造成；在修复 native water 初始化/PhysX 稳定性前，不应继续进行长程 skill 比较。

### PARTY-036：state-restore water 重写跨过 GPU 崩溃，但完整模型仍受姿态/导航边界影响（2026-10-28）

- v49 在每次 native `load_state` 后重写最近有限 water USD 状态，并在 render 前同步回 Fabric；私有 toggle 探针可成功完成水槽开关，真实 v50 运行到 3985 步后才在水源开启后的后续动作触发 GPU700。
- v51 将水源生成上限设为 0（保留真实 toggle 与已有水粒子），真实模型运行到 11,449 步、60 个动作，未出现 CUDA700；模型完成水槽开关、部分餐具与清洁链，最后因 `broom_274/is_asleep` 的 native barrier 差异被拒绝。
- v52 将 `/is_asleep` 瞬态等价扩展到所有 articulation edit。真实回合运行到 9,574 步、74 个动作，仍无 GPU700；完成水槽开关、两处除尘及多项搬运后，回收桶移动/不可达恢复使机器人末端四元数变为 NaN，后续抓取以 AssertionError 结束。
- v53 增加有限姿态的底盘 link 回退；另一条模型轨迹在 2,066 步发生 GPU articulation kernel 700，未证明该回退充分。当前最佳设施候选为 v52 的 state-restore water 修复 + source limit 0 +全局 sleep 等价，但完整独立终态评分仍缺失。

### PARTY-037：回收桶 settling 窗口确认，但轨迹相关 GPU 失败仍存在（2026-10-28）

- v54 将 `recycling_bin` 的 place_on_top settling 从最多 50+10 步降为 1+1 步，针对 v52 在桶放置后 NaN 的时间窗口；另一条真实模型轨迹仍在 2067 步触发 GPU articulation failure，未取得终态评分。
- 因此回收桶 settling 是 v52 NaN 的明确风险因素，但不是当前所有轨迹 GPU 失败的充分根因。v52 仍是最长真实回合（9574 步、74 动作、无 CUDA700 直到机器人姿态 NaN）。

## PARTY-038：v55 回收箱放置后禁用碰撞候选（model_on_r26）

v55 从 v52 冻结源码派生，在回收箱放置通过官方体积检查并完成稳定后，对已放置回收箱调用 `disable_collisions()`，试图避免后续动态碰撞向机器人注入姿态冲击；水源仍限制为 0，有限值守卫和 `/is_asleep` 等价规则保持不变。GPU1 上真实模型启动成功，首个 `navigate_to` 实际到达：483 控制步，路径 4.72 m，最终位置误差约 6.5e-5 m。模型因等待该动作超过数分钟而自行调用 `finish(blocked)`，没有继续打开洗碗机或触发回收箱候选。独立终态评估为 `task_success=false`、`goal_satisfaction_fraction=0.0`、14 项目标全 false；不是模拟器崩溃，也不是完整任务成功。该结果显示当前模型/执行器的动作等待策略会把一个最终返回的长导航误判为阻塞，v55 的碰撞候选尚未被有效测试。

## PARTY-039：v56 真实回合推进到首个盘子运输后 GPU700（model_on_r27）

v56 从 v55 派生，额外让 `online_backend` 读取 `MAS_NAV_WALK_SPEED`；该路径未被本回合实际导航使用，GT follower 仍报告 0.5 m/s。模型没有重复 v55 的早期 blocked：首次导航 483 步后继续打开洗碗机，确认抓取第一只盘子，并在持盘导航返回洗碗机时执行到约 2100 控制步。随后原生 PhysX 触发 CUDA error 700，`robot._articulation_view` 失效为 `None`，最终暴露为 `AttributeError: NoneType has no attribute view`。独立终态没有有效评分（任务未完成，模型在运行时故障后 blocked）；回收箱碰撞禁用候选仍未触发。该回合证明 v55 的模型等待问题可绕过，但首个载荷运输的 GPU/articulation 稳定性仍未解决。

## PARTY-040：v60 持物跳过 PhysX 候选（model_on_r31）

v60 在 v59 的 link 级碰撞修复上增加 `MAS_CARRY_SKIP_PHYSICS=1`：ideal carry 期间仍投影基座、载荷和相对位姿，但跳过原生 `env.step`，释放/放置后恢复正常物理。真实模型回合运行 18,800 控制步、111 次动作，无 CUDA700；独立终态为 `task_success=false`、`goal_satisfaction_fraction=0.5`。完成证据包括两瓶官方回收、厨房地面黏污、客厅地面与茶几灰尘、一盘和一杯入洗碗机；剩余两盘两杯、浴室水与马桶污渍、厨房台面污渍未完成。模型在动作预算剩余 9 步时 blocked。该回合末尾另有连续视频记录失败：跳过物理分支只按稀疏间隔写帧，导致 `every_env_step_recorded=false`；任务独立评估仍有效，但视频验收不通过。该候选证明持物期间 native solve 是 GPU700 的主要触发窗口，同时暴露完整视频协议需同步修正。

## PARTY-041：v61 完整视频/扩大动作预算回合（model_on_r32）

v61 保留 v60 的持物跳过 PhysX 与 link 碰撞候选，改为每个控制步写视频帧，并将 launcher 的动作上限提高到 200。视频验收通过：`every_env_step_recorded=true`、编码器退出码 0、视频约 50 MB。模型仅执行 19 个动作后异常结束，没有正式 finish；独立评估 `task_success=false`、`goal_satisfaction_fraction=0.0`，因此没有新增任务完成证据。此前两次启动失败分别是 CLI 不支持 `--model-max-turns` 和残留 proxy 日志，已修复后重新启动。v60 仍是当前最佳真实回合（0.5 goal fraction）。

## PARTY-042：v62 放宽官方 Inside 采样预算回退，但模型提前中止（model_on_r33）

v62 从 v61 派生，把 `_checked_place_inside` 中 `sampling_budget_exhausted` 的确定性 fillable-grid/repack 回退从 `demo_motion` 专用路径扩展到所有受管模型回合。回退只尝试选定容器的 fillable link，并用未修改的 `Inside.get_value` 与 settling 后置条件验收，不写入谓词；源码通过 `python3 -m py_compile`。真实回合 `model_on_r33` 无 CUDA700，视频通过，但模型在首次导航并开启洗碗机后无正式 finish，被监督器 abort，独立终态 `task_success=false`、`goal_satisfaction_fraction=0.0`，动作未到达盘子放置，因此回退尚未得到真实模型触发验证。v60 仍是当前最佳部分回合，不能据此宣称完整任务成功或开始 skill 比较。

## PARTY-043：v62 第二次回合确认模型早期退出方差（model_on_r34）

同一 v62 源码的第二次真实模型回合只完成 initialize，随后无正式 finish 被监督器 abort，独立终态 0/14、0 控制步；视频协议通过，模拟器没有 CUDA700。该结果与 r33 的“开门后退出”共同表明 provider/model 早期退出具有明显方差，不能作为 Inside 回退有效性证据。下一步需用私有 staged plate probe 验证回退，或等待模型真正进入放置动作后再进行独立评估。

## PARTY-044：v62 私有原生链验证三盘三杯两瓶与 Inside 回退（probe_v62_clean_first）

在不调用模型、无 RGB 选点、无跨房间导航的私有对象句柄探针中，v62 完成三盘、三杯和两瓶的放置链；独立 native evaluator `task_success=true`、目标满足比例 1.0，但 `official_submission_eligible=false`。第二、第三盘及三杯的记录包含 `official_sampler_budget_to_verified_fallback`，随后 `verified_fillable_grid_fallback` 和 50 步 settling 提交成功；总容积采样物理步 1621。该结果确认确定性回退不改变官方谓词语义并能处理已放置内容，但不计为模型 benchmark 成功。模型回合仍需真正到达放置动作后才能验证公开 RGB 执行链。

## PARTY-045：v35 medium reasoning 长回合达到 10/14（model_on_r35）

v35 使用 v62 执行器、`gpt-6-astra` medium reasoning 和相同研究实例。模型完成真实 RGB 导航、三盘放入洗碗机（公开事件均由 `verified_fillable_grid_fallback` 验收）、多项清洁与容器操作；运行到 20,000 控制步附近，连续导航 `action_timeout` 后正式 blocked。独立 native 评估为 `goal_satisfaction_fraction=0.7142857142857143`（10/14）、`task_success=false`、`official_submission_eligible=false`；视频通过且无 CUDA700。该回合是当前最佳公开模型结果，证明 v62 回退已进入模型 benchmark 链，但剩余四项目标和完整任务成功仍未解决。

## PARTY-046：r36 延长预算后在 11.2k 步复现 CUDA700（model_on_r36）

r36 使用 v62、medium reasoning、30,000 模拟步和 250 动作，避免 v35 在 20,000 步预算处的 action_timeout。模型推进到约 11,200 步，完成两盘、一杯及多项清洁；随后在导航中触发 CUDA error 700，`ArticulationView` 变为 `None`，运行时失败且没有独立终态评分。视频通过。该结果把“预算不足”和“长回合原生 GPU 资源/轨迹故障”区分开来；v35 仍是当前最佳公开模型回合（10/14）。

## PARTY-047：r37 stride3 长回合无 CUDA700，但导航超时为 9/14（model_on_r37）

r37 使用 v62、medium reasoning、30,000 控制步、250 动作和 `MAS_VIDEO_RENDER_STRIDE=3`。模型完整运行至长回合预算附近，视频协议通过，未出现 CUDA700；独立 native 评估为 `goal_satisfaction_fraction=0.6428571428571429`（9/14）、`task_success=false`、`official_submission_eligible=false`。最终因连续 GT follower `action_timeout` 正式 blocked。该回合证明 stride3 候选消除了 r36 的约 11.2k 步 CUDA700，但长程导航成本降低了任务完成率；r35 的 10/14 仍是当前最佳公开模型成绩。

## PARTY-048：r38 确认 1.2m/s 导航速度生效，但 4.4k 步复现 CUDA700（model_on_r38）

v63 只修改 GT follower 速度读取逻辑，使 `MAS_NAV_WALK_SPEED=1.2` 真正生效。公开导航记录显示首段 4.72m 使用 318 控制步，速度为 1.2m/s；模型完成两盘及部分工具链后，在约 4.4k 步再次触发 CUDA error 700，`ArticulationView=None`，无独立终态评分，视频通过。速度修复有效降低导航预算，但不是 native GPU 故障的充分修复。r35 仍是最佳公开模型成绩（10/14），r37 是最长无 CUDA 长回合（9/14）。

## PARTY-049 — v64/r39 reaches 8/14 then singular-matrix failure at bathroom access

- **Observed:** `model_on_r39` (v64, medium, stride 3, 30k/250) completed 8 public goal actions with official postconditions and ran 8,355 control steps / 106 actions. Video validation passed and no CUDA 700 occurred.
- **Failure:** After repeated bathroom `out_of_reach` and `navigation_unreachable` recovery attempts, the controller emitted `AssertionError: Some matrices are singular and cannot be decomposed`; exit code 1, no formal finish and no independent terminal score.
- **Interpretation:** v64 layered navigation speed and v62 verified Inside fallback support a materially longer stable chain, but bathroom geometry/reachability still exposes a simulator/controller singular-matrix failure. Public 8/14 evidence is not an independent benchmark score.

## PARTY-050 — v65/r42 stable long chain, cloth OnTop diagnostic crash

- **Observed:** r42 with behavior environment, layered speeds (empty 1.2 m/s, held 0.5 m/s), v62 Inside fallback, stride 3. Reached 15,081 control steps / 101 actions; video passed, no CUDA700. Public action evidence: three plates, one cup, one bottle, living vacuum, kitchen adhesive and counter stain.
- **Failure:** Model staged the absorbent towel on a recycling-bin lid, then grasped the bin. OmniGibson `OnTop._get_value` raised `ValueError: Cannot detect if an object is on top of a cloth object.`; controller exit 1, no independent terminal score.
- **Repair:** v66 wraps this diagnostic-only relation query in `supported_child_on_top` so the unsupported cloth target falls through to the reverse relation check. This preserves official relation semantics and converts the crash into recoverable carry logic.

## PARTY-051 — v66/r43 CUDA700 in first cup Inside sampler

- **Observed:** r43/v66 reached 11,261 control steps / 79 actions with three plates, two kitchen cleanups and two living-room cleanups evidenced; video passed and cloth OnTop crash did not recur.
- **Failure:** First cup `place_inside` entered `official_Inside_volume_sampler`, then simulator logged repeated CUDA 700 illegal-address errors and the controller returned `AttributeError: 'NoneType' object has no attribute 'view'`.
- **Repair:** v67 adds `MAS_INSIDE_DETERMINISTIC_FIRST=1` for managed model runs. It tries the bounded fillable-grid pose first, accepts only the unmodified official `Inside` predicate after settling, and preserves the existing official sampler fallback for cases where the grid cannot be verified.

## PARTY-052 — v67/r44 deterministic Inside passed, surface settling still CUDA700

- **Observed:** r44/v67 successfully used `verified_fillable_grid_deterministic_first` with zero sampling physics steps for the first plate. It later reached 1,882 simulation steps and failed while staging a towel with `place_on_top`; simulator logged CUDA 700 and controller returned `NoneType.view`.
- **Repair:** v68 adds `MAS_PLACEMENT_DETERMINISTIC_FIRST=1` for managed model episodes. Selected-surface placement now keeps the geometrically verified pose and performs zero native settling steps, while retaining selected-surface support checks and official `OnTop` verification.
