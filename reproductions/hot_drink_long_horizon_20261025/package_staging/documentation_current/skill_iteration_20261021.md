# Robot skill 动态优化：杂货完整回合的动作开销

## 基线与结论

基线为 `../experiments/groceries_v2_robust_gpt61sol_low_20261021_r1`：`carrying_in_groceries` / `house_double_floor_lower` / 301 / seed 0，gpt-6.1-sol / low，规划 skill 开启；双柜任选、袋高 1.2、显式导航。105 次 act/look 后独立 12/12，协议审计通过。这是开发实例上的一次成功，不是最短解。

存在可以优化的动作链，但不能直接把导航、失败动作和终态检查全部删掉。49 次导航包含跨房间搬运和合法的视角恢复；3 次 look 改变实际朝向，不仅是获取四路图像；最后一次后退用于解决第二只袋子位置的不确定性。18 次失败操作也包含首次暴露距离限制的信息，不能直接宣称可以从 105 减到 87。

本轮只修改 policy 的 skill 文本，不改任务、场景、harness、执行器、评分、模型或预算。**候选 v1 完整回合为 11/12、120 次动作，未证明动作节省；以下修改动机仍是优化假设。**

## 日志中的候选开销

动作编号按 `run/events.jsonl` 中 act/look 调用顺序，从 1 开始。`update_plan` 等非运动调用另计。

| 范围 | 实际发生 | 判断与优化方向 |
| --- | --- | --- |
| 1–19 | 首次抓袋前用了 19 次操作：13 次导航、5 次 open、1 次 look | 包含必要探索和开门。检查“必须先空手找目的地”的过强规则；有安全暂放条件时可比较携容器首访。不能未经重跑就认定整段多余 |
| 56–61 | 第二袋先上台面，再抓回并放地面；期间一次超距、一次误选固定物 | 台面袋壁挡住内容。暂放位置除了稳定，还应支持下一步观察；6 次操作不等于 6 次都可删 |
| 67–92 | 第二盒牛奶处理用了 26 次操作：9 次导航、4 次 grasp、3 次暂放、7 次开关门、1 次 look、2 次入库 | 这段实际墙钟约 809 秒，包含模型响应。主要候选是门遮挡下重复暂放和重新取物；选择开门后仍可见的暂放位置，复用成功视角 |
| 79–80 | 打开冰箱后紧接关闭，中间没有取放 | 状态层面可避免的开关循环候选。第 79 次动作前应先处理会被再次遮挡的暂放位置；尚未在仿真中删除并重放验证 |
| 94–97 | 第二包饼干：超距抓取→接近→物品落到画面下缘→后退→抓取成功，共 4 次操作（首次抓取失败在 94） | 对小地面物体优先选物体本身作接近目标，保留观察距离；地板点击按落脚点执行，可能把物体移到相机下方 |
| 全回合 | 15 次 out_of_reach、1 次 fixed_object、2 次 navigation_unreachable | 分类记录。对长柜/冰箱，先检查同一合法目标的近侧可见表面；需要时显式换位，保持原目标与关系 |
| 非运动调用 | 5 次 update_plan 中 2 次被拒绝 | 首次缺顶层 reason（event-00014）；末次把多个 evidence ID 拼成一个字符串（event-00341）。纠正接口说明，不改变执行器接受规则 |

## 已确认的 skill 问题与本次修改

1. **执行协议矛盾**：旧 pick-and-place 与 material-actions 写着 grasp 可自动接近；实际 RGB 执行器要求显式 navigate_to。删去错误承诺，与现行协议一致。旧 pick-and-place 还要求检查未公开的 `contained_rigid_objects`；改为使用 RGB 中实际移动的物体和公开类别反馈。
2. **导航终止与恢复**：visual-exploration 明确先看四路图像，说明地板落脚点与小物体接近点的区别。目标已可用时执行操作；超距时先检查同一目标的近侧表面，否则再显式换位。不是减少必要的导航或解除距离限制。
3. **暂放与门遮挡**：新增 pick-and-place/references/staging-and-doors.md，检查“下一次开门后”物品是否仍可见、可取；内容被袋壁遮住时调整视角或高度。找到有效位置后复用视觉地标。规则不包含杂货对象 ID、固定数量、地图或任务路线。
4. **长程规划**：逐件维护完成证据与未知内容；避免“全部搬运完成”成为处理每一件的假依赖；比较空手探路和携物首访的成本，保留必要的安全探索。明确 update_plan 的顶层 reason、单个 response evidence_id、依赖和状态格式。
5. **按需加载**：pick-and-place 主文从约 2681 个英文词降至约 500 个；薄片、曲线物体、托盘、篮子、高柜和特殊放置关系的原有案例移到 target-selection.md / placement.md。主文保留触发条件，由 read_skill 加载相应参考。visual-manipulation 与 failure-recovery 的入口规则同步。
6. **同一协议中的开关说明**：material-actions 与现有动作契约一致，持有物可操作自己的开关；只有不能选到开关时才暂放。没有更改工具实现。

## 版本与验证

- 基线 skill bundle：`6f23283b5c007a753d443baef04e7d19ee4a1bb33a0857fb2fa0a8207cbcfedf`。
- 本轮候选 bundle：`c19b260155167d12ec8a2023aabcef03969519acb215c11a2a9984681905be58`。
- 新候选文本由实际 SkillLibrary 加载、冻结于 `../experiments/skill_iteration_20261021_validation_r2/skill_snapshot/`，三个新参考资源均通过 allowlist 读取。
- `tests/test_skills_loop.py` 与 `tests/test_long_horizon_planning.py`：6 项通过，验证加载、规划 profile、工具 schema 和调用。
- 既有后端与 RGB 显式导航回归：12 项测试、4 个子测试通过（test_gt_backend.py、test_visual_manipulation_navigation.py）。
- 原实验使用旧 snapshot，不重新标记为新版 skill 的成绩。候选文本的收益与跨任务副作用尚未验证。

## 候选完整实验与复用分析

已完成 `../experiments/groceries_v2_skillv1_gpt61sol_low_20261021_r1`；启动时核验实际 `run/skill_manifest.json` 的 bundle 与上述候选一致。相邻 `_source/launch.json` 保留完整命令、基线名、场景 overlay 和七项任务/执行源码 hash；这些 hash 与成功基线相同，结束后复核一致。实验记录在回合目录的 `experiment_record.md`。整个回合未修改冻结 skill 或执行器。

### 完整结果

| 指标 | 基线 6f23283b | 候选 v1 c19b2601 |
| --- | --- | --- |
| 独立目标 / 整任务 | 12/12，成功 | 11/12，失败：cabinet.n.01_2 未关 |
| act/look | 105 | 120，预算耗尽 |
| 失败操作 | 18：超距 15、固定物 1、不可达 2 | 26：超距 20、固定物 3、不可达 3 |
| 导航次数 / 路径距离 | 49 / 137.29 m | 54 / 111.29 m |
| 环境步 / 独立体积采样物理步 | 13586 / 2465 | 12430 / 168 |
| policy 墙钟 | 4837.83 s | 6554.17 s，含长段非运动等待与服务重试 |
| 结束 / 控制器 | achieved / exit 0 | blocked，如实报告漏关吊柜 / exit 0 |

六件食品与两袋均满足独立终态，未重现漏同类物品；仅缺一个吊柜关闭。候选全部审计检查中只有整任务成功、agent_reported_achieved 为 false；调用对齐、显式路径、RGB、公开决策、连续视频与底盘稳定均通过。整体 passed=false，不能写成实验通过。路径更短和仿真步更少是观测值，不覆盖动作更多及任务失败，也不建立稳定增益。

阶段对比：首袋暂放 15 对 24 较早，但首次食品入库 44 对 31 更晚；首袋全部食品到位在 57，袋子最终归位在 64。第二袋避开门的地面暂放在 86；两包饼干分别在 96、105 入柜，最后一盒牛奶在 115 入库。两次第二包饼干 fixed_object 的原图显示选中了地板/烤箱，不能归因于抓取器。GROC-007 的低矮物体导航分类是独立待复现问题。

结构化结果为 `../experiments/skill_iteration_20261021_comparison_r1_audited.json`；[完整对照、五视角与录像](http://10.130.140.115:8769/groceries_skill_iteration_20261021_index.html) 已发布并检查 HTTP 200。保留失败 v1，不能将本轮视为成功率提升。下一版先消除主文与门遮挡例外的冲突；执行器问题单独复现，若修复则另立协议/源码版本。

离线分析命令（在仓库根目录执行）：

```bash
python scripts/analyze_workflow_efficiency.py \
  ../experiments/groceries_v2_robust_gpt61sol_low_20261021_r1 \
  ../experiments/groceries_v2_skillv1_gpt61sol_low_20261021_r1 \
  --output ../experiments/skill_iteration_20261021_comparison_r1.json
```

使用新输出名保留每次比较；工具拒绝覆盖已有文件。输出逐动作时长、错误、决策、primitive 计数、未返回调用、规划等非运动调用的拒绝记录，以及任务、协议审计和 skill hash。运行未完成时结果可能为空；不能把缺失值当作失败或成功。相邻成功 open/close 只列为人工检查候选，须结合目标身份与 RGB 判断信息收益。工具不向模型提供任何隐藏状态，也不修改运行时。

基线解析已核对：105 次 act/look、18 次失败，与原始日志人工统计一致；分析脚本通过 Python 编译检查和 diff 空白检查。该检查验证记录工具，不构成新版 skill 的性能证据。

### 回合中发现的问题与候选 v2

候选回合约第 31–40 次动作仍在第一袋卸货恢复，反复出现物体因开门被挡、关门后又优先准备目的地的行为。`pick-and-place/SKILL.md` 第一步无条件要求“Before picking a loose item for a closed destination, prepare an open destination”，而 `references/staging-and-doors.md` 要求门挡住物体时先关门、取回并移出遮挡区，再重新开门。后者是前者的例外，却只在参考中出现；即使模型已读取两者，主流程仍可能诱导恢复循环。

这是已确认的指导冲突，不证明本轮所有失败由它造成。v1 完成后，候选 v2 已在主文同一句旁明确“会遮挡源物体时，先移走源物体”的例外；failure-recovery 与 visual-manipulation 入口未发现同类无条件要求。v1 的冻结资源与原始失败保持不变。

v2 bundle 为 `26f420d34792c9061eff2bb600e2749908b5d1a768fe2f4f1936b40a1fc773a3`，冻结在 `../experiments/skill_iteration_20261021_validation_v2/`。逐文件比较确认只改 `pick-and-place/SKILL.md`，实际 SkillLibrary 加载通过，pytest 的 skill-loop 与 planning 合计 6 项通过。未启动 v2 自主回合，不作性能结论。GROC-007 已独立复现，真实仿真局部对照 6/6 通过。现先启动 `groceries_v2_navv2_skillv1_gpt61sol_low_20261021_r1`，沿用 c19b2601 技能，仅改变执行器为 explicit_navigate_to_only_v2；独立 execution 副本冻结全量源码和 v1 技能。该回合用于 harness 回归，结果待完成。随后保持新执行器固定，再单独测试 v2 技能；不能将两种变化合并归因为提示增益。

## 后续迭代方法

1. 从真实回合标记动作段、对应 RGB、错误反馈与完成证据；区分必要执行、信息获取、安全恢复和可避免循环。没有最短解证据时，不给整个动作段贴“可删除”标签。
2. 将可迁移的决策规则写入 skill；接口/物理实现缺陷归到相应层，不用提示词掩盖。保留任务的计数、探索与完成核查难度。
3. 冻结候选 bundle。比较时保持任务/场景/协议/模型/预算一致，分别记录成功率、动作数、失败类型、重复暂放和开关次数、仿真步与墙钟；失败也计入，不能只比较成功回合的短轨迹。
4. 对柜门、容器、多件取放之外的任务做迁移检查，再用留出布局评估泛化。消融单条规则才用于归因；本轮同时改多处，只能评估整体候选，不能归因于某一句提示。
5. 把新证据追加到此记录及任务失败日志；成立的规则保留，无收益或引入回归的规则删改。每个回合以实际 skill hash 为准。

## 迁移验证的边界

先在杂货的同一冻结配置下完成候选回合和重复回合，保留失败，随后才扩展到其他任务。第二个现有任务可选热饮：它覆盖单手暂放、柜内取物、跨房间摆台以及取水→烧水→倒水的物质依赖，能检验新规则是否伤害这些能力。热饮 README 和构建 workflow 明确，其视觉协议使用 `high_level_visual.bddl` 与 `HighLevelHotDrink` 11 项高层评分；`problem0.bddl` 的原生水粒子定义不是该协议，不能直接替换或把高层成功写成官方成绩。

迁移前须重新核验运行入口、冻结任务/场景/高层评分版本、显式导航和技能加载；模型仍用用户指定的 gpt-6.1-sol / low，并单列协议与预算。历史热饮规划回合为 120 次动作、8/11，skill 和运行时版本不同，只用于定位失败，不能作为新版 skill 的受控增益基线。热饮也已参与开发，属于跨任务回归，不是留出测试；真正的泛化证据需要预先冻结且未参与修改的新布局或对象组合。

墙钟是运行成本指标，包含模型服务等待。候选杂货回合早期的 `controller/model_events.jsonl` 已出现 overload / stream-disconnected 重试；汇总时保留服务错误次数，另列成功与失败的运动动作、工具耗时和仿真步，不把服务拥塞归因为机器人策略变慢。日志短时安静且进程存活时继续观察原回合，不重复启动。

## 预先固定的同协议重复比较

`../experiments/skill_iteration_20261021_controlled_plan/plan.json` 在新协议首轮早期冻结：顺序为 v1-r1、v2-r1、v1-r2、v2-r2，均使用 explicit_navigate_to_only_v2、同一开发实例、原指令和预算，每种 skill 两回合。完整记录成功/失败及恢复成本；两回合只提供描述性证据，不宣称可靠总体成功率。运行时或 skill 再改动则另立版本，不覆盖本矩阵。原 v1/旧协议 11/12 用于 harness 回归背景，不混入新协议 skill 比较的分母。随后按相同执行器分别验证两个 skill 的热饮高层视觉回归，热饮不算留出。

热饮迁移输入已冻结到 `../experiments/hot_drink_skill_transfer_20261021_frozen/manifest.json`：`install.py --check` 只读核对通过，三个场景 JSON 与目录元数据独立复制到 overlay，任务包与高层评分源码 hash 已记录。其余资产仍是共享只读引用，不能称全量资产快照。固定 house_single_floor / 301 / seed 0、120 次动作及 11 项 high_level_hot_drink_state_v1；尚未运行新配置真实仿真或模型回合。迁移应使用 task_snapshot/high_level_visual.bddl，不使用原生水粒子定义。

该矩阵由实验目录中的 `run_frozen_matrix.py` 串行执行，监督 PID 初始为 616416，当前运行与状态见 `execution_status.json`、`execution.log`；每轮 launcher PID 写入各自 `_source/pid.txt`。首轮等待现有 launcher 553610 与其 finalizer 完成，随后启动预先冻结的三轮，不重试已有输出。控制器正常结束、正式 finish、连续视频通过与实际 skill hash 是运行完整性关卡；任务未成功仍保留为有效失败，不要求 audit 总分通过才进入下一轮。源码或任务 hash 不符、输出冲突、基础设施异常均停止批次等待诊断。每轮发布 `groceries_navv2_skill_matrix_20261021_index.html` 并生成不可覆盖的逐阶段比较，最终仍需人工结合 RGB 审阅；热饮尚未加入自动批次。

导航协议 v2 的因果边界补充：物体解析结果除地板分类外，还进入既有终点可见性检验。旧/新执行器回合只能比较该组合运行时变化，不能当作地板谓词的单独消融；该变化在四轮 skill 矩阵内保持固定。细节记录在 `controlled_plan/runtime_scope_clarification.json`，不修改已冻结的实验声明。

## 完整四回合矩阵结果（2026-10-22 核验）

固定导航 v2、任务/场景/预算/模型不变，已按计划完成全部四回合。四轮实际运行源码 hash 均为 `c8258d21a53999401012628d3711a64792b5bad9689b6b04df38b8fa1add4e2d`，技能 hash 与计划相符。独立目标评分取最佳合法绑定。

| skill / 回合 | 目标 | act/look | 失败动作 | finish | 主要未完成项 |
| --- | --- | --- | --- | --- | --- |
| v1 / r1 | 5/12 | 120 | 31 | blocked | 两盒奶、奶酪、两包饼干、后备箱、一个柜关闭 |
| v2 / r1 | 9/12 | 106 | 21 | achieved，错误完成声明 | 第二盒奶、第二包饼干、第二袋 NextTo |
| v1 / r2 | 8/12 | 120 | 25 | blocked | 奶酪、一包饼干、第一袋 NextTo、后备箱 |
| v2 / r2 | 4/12 | 80 | 25 | blocked | 番茄、两盒奶、两包饼干、第二袋 NextTo、后备箱、冰箱关闭 |

两版均 0/2 成功、平均 6.5/12。v2 动作少，包含 106 步误报完成和 80 步受阻提前结束，不能称为效率提升；两次重复也不足以估计可靠总体成功率。四轮调用对齐、显式导航、公开决策、底盘高度和录像通过，整体 audit 因任务失败而不通过。模型服务错误事件均未记录，不把这些失败归为服务中断。

[四轮审阅和完整录像](http://10.130.140.115:8769/groceries_navv2_skill_matrix_20261021_index.html)首页、四个审阅页与四个视频均 HTTP 200。逐项未完成目标、版本核对在 `../experiments/skill_iteration_20261021_controlled_plan/matrix_result_review.json`；完整时间线/成本在 `completed_through_cell_4.json`。原旧协议 v1 的 11/12 不混入此矩阵分母；结果不支持推广 v2，也不从少量随机模型轨迹直接认定全部差异由导航修复导致。

下一步已启动冻结热饮跨任务回归，顺序 v1→v2，每版一次；task/scene/11 项高层评分/预算固定，模型仍 gpt-6.1-sol low，无 demo、非 semantic。监督初始 PID 715201，运行状态见 `../experiments/hot_drink_skill_transfer_20261021_frozen/transfer_status_20261022.json`。其完成不替代杂货失败诊断，也不算留出泛化。杂货的导航受阻、重复类别漏件和错误完成声明仍需按原图与执行日志复查；当前不继续叠加规则或宣称新版有效。

热饮启动配置纠正：v1-r1 在模型开始前因 CPU 从启动启用而无法初始化水系统，保留为配置失败（HOT-001），v2-r1 未启动；新 r2 批次移除 `--cpu-dynamics`，两版均采用已有 GPU-init→CPU-after-water 流程，任务/场景/评分/技能和执行器源码不变。运行状态改查 `transfer_status_20261022_r2.json`，监督初始 PID 775327。首个失败不计入模型成功率分母；仍须报告尝试总数及基础设施失败数。

## 候选 v3：整份计划替换时保留未决义务（尚未自主测试）

v2-r1 的 event-00100 仍把两袋检查设为 active；event-00327 将 milk/cookies 类别置 done，同时删去未核清的袋内检查义务；event-00353 改名并将两袋均置 done。逐动作私有对象归属复核显示，仅动作 82 抓取 milk79、91 抓取 cookie85，未抓到 milk83/cookie86；最后 103→106 处理的是 bag81，不是 bag82。该私有身份只用于事后诊断，没有交给模型。独立目标 9/12 与漏件一致，不能仅将问题解释为评分绑定错误。

新候选只在 long-horizon-planning 的 whole-DAG replacement 合约旁加入一次显式 reconciliation：对照旧计划和 item ledger，带回每个未决义务，或用公开证据解释其解决；保留载物容器身份，将未知内容检查与类别入库进度分开。避免继续重复添加“逐件计数”的泛泛句子。此改动针对计划替换中的信息丢失，尚未证明能防止全部漏件、错误完成声明或减少动作。

v3 bundle `5fd7031b5e4b2398c27723137156011b9ee539eb8820ac3c62b16a132894edd2`，冻结在 `../experiments/skill_iteration_20261022_validation_v3/`；实际 SkillLibrary 加载通过，相对 v2 逐字节只有 long-horizon-planning/SKILL.md 改变。不新增工具字段、隐藏数量、评分/结束守卫，不改变 task/harness。当前热饮 v1/v2 对照使用独立冻结源码，不受此候选影响；v3 未启动自主实验，未推广为已验证改进。

## 热饮迁移的既有结束校验边界

修正后的 v1-r2 已通过 bridge_ready 并产生模型动作，初始化问题已通过实跑验证；整任务结果仍待完成。两版冻结热饮运行均沿用既有 high_level_harness finish 守卫：achieved 经独立评分核验，不满足时只返回未完成，不泄露分项；有预算且错误可恢复时 blocked 被拒绝，aborted 仅供监督器。杂货协议没有相同守卫。因此热饮内部 skill 比较可保持同协议，但不能直接用跨任务的误报完成或提前结束次数比较策略质量，也不能称为完全无评分反馈的 RGB 协议。补充来源 hash 与反馈范围见冻结目录 finish_protocol_supplement_20261022.json；没有修改正在运行的源码。

## v2/v3 前瞻重复验证

已冻结 `../experiments/skill_v3_controlled_20261022/plan.json`，顺序 v2-r1→v3-r1→v2-r2→v3-r2。每回合独立复制执行源码和技能，保持导航 v2、杂货任务/overlay、模型和预算；唯一策略差异是 whole-DAG 未决义务 reconciliation。串行监督器等待热饮 r2 批次正常完成后接续，完整性关卡失败即停止，不覆盖或自动重试。状态见 execution_status.json；每种两回合仅描述性证据，动作更少但任务未完成不算收益。新比较尚无结果，v3 仍未验证。

## 热饮迁移首轮终态

v1-r2 已完成：高层 11/11、111 次动作、11 次失败、9436 仿真步，墙钟 4703.13 秒、运动工具 2837.31 秒，正式 achieved、独立评分与全部 audit 通过。模型服务错误未记录。v2-r2 仍在运行，不提前宣布迁移比较结论。首轮属于开发任务的一次回归成功，不是留出泛化，也不证明原生水粒子任务成功。完整运行身份见其 experiment_record.md。[审阅与连续录像](http://10.130.140.115:8769/hot_drink_skill_transfer_20261022_index.html) 已核验实际链接 HTTP 200。

动作 40→41 的关闭/重开由“只关一扇遮挡门”的预期与整组柜门操作范围不一致触发；已查看三张前后 RGB，确有整体关闭后重新暴露柜内。两动作运动工具共 4.326 秒，是可避免恢复候选；没有同状态反事实回放，不直接减去两步或宣称必要动作下界。先观察跨回合是否复发，再决定是否值得新增 skill 规则。

## 热饮 v1/v2 迁移完成

v2-r2 正式 blocked：120 动作、10/11，仅 tray_on_table 未满足；20 次失败（4 invalid_visual_target、3 out_of_reach、6 navigation_unreachable、5 no_surface_at_point、2 unsupported_relation），8524 仿真步，墙钟 4872.49 秒、运动工具 2490.23 秒。控制器/协议/连续视频通过，整体 audit 因任务失败不通过；无记录到的模型服务错误。v1-r2 为 11/11、111 动作。每版一次，仅开发迁移回归，不支持推广 v2；未完成任务的较低仿真/工具成本不算效率收益。启动 r1 配置失败继续单列。结构化核验见 frozen/transfer_result_review.json；首页、两版审阅和视频实际链接全部 HTTP 200。

热饮批次正常终止后，前瞻杂货 v2/v3 对照第一格已启动，launcher 初始 PID 2874404。没有改任务、运行时或运行中的 skill，状态以 skill_v3_controlled_20261022/execution_status.json 为准。

导航离线复现已固化为 `../experiments/groceries_navigation_diagnosis_20261022/replay_static_grid.py`，实际运行在 1 秒内重复五个静态结果并校验地图 hash：原失败选点中两点在静态图也不可投影，另两点在不含动态遮挡时可达。此脚本不包含当时动态 AABB/实际 masked grid，仍不是完整导航故障的红色复现；不能据此放宽碰撞距离或声称已定位根因。缺失输入与下一步私有同状态取证见 reproduction_limits.json。冻结比较完成前不改运行时。

计划诊断精化：新增 `scripts/audit_plan_replacements.py`，仅提取被工具接受的完整计划更新，列出未决 ID 消失、相同 ID 描述改写及新 done，供事后审阅，不自动判错、不向运行中模型反馈。旧 v2-r1 实跑提取 6 次 accepted updates、3 次含未决 ID 消失。尤其 event-00327 并非删掉 bag1/bag2 ID，而是将“检查袋内/确认空袋”的描述收窄为“空袋在冰箱旁”；真正的问题是同 ID 语义义务丢失，不能仅靠 ID 连续性检查。此前“删去检查义务”应按此语义理解。event-00353 才发生 bag1/bag2 ID 替换。重命名、合并和合法解决都可能产生候选，需结合公开证据与独立终态判断。


## v2/v3 前瞻比较第一格终态（2026-10-22）

`groceries_v2_navv2_skillv2_gpt61sol_low_20261022_r1` 已完成：116 act/look、20 失败（15 out_of_reach、3 navigation_unreachable、1 fixed_object、1 postcondition_error），独立最佳绑定 10/12，正式 achieved 但任务失败。遗漏为 milk79 Inside fridge 和 bag82 NextTo fridge；其余十项满足。实际 v2 bundle `26f420d34792c9061eff2bb600e2749908b5d1a768fe2f4f1936b40a1fc773a3`，任务和导航 v2 保持冻结。18339 环境步加 163 volume-sampling 步，194.287 米；墙钟 6906.294 秒，运动工具 4176.207 秒。模型服务 1 次 overload、3 次断流重连，均后续恢复，不能将墙钟差异全部归因于策略。

动作 98 抓取 milk83、100 放入成功，milk79 无单独成功抓取记录。event-00328 仍明确说另盒奶待查；00331 把两袋检查节点合并为 done，剩余义务收窄为车库饼干及泛称余货；00378 将冷藏品整体标为 done，00380 错误声明完成。00331 引用的袋空证据 00312 实际为导航成功，操作完成本身不能证明袋空；附带 RGB 是否充分仍需视觉审阅。bag82 早前暂放成功不等于终态相邻保持，尚未隔离是哪次后续动作改变关系。离线证据见 `skill_v3_controlled_20261022/cell1_result_review.json` 和该回合 source 下的计划/服务审计，未向运行中模型反馈。

[第一格审阅与连续录像](http://10.130.140.115:8769/groceries_skillv3_20261022_index.html) 已发布。v3-r1 接续运行，尚无结果；不因 v2 失败就判候选 v3 有效，不改本矩阵策略或预算。

第一格终态视觉补充：人工查看 capture57/58/116/117 的四相机图（两张 contact sheet 保存在对应 source），最后 capture117 的前视支持冰箱已关闭、左视支持吊柜区域，但四视图没有清楚展示两只袋及内部。00378 声称左视图确认两只空袋缺乏直接图像支持。bag82 在 env_step7821 曾 NextTo verified、终态为 false；现有图像视点不同且无连续物体 pose，仍不能归因于某次开关门或碰撞。结构化视觉边界见 source/bag82_final_visual_review.json；不向运行中的 v3 提示。


## v2/v3 前瞻比较第二格：保留 ID 仍会收窄任务范围

`groceries_v2_navv2_skillv3_gpt61sol_low_20261022_r1` 已完成，最佳合法绑定 10/12，缺 milk83 Inside 和 bag82 NextTo；正式 achieved 是错误完成声明。81 动作、9 失败（2 navigation_unreachable、7 out_of_reach），10819 环境步加 145 volume tick，101.659719 米；墙钟 4461.452224 秒、运动工具 2569.999637 秒。1 条服务断流重连，回合恢复并正常终止。实际 task/skill/runtime/model/instruction/scene/budget/录像完整性检查通过，整体 audit 因任务失败不通过。实际 bundle 为 5fd7031b…，完整身份见该回合 experiment_record.md 与 `skill_v3_controlled_20261022/manual_review_through_cell_2.json`。两版首轮均10/12、均误报完成；v3 较短的失败轨迹不能称为成功效率提升，剩余两轮继续冻结执行。

局部正面证据：milk79 在35抓取、37存放成功；cookie86在60/62、cookie85在66/69分别抓取/存放，两个不同实例。负面证据：3 次 accepted DAG 更新没有删未决 ID，但 event-00271 把 cold 从“全部冷藏食品、等待第二袋检查”改为“全部已见冷藏食品收纳完成”并标 done，引用的 event-00249 是第74步关冰箱成功；milk83 始终没有单独成功抓取。节点连续性并不保证原义务没有被“已见”限定词缩小。v3 本次不能算修好漏件或终态核验。bag82 在73放置成功、终态不相邻，具体改变关系的动作仍未隔离。81关闭洗碗机不在目标中，仅列额外动作候选，未证明删除后无影响或由碰撞触发。

[前两格审阅和连续录像](http://10.130.140.115:8769/groceries_skillv3_20261022_index.html) 实际首页、v3审阅页及视频均HTTP200。完整结构化证据在 cell2_result_review.json 和 source/plan_review_terminal.json。下一格 v2-r2 已接续，不因这次结果中途改 skill/harness 或追加隐藏数量提示。

第二格洗碗机动作证据修正：capture13/16显示早期闭合，81显示打开、82显示close后闭合，成功open日志没有洗碗机。81确为目标外整理，但有恢复原状的作用，不能直接称无意义冗余；具体触发仍未知。已单列GROC-009和source/dishwasher_state_change_review.json，不修改冻结回合或归因碰撞。

### 前瞻v2/v3第三格结束（2026-10-22）

v2-r2正式achieved但独立9/12，漏milk83 Inside、cookie85 Inside及bag81 NextTo；75个预算动作、76次act/look尝试（含一次act primitive=look参数拒绝），9次已接纳动作失败加1次参数拒绝。10198 env步加122采样tick，103.878335米，wall4130.042205秒、请求/动作2450.331159秒，无模型服务错误。14项手动冻结配置核对通过，协议/录像通过，整任务审计失败。三次整份计划更新0个未决ID删除，但最终253以74旁放证明第二袋取空、以单枚饼干67存放证明整类完成；72抓到bag82仍含2件。bag81曾37旁放成功，终态false，触发未定位。自动成本汇总的actions=76实际是请求尝试，不等于预算动作；保留原文件并在cell3_result_review.json与experiment_record.md分列口径。v2两轮0/2，平均9.5/12；v3第一轮0/1，第四格已启动，未完成不计失败。不因失败回合动作少宣称效率提升，不推广候选。审阅与连续录像：groceries_skillv3_20261022_cohort/03_skill_2_repeat_2/（内网HTTP200）。

- 成本口径更正（BENCH-001）：旧导航v2下skillv2-r1（20261021）原报告106动作/21失败应分为105预算动作、106请求、20执行失败和1次stale_observation参数/版本检查拒绝。新前瞻v2-r2则75预算动作、76请求、9执行失败和1次invalid_arguments拒绝。旧报告保留，新版成本分析已在前三格及六场历史回归上与runtime核对；任务评分、成功率、距离、步数和请求耗时不变，不据此宣称策略提升。

## 2026-10-22：用户确认热饮专项三版本迭代

用户已确认：热饮 house_single_floor / instance 301，gpt-6.1-sol low，四固定 RGB、模型自主像素选择与显式导航，planning skill 开启；11 项 high_level_hot_drink_state_v1 高层评分。每版 seed 0/11/22、120 act/look，基线后按证据产生两轮候选，共计划 9 个回合；无任务墙钟截止，初始化 watchdog 900 秒、预算耗尽/关闭后的收尾 watchdog 180 秒，次级环境步上限 20000。非原生粒子官方成绩，开发实例回归，不是留出泛化。

本批目录 `../experiments/hot_drink_optimization_20261022/`。基线 skill bundle 为 `5fd7031b5e4b2398c27723137156011b9ee539eb8820ac3c62b16a132894edd2`，当前文件逐字节冻结于 baseline_source，SkillLibrary 实际加载结果在 baseline_frozen。场景/目录采用已有冻结 overlay 并核对 hash，任务定义另行复制，资产仍含共享只读引用。baseline_manifest.json 记录全量实验源码 hash、任务协议和来源；plan.json 记录确认配置与启动命令。当前等待既有杂货对照进程 2751300 正常结束，串行复用 GPU 0；后台监督为 run_baseline.py。候选尚未编写，不预先堆叠所有 skill 修改。

为落实已确认配置，本批启动前增加显式 --unbounded-episode：controller 的进程等待不设总时限，Budget.wall_seconds 为 null；动作、调用及环境步计数仍受限。bridge 健康状态为私有监督提供预算耗尽标志；初始化与收尾分别限时。保留原有限时模式默认值。相关 43 项测试及 28 个子测试通过，包括新增无总时限 controller 正式 finish 测试；仅证明运行契约，不证明自主任务收益。初次测试因未指定 PYTHONPATH 导入了其他安装副本，未启动实验；正确指定 PYTHONPATH=src 后测试通过。基线与后续候选共用本批冻结运行时，不与历史有限时回合混算受控收益。

每个基线回合结束生成 audit、成本分析和连续录像审阅页；任务失败照常保留并继续其他 seed，基础设施/闭环/录像故障保留后停止复用并诊断。基线三场完毕须审阅失败目标、公开决策、逐动作原图及恢复段，再仅修改证据支持的层。候选快照、理由、效果和退化在本节后追加。当前未产生本批自主成绩。

启动前补充闭环校验：CPU mock CLI→bridge→initialize→单动作预算耗尽→finish 集成测试首先复现 wall_seconds=null 在 bridge 的 float 转换错误；同时检查并修正共享 start_standalone_clock 的空预算处理和 bridge 服务循环。随后集成测试通过，正式模型 controller 的无限等待/finish 测试仍通过；相关合计44项及28子测试通过。收尾计时只在最后动作返回并请求停止后开始，不在最后动作开始时计时。旧启动前冻结副本和 manifest 保留，实际启用 baseline_source_r3；此时尚未启动任何热饮回合。候选 skill hash 未变，重启的只是仍处于等待状态的本批监督。测量口径已预先写入批次 measurement_protocol.md，包含冗余候选与恢复段的人工证据要求及覆盖范围。

## 2026-10-22：冻结 v2/v3 四轮对照完成

固定杂货v2、house_double_floor_lower/301/seed0、nav v2、gpt-6.1-sol low。v2两轮分别10/12、9/12，预算动作116、75（第二轮76请求）；v3两轮10/12、11/12，预算动作81、89。两组均0/2全成功、2次误报完成；均值9.5与10.5仅为同一开发实例的小样本描述，不证明v3因果提升或泛化，不推广。失败回合较短不能当作效率收益。

第四轮六件食品最终Inside全部通过，晚期主动纠正第一袋判空并补入milk79；唯一失败bag82 NextTo fridge。81旁放实际验证NextTo真，终态假，首次变化待私有回放定位。89动作/89请求，7次失败（6超距、1导航不可达），11878 env steps另202 volume ticks，110.726610米，4707.424393秒回合墙钟，2765.086363秒动作工具时间；无服务错误。14项独立完整性检查均通过，任务audit仍因不完整失败；审阅/连续视频HTTP200。

权威记录：`../experiments/skill_v3_controlled_20261022/manual_review_through_cell_4.json`、`costs_through_cell_4_counting_v2.json`、`cell4_result_review.json`及各轮experiment_record.md。自动冻结旧分析器输出保留；纠正后的统计将预算动作、参数拒绝请求、执行失败分开。完整录像索引 `http://10.130.140.115:8769/groceries_skillv3_20261022_index.html`。下一步优先建立袋子关系退化与导航/操作距离衔接的实际红色复现，基于根因分别版本化benchmark设施修复与skill变更。

### 第四轮第一袋判空与晚期恢复的RGB复核

已实际查看rgb33-right、rgb34-front、rgb83-left对照：桌面接近后仍主要见袋口/内壁，不能可靠确认完整袋底；34却判空且抓袋仍带1件。晚期地面袋中蓝紫包装可见，83主动纠正并抓到milk79。位置、视角和门状态均变化，不能将恢复因果归于关门或某条skill。候选视觉优化方向为检查袋底覆盖和保留遮挡不确定性，而非机械增加靠近动作；尚未改skill或冻结新版本。证据保存于第四轮trial/source/first_bag_visibility_evidence_review.json和first_bag_empty_claim_vs_late_recovery.jpg。

### 视觉容器覆盖候选（独立草案，未部署）

以冻结v3为父版本，仅在visual-exploration/SKILL.md补充容器内部区域覆盖：区分袋口、袋底与壁后遮挡；缺失区域仍未核清；按能暴露缺失区域的侧移/安全暂放选择下一动作，并比较实际覆盖变化。未规定多走几步、固定放桌或隐藏物品数量。候选仅在 `../experiments/skill_visual_coverage_20261022_candidate/`，仓库当前skill保持v3。实际SkillLibrary重定向到候选根目录加载、allowlist读取通过，共11个资源；bundle `cd2c5f2672ce6a8731595b3c7c1094666ce45ceb885e1ec4692eb442556d9378`，逐文件确认仅一个资源变化。无自主效果证据、未推广；可能增加观察成本，须在固定执行器上完整比较成功率和恢复成本，另做迁移。导航候选单独验证，不将两项同时改变后归因为skill收益。


## 2026-10-22：有无 skill 的独立消融协议（尚无模型结果）

新增 `planning_no_skills`，与 `planning` 共享 initialize/look/act/finish/update_plan/remember/recall 的输入 schema、public decision、动作预算和完成处理。只移除 list_skills/read_skill、SkillLibrary 加载和技能读取指令；保留共同动作说明、依赖规划与记忆。启动用 `--long-horizon-planning-skill on --without-skills`；默认启动行为不变。minimal 同时移除规划/记忆，不用作仅归因于技能文档的对照。

验证：先见未知 profile 的两项失败，再接通目录、harness、提示词、启动器和控制器。34项测试与32子测试通过，覆盖实际规划、记忆、动作执行、禁止 skill 读取、supervisor 收尾，以及三种客户端的提示词/工具注入；diff检查通过。源码、两个提示词/目录和结果冻结于 `../../experiments/skill_ablation_protocol_20261022/`。这些只证明协议可用，不证明skill收益；完整对照尚未启动。初始扩大测试另发现旧导航测试期待require_object=False而当前导航契约为True，单独记录，未因消融修改执行器。正在运行的两轮设施回归使用原冻结源码，不受本次修改影响。

下一组对照必须统一设施版本、杂货v2/301/seed0、模型gpt-6.1-sol low及预算，保留全部失败，以独立完整成功率优先，辅以漏件、冗余及恢复成本；v2/v3历史结果不充当有无skill证据。

有无skill完整对照已提前冻结4个cell：`../../experiments/skill_presence_controlled_20261022/plan.json`。GPU2无/有、GPU3有/无，各组各2次；同一开发实例，只作描述性比较。执行副本以正在回归的placement 1b9ace与navigation 74bb8c设施为父版本，只接入消融协议和纠正后的统计器，共154源码/skill文件校验。实际CPU harness初始化验证有skill为v3哈希、无skill为null，共同工具schema一致；尚无provider或物理验证。状态明确为prepared_not_launched_pending_facility_regression_review；须先审核当前设施回归，再按GPU锁启动，不把未运行计划算结果。

四cell监督脚本已准备在skill_presence_controlled_20261022/run_controlled.py：每GPU锁内串行两个固定cell，校验冻结源码、实际profile/skill哈希、formal finish与视频状态，独立audit保留；真正任务失败不因低分取消第二轮。启动前要求带证据哈希的设施审核记录（agent内部证据检查，无需用户额外许可）。py_compile通过；缺少审核记录时实际调用拒绝且四个trial目录均未创建。锁/真实launch/audit分支未执行，不算完整监督器验证。

恢复成本审查准备：历史v2/v3四轮已生成逐失败候选区间（20/9/9/7），旧成功率及成本字段完全不变，输出位于../../experiments/skill_recovery_review_20261022/reports.json。候选直到下一次同类成功，只用于定位RGB/动作复核，不能证明同一目标恢复、动作冗余或因果成本，重叠区间不相加。该统计器版本单独冻结；当前四cell执行快照不变，后续对照统一使用同一分析器版本离线复核。

恢复区间实审：四轮45区间按私有选点日志离线核对，33个前后owner相同、12个不同；导航换地面owner可能是有效绕路，不能据此判失败或冗余。v2-r1动作44奶酪超距后47抓到第二袋，decision明确是先取袋、奶酪待取，所以同类成功不等于原物品恢复。首袋4→5→6已实际看RGB三图并核对均为bag81：超距后接近同袋，再抓取成功，后续2预算动作/38.84729秒工具时间（失败本身0.790666秒，均不含模型/skill读取时间）；导航有明确恢复作用，不宣称最短路径或skill因果收益。结构化审查和带选点拼图位于../../experiments/skill_recovery_review_20261022。保持统计器confirmed_recovery=null，人工结论单独存证。

消融真实传输验证：冻结execution中的planning与planning_no_skills分别通过stdio MCP→HTTP bridge→CPU mock RGB完整调用，实际执行initialize/update_plan/remember/recall/look/act/finish七个共同工具，正式关闭且桥进程exit0；catalog逐字段schema核验通过，skill组v3哈希/无skill组null符合预期。首次六工具探针与补齐look后的七工具探针分别保留于skill_presence_controlled_20261022/transport_preflight及transport_preflight_all_common；154个冻结源码哈希再次逐项一致。纯脚本+mock后端，不计入自主模型、provider连接或物理任务成功。

消融计划revision2：修正独立audit对planning_no_skills的profile/decision检查遗漏；execution_v2相对原冻结execution仅差audit及带恢复区间的新分析器，保持所有策略/设施/任务输入相同。旧plan/execution不覆盖；当前plan显式指向v2及source_manifest_v2。监督器增加调用对齐/视频/公开协议完整性检查，历史task=false或agent blocked不阻止既定第二轮；缺失检查或调用错位则停止并保留。19项测试及两份真实mock传输账本离线核验通过，仍非实际模型结果。


### 2026-10-22：导航设施完整回归的终态漏件（GROC-003 / GROC-010）

固定杂货v2 / house_double_floor_lower / 301 / seed0，BDDL b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1；gpt-6.1-sol low、skill v3、导航74bb8c、旧放置器的完整自主回合 `../../experiments/groceries_v2_navreach_candidate_skillv3_gpt61sol_low_20261022_r1` 正式结束。独立最佳binding为11/12：仅第二件饼干入柜失败，两袋NextTo及全部关闭目标终态通过；63预算动作、7909环境步、79.201108米。模型误报achieved，不能以动作返回成功代替逐件验收。

确认原因：event-00179称第二袋底部可见且无余物并抓袋，私有carry记录env6855显示bag82仍携带cookie86；cookie85已单独入柜，cookie86未被单独尝试抓取。这是取空/完成判断遗漏，不是cookie86抓取能力失败。已实际查看rgb52四视图：袋在back图下缘，袋口较小且斜视，无法确认完整袋底或从图中可靠识别余饼干；不应将未见物体解释为空。图像、事件与私有状态分别保存于该轮设施目录的 second_cookie_omission_review.json、second_bag_empty_claim_rgb.jpg、final_goal_review.json。私有身份/关系仅诊断，不注入模型。沿用GROC-003，不因新回合另造根因；尚未修改skill，不宣称已修复。后续统一设施后验证已有视觉覆盖候选与逐件清点，通用skill不写入此场景准确物品数量。该轮并未重现关门后NextTo退化，不能由单轮通过否定历史设施缺陷。


### 2026-10-23：设施回归完成与有无skill对照revision3

placement1b9 + navigation74bb + skillv3 的完整杂货v2/301/seed0回归独立12/12，88预算动作、12412环境步，审计所有检查通过，录像全流解码通过。单轮成功不证明放置器因果收益或稳定成功率。GROC-011探针r2完成同一dump初态的三组物理比较：静置350步三件保持；旧导航239步番茄散出；候选263步三件保持。旧组障碍遗漏/候选包含小袋检查均通过，录像解码通过。私有构造对照，不是原回合精确重放或自主成绩。

据此skill_presence_controlled_20261022更新至revision3：新execution_v3仅替换execution_v2的RGB导航为d57f520d6848e0a16f4f83db8bdeaeabf49d75e55f98715256da14a5d45df7d0，保留placement1b9、skillv3、相同模型/任务/预算。原plan/execution保留，154文件manifest校验；facility_review.json关联物理/自主/录像证据哈希。GPU2先无后有、GPU3先有后无，各2重复，独立准备进程已启动。全批仍研究协议；新导航尚无整轮自主结果，不将预期收益写为结论。记录每个任务失败，逐轮检验实际profile/skill哈希和审计；与历史v2/v3比较不得混为有无skill证据。

## 2026-10-23：热饮三 seed 基线与候选1

热饮基线已完成，实际 bundle 全为 `5fd7031b5e4b2398c27723137156011b9ee539eb8820ac3c62b16a132894edd2`；seed0/11/22 的独立11项高层目标全部通过，正式finish、模型/仿真调用对齐、连续录像和其余audit均通过。动作116/116/105，失败17/17/13；均值112.33动作，失败均值15.67，中位动作116。policy墙钟6262.63/8790.26/7315.34秒，包含服务和恢复等待，不是纯motor成本。三次开发seed的3/3不能外推为稳定可靠性或留出泛化。

记录：`../experiments/hot_drink_optimization_20261022/baseline_through_cell_3.json` 为冻结分析器结果；`baseline_evidence_review.json` 另记已人工选择审阅的恢复/搜索窗口、原始导航距离及服务reported token用量。分析器的高层任务navigation_distance_m为空，补充值只累计已返回导航的actual_path_distance_m，不假称覆盖失败路线。原图contact sheets位于baseline_visual_review；并未逐帧人工审完全部视频，视频完整性由各回合审计验证。

三场饼干分别在请求109/110/102才成功抓取；晚期搜索含重复invalid_visual_target的门状面板、开口受柜台遮挡和换位。seed22 capture79有架面上的小棕色候选，但不能仅据此证明当时已能识别/抓取饼干。seed11请求33导航后四图成为近白色几何，42-left恢复厨房可见、43回到正常操作位；原因未隔离，见HOT-004，不能用skill修改宣称修复执行器。

候选1只改 `visual-exploration/SKILL.md`：按实际可见架面记搜索覆盖；取走物体后检查新露区域；柜台遮挡时改变观察距离/侧向；记住被拒面板，只有新图像区分不同可动部件或解除遮挡时才重试。没有加入物体ID、任务路线、固定位置或评分状态。两个相关搜索规则作为整体候选，不作单句归因；目标为保持成功并降低重复搜索成本，收益尚未验证。

候选1 bundle `d6f25846be763cd9677aa52efe6615357258db95cd4ba0c237f45ab9d6a9ba48`，冻结于批次candidate1/execution与candidate1/frozen。逐文件比较确认相对baseline_source_r3只改此skill文件，运行时、任务、模型、预算和评分保持。frontmatter检查及冻结副本skill/planning六项测试通过。首次误用活动工作树的新版planning_no_skills测试配冻结运行时，产生三个profile不匹配失败；改从冻结副本运行对应测试后六项通过，未为通过新版测试修改本批运行时。

用户指出空闲GPU后，候选1曾尝试GPU3，但该GPU被另一实验PID3392095持锁；启动前被正常拒绝，未生成回合。相关plan/manifest/status保留为*_gpu3_lock_attempt.json。随后使用未持锁的GPU0，与基线同UUID，并采用独立hot_drink_optimization_appdata_gpu0缓存。三seed继续串行，supervisor为批次run_candidate_round.py，实际启动状态见candidate1/status.json。不能把调度锁冲突计成模型任务失败。候选1三场完成后再按证据形成候选2，当前不提前编写。

[热饮专项审阅与连续录像](http://10.130.140.115:8769/hot_drink_optimization_20261022_index.html)包含三个基线；候选结束后追加，不覆盖历史失败。

### 2026-10-23：revision3 有／无 skill 对照首轮完整结果（重复回合未完）

统一固定杂货 v2 / instance301 / seed0，BDDL b38c56b，导航 d57f520d、放置器1b9ace37，gpt-6.1-sol low，120预算动作／20000环境步／14400秒。两组保持 planning 决策、计划和记忆工具，仅有 skill 目录／读取能力与对应提示不同；有 skill 使用冻结 v3（5fd7031b）。首轮原始目录分别为 `../../experiments/groceries_v2_skill_presence_with_skills_gpt61sol_low_20261022_r1` 和 `../../experiments/groceries_v2_skill_presence_without_skills_gpt61sol_low_20261022_r1`。

| 首轮条件 | 原生最佳binding | 预算动作 | 失败动作 | 环境步 | 独立采样物理步 | 墙钟秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 有 skill | 12/12，成功 | 83 | 8 | 11399 | 1437 | 13249.30 |
| 无 skill | 11/12，失败 | 77 | 11 | 8943 | 1360 | 10200.23 |

有 skill 18项审计均通过；无 skill 除真实任务失败外的完整性检查通过，误报 achieved，漏第二份饼干。逐项验收与动作统计见各目录 final_goal_review.json、workflow_efficiency.json、demo_audit.json。录像编码通过，独立全片解码另记 full_video_decode_review.json，不以编码状态代替该检查。

成功恢复证据：有 skill 第二袋在env10183仍携cookie86，模型未直接判空，重新摆袋后env10249成功单独抓取cookie86，event250放入吊柜成功，event280终态谓词通过；随后再抓bag82的env10989私有记录为空。此为本轮取件链证明，不是因果消融结论。模型仅接收RGB和有界类别反馈，私有身份不注入模型。

成本限制：第二盒牛奶抓起后，冰箱视角恢复用了5次导航、2次look、约9.57米、1279.86秒；取第二份饼干也经历多次换位与一次重摆袋。首轮提高完成程度但更多动作及墙钟时间，不能称效率改善。重复回合正在相反GPU条件下运行，整组四格未完，不宣布稳定收益、skill升级或迁移效果。下一版候选应针对几何遮挡与相机裁切区分、有效取景距离及逐件取出证据；当前冻结回合不变。

### 2026-10-23：视觉恢复候选快照（未部署）

基于首轮完整对照中的冰箱门遮挡与袋底重新摆放恢复，建立 `../../experiments/skill_visual_recovery_20261023_candidate`，继承先前visual-coverage候选，仅修改visual-exploration/SKILL.md，将泛化转向建议细化为：区分画面边界裁切与几何遮挡；前者转向居中，后者选择能显露目标区域的侧向空地或安全重摆；保持开口与物体入镜，比较新显露区域，并通过当前RGB地标复用有效视角。未包含场景坐标、物体准确数量或评分信息。

冻结bundle `d24f0fae7b959da21828c301605129f6da44e74cbfe2585312e3c4b456727f26`（11资源）。quick_validate与实际SkillLibrary快照／allowlist读取通过，详见validation_record.json；仅证明文档和接口有效，不证明行为收益。未部署到源码skill或正在运行的四格对照。候选需在同一设施上与v3完整自主比较，核查成功、遗漏、导航／look成本及迁移回归；重新摆放可能增加成本或改变载荷，不能预先认定为改进。

### 2026-10-23：revision3 四格对照完成（完整组结论）

同一开发 instance301/seed0、冻结 d57 导航／1b9 放置器／v3 skill，gpt-6.1-sol low，沿用原预算与交叉 GPU 顺序。四轮运行完整性检查、独立评分及录像全片解码已核验。

| 条件 | 重复 | 最佳 binding | 预算动作 | 失败动作 | 导航米 | 墙钟秒 |
| --- | --- | --- | --- | --- | --- | --- |
| 有 skill | 1 | 12/12 | 83 | 8 | 98.25 | 13249.30 |
| 有 skill | 2 | 11/12 | 111 | 20 | 137.81 | 13109.55 |
| 无 skill | 1 | 11/12 | 77 | 11 | 80.36 | 10200.23 |
| 无 skill | 2 | 11/12 | 64 | 8 | 78.64 | 8646.21 |

有 skill 成功1/2、平均97动作／118.03米；无 skill 成功0/2、平均70.5动作／79.50米。三个失败均遗漏 goal5，且误报完成。该小样本未达到稳定成功与效率改善目标，不证明泛化或因果收益。完整证据及计数更正见 `../../experiments/skill_presence_controlled_20261022/four_cell_comparison_reviewed.json`：原始汇总把有skill第二轮失败动作写为21，而原始workflow_efficiency为20，原文件保留，新文件记录更正及源哈希。

下一组候选与v3在相同设施上比较；视觉候选仍未部署、未完成自主验证。重点同时检查遗漏、误报完成、导航与观察恢复成本。后续仍需独立布局与跨任务验证。

### 2026-10-23：视觉恢复候选与 v3 对照启动

预先冻结计划 `../../experiments/skill_visual_recovery_controlled_20261023/plan.json`，两组仅visual-exploration/SKILL.md不同，其余154文件清单内设施保持父对照版本；真实SkillLibrary预检分别匹配v3 5fd7031b与候选d24f0fae。固定杂货301/seed0、gpt-6.1-sol low与原预算；GPU2候选→v3，GPU3 v3→候选，每组两次。保留所有失败与独立审计／全片解码，任务失败不取消下一格，基础设施失败停止对应序列并保留诊断。

该对照检验内部覆盖及遮挡换位规则能否改善完整成功，同时核查误报完成、动作与恢复成本。没有修改任务／评分／执行器，不宣称候选已获收益；留出与迁移仍待后续验证。实时状态与PID见批次worker_gpu2.json、worker_gpu3.json，记录不是存活证明，监控时核对实际进程。

### 2026-10-23：热饮专项 candidate1 完成，candidate2 冻结启动

热饮301／seed0、11、22，同一11项高层评分与120动作预算，candidate1成功1/3（0、22均仅缺cookie_served）；基线3/3。预算动作120/119/120，失败动作20/21/17；基线116/116/105、17/17/13。candidate1不晋升。所有回合正常finish且模型调用对齐、状态重放与连续录像检查通过；失败场综合audit=false来自任务失败及未声明achieved，保留计入分母。记录见 `../../experiments/hot_drink_optimization_20261022/candidate1/cohort_outcome_review.json`、`full_trace_cost_review.json`。恢复窗口含必要搬运／观察，不整体计为冗余；墙钟受主机负载影响，不据此单独归因。

两场失败都在多次配送后进入末段饼干搜索；seed22多个层板视图仍有台沿遮挡，不能据棕色像素断言饼干早已可抓。seed11茶匙多次像素失败后成功；seed0末段面板目标分属不同区域，不能全部标成同一面板重试。当前未隔离设施缺陷，HOT-004继续未解决。

candidate2从基线分支：撤回candidate1视觉新增段，仅在long-horizon-planning第3步增加配送前核对未定位必需项、比较当前局部检查与返程成本、按已观察成本保留搜索／配送预算的决策标准，不强制全部物品先找齐。相对基线仅此一个文件变化；任务、设施与评分不变。冻结bundle `3befbe60be616b74df99a94d2254cea368b516538dbd32fa7fecb6d4b80592e0`；清单／计划／具体改动见 `../../experiments/hot_drink_optimization_20261022/candidate2/`。前置结构检查6通过、frontmatter合法，仅证明接口兼容。串行GPU0启动，性能仍待三场自主实跑。三粒开发seed不支持留出泛化或单句因果结论。


### 2026-10-24：热饮专项九场完成与最终选版

本组 baseline、candidate1、candidate2 均完成 seeds 0/11/22，九场全保留。独立高层11目标成功分别3/3、1/3、2/3；平均动作112.33、119.67、118.00；平均失败动作15.67、19.33、21.00。candidate2/0仍缺糖饼干，10/11；candidate1/0和22同样缺点心。三场失败均正式收尾且如实报告，调用对齐、状态重放、显式导航、空手与五视图录像完整性通过，不是被丢弃的基础设施失败。

结论：两轮候选均不晋级，保留基线 bundle `5fd7031b5e4b2398c27723137156011b9ee539eb8820ac3c62b16a132894edd2`；仅将本轮长程规划候选字节恢复为已冻结基线，视觉探索已为基线，其他层未作本轮新修复。候选与全部失败录像保留。candidate2耗时减少不等于完成效率改善，共享主机及模型等待未控制。

恢复成本实例：candidate2/11 请求98–114暂放饼干后重获定位17次请求、134.81秒工具耗时；candidate2/0请求97–120最后搜索与收尾24次请求、801.76秒仍未完成。窗口混有必要检查与恢复，未做删除反事实，不统计为已证实冗余；失败调用自身延迟不能代替下游恢复成本。仅3个同布局开发seed，无留出/原生粒子/官方挑战泛化结论。HOT-004继续未解决，不凭失败擅改导航执行器。

最终报告：`experiments/hot_drink_optimization_20261022/FINAL_REPORT.md`；完成核验 `completion_audit.json` 校验三版各254源项和九场实际bundle，54个发布链接HTTP200。各轮冻结manifest、完整比较、选帧与结构化恢复窗口、模型用量和录像均在同目录或逐场原始目录；用量不冒充实际费用。

### 2026-10-24：杂货视觉恢复四格最终审阅

固定杂货301/seed0、gpt-6.1-sol low，自定义原生BDDL研究协议，RGB加有限抓取类别反馈；d57导航/1b9放置、任务与预算不变，仅视觉skill不同。

|版本|重复|目标|动作|失败动作|导航米|
|---|---:|---:|---:|---:|---:|
|candidate|1|12/12|79|9|88.90|
|v3|1|12/12|92|20|82.79|
|v3|2|6/12|34|8|26.99|
|candidate|2|9/12|110|20|168.41|

两组均成功1/2，候选d24f0fae不晋升。原版r2提前blocked，不能把两组成本均值当作效率收益；候选r2漏牛奶79、饼干86与袋82归位却宣称achieved。GROC-003仍未解决；GROC-012记录旁放后导航失败、根因待诊断。未改skill、任务、评分、执行器或仿真器，也未启动下一轮。

四场各99个运行时src/skill快照文件匹配冻结清单，bundle均匹配；脚本不在该运行时快照检查范围。四条连续录像全片解码通过，人工检查关键8帧，不宣称人工看完全片。所有失败保留计入分母，任务失败之外完整性检查通过。每组两次同开发实例不构成留出、迁移或有无skill对照；benchmark成熟度仍为研究候选。
详见[完整审阅与证据索引](../../experiments/skill_visual_recovery_controlled_20261023/review.md)。

### 2026-10-24：GROC-012 恢复成本人工分类

原版第二轮旁放后动作27–34共8动作：5次不同地板目标的失败导航、2次换视角look、1次成功关闭冰箱。该窗口工具耗时86.81秒，其中失败导航9.54秒；旁放返回至最后一次导航返回墙钟195.83秒，至finish请求228.96秒。环境推进118步，来自look/close；失败导航本身未推进env.step。结束仍余86动作，非动作预算用尽。

未确认同像素无变化重复；look获取新视野、close也服务最终关闭义务，因此不将整段8动作称为冗余。它是未恢复成功的实际成本，不证明存在更优合法退出动作。避免把墙钟剩余全归因执行器或把重叠自动恢复窗口相加。[逐动作分类与计算](../../experiments/skill_visual_recovery_controlled_20261023/groc012_recovery_cost_review.json)。

### 2026-10-24：GROC-012 设施候选与导航超时，不新增skill版本

固定主体边缘补采样的私有完整前缀中，旁放通过官方邻接、地面支撑、空手及沉降后通路检查；下一导航走完8.804米至目标XY，最终朝向阶段耗尽原700步预算。CPU原跟随器时序重放显示理想反馈也需693运动步，预算仅690（预留10沉降），暂不支持“姿态抖动导致额外恢复”的解释。此为设施诊断，不是新模型实验，未产生自主成功或有无skill差距证据。

visual-exploration已有分段导航和检查新视图的指导，因此本轮不累积同义规则、不晋升skill；下一步应实测遵循当前协议的超时恢复，并做其他任务放置回归。全部历史分数保留。证据位于experiments/groc012_body_perimeter_candidate_20261024/navigation_budget_review.json与outcome_review.json。


### 2026-10-24 HOT-005：无 skill 对照 seed0 未完成及审计拒绝调用缺陷

任务prepare_and_serve_hot_drink，house_single_floor/301，seed0；任务哈希4be5469c5eff25f59504c90c4d78d04312057353ae35a0bf007012c2d0a9b915，独立high_level_hot_drink_state_v1。用户要求无workflow skill但每次tool前公开意图；保留相同规划工具，移除skill加载与读取。本次108/120动作、48失败，最终2/11，约100.4分钟策略墙钟。135次模型工具调用均有decision，无skill调用或加载bundle。

模型多次提前blocked被共同规则拒绝，在配置允许的续跑后仍未正式finish，最后监督器aborted。归类为未完成且收尾失败的尝试，不冒充完整闭环；全调用对齐因额外监督器finish失败，剔除此明确监督器调用后模型序列严格对齐。录像通过。

审计另遇event-00169：act携带finish形状参数缺primitive，被正常拒绝；审计在检查ok之前索引primitive触发KeyError。仅修复实验目录postrun_analysis副本：失败非法调用不提交语义，成功但缺primitive必须报错。最小拒绝/成功两例回归通过，真实轨迹18条私有状态重放全部一致；原始冻结源码哈希未变，原始故障与综合审计失败均保留。未更改任务、执行器或skill，未重跑替换失败。

证据：experiments/hot_drink_no_skill_20261024_s0/{REPORT.md,result.json,efficiency.json,audit_repair_notes.json,trial/}。后续审计应覆盖非法tool参数，区分模型意图记录、监督器收尾和全任务成功；单seed不能推出skill收益。


### 2026-10-24：启动仅消融长程skill的配对比较

用户澄清目标是检验长程规划skill的增量贡献，其他skill必须恢复且两组一致。已冻结 `experiments/hot_drink_long_horizon_ablation_20261024`：补跑无long-horizon-planning文档的seed0/11/22，参照历史完整skill三场（旧标签baseline，仅历史有skill参照）。两组均保留failure-recovery、pick-and-place、visual-exploration、visual-manipulation，同字节9份文件；9个工具与强制调用前decision一致，规划和记忆工具不消融。只移除长程主文档、BDDL参考及其提示词强制读取段。预检确认其他skill哈希、工具集合、意图要求及长程资源不可访问。

冻结四skill bundle `5dcce20f01e9c4d37cd45e46a28093152fc1a5321ddd87df060b71cbf7b00996`。相同热饮301、gpt-6.1-sol low、120动作/20000sim steps、无整场墙钟截止、GPU0串行；运行期间不改策略。先前全skill关闭单场单列，不纳入对照分母。已知审计非法act缺primitive仅修复离线分析，执行/评分代码不改。预启动校验曾遇父清单中的Python字节码缓存条目缺失，未创建场次；保留失败日志并排除生成缓存后核验全部实际任务输入与场景哈希。

完整skill为历史参照，本轮为前瞻补跑，非同时随机双组，主机/模型时间不受控；3个开发seed不得宣称泛化或因果证明。首场已启动，其他两场由串行监督器继续；尚无本轮结果，状态/报告见实验目录。

### 2026-10-24：GROC-012设施自主回归失败，GROC-013视觉选点与提前停止

单轮固定v3 bundle5fd7031b、gpt-6.1-sol low、301/seed0及原任务/评分/预算，只有声明的设施候选变化。模型两次导航成功，首次grasp选中红色车身板件返回fixed_object后直接finish blocked；3动作、1失败、导航2.780米、独立3/12。未执行旁放，不支持候选设施因果效果或skill收益声明。原结果保留，没有自动安排第二模型cell。

99运行时src/skill文件匹配冻结清单；正式收尾、调用对齐、录像完整性通过，整体audit=false包括真实任务失败。四路末尾RGB显示其他可见地板，但恢复尚未经物理验证；GROC-013记录已确认事实与限制。当前skill已有换位指导，下一步审阅实际skill读取/错误后的决策，再决定是否值得做特定fixed_object恢复候选；本轮未改策略或晋升版本。证据：experiments/groc012_autonomous_facility_regression_20261024/outcome_review.json及workflow_efficiency.json。

### 2026-10-24：GROC-013 恢复skill触发候选（未采用，未跑模型）

实际读取记录：模型只读long-horizon-planning、visual-manipulation、pick-and-place；未读visual-exploration/failure-recovery。现有system prompt已有换位恢复及禁止仅因早期错误blocked的指导，不能把失败归为没有任何指令；这里只确认主流程到恢复skill的实际触发缺失。是否更强指针能改善行为仍是假设。

独立冻结experiments/skill_recovery_routing_candidate_20261024，仅修改visual-manipulation第2步：搜索被遮挡物前读visual-exploration；目标选择错误（如fixed_object）后，在选择恢复或finish blocked前读failure-recovery。保留原恢复预算/停止条件，未强制重试不可恢复错误，未改任务/评分/执行器或仓库接受版skill。主体恢复规则不再重复添加。完整manifest差异只有该文件，quick_validate及真实SkillLibrary打包通过；结构检查不证明行为收益。

候选bundle 75159478e08409edfba0f3b191d47d1c6e2eaee8cb839b65beff44a5fa21325d；基线仍5fd7031b。下一步对同设施基线/候选做预注册重复比较；若回合未遇到相关错误，不将完成率差异直接归因为触发语。需检查实际read_skill、恢复选择、独立任务分数、动作/失败成本及诚实收尾，所有失败保留。尚未启动新模型批次或晋升。

### 2026-10-24：恢复触发语四格受控重复启动

独立预注册experiments/skill_recovery_routing_controlled_20261024：GPU2 candidate/r1→baseline/r2，GPU3 baseline/r1→candidate/r2，两个条件在各卡各一次，交叉顺序；每组两次，同一开发301/seed0。仅visual-manipulation/SKILL.md不同，bundle基线5fd7031b、候选75159478；执行器采用同一未晋升GROC-012候选，任务/原生评分/模型gpt-6.1-sol low/120动作/20000步/14400秒等配置相同。上轮3动作blocked保留为先验诊断，不混入预注册四格分母。

主指标独立全任务成功；同时检查相关选点错误后是否实际read_skill恢复资源、后续动作与恢复成本、早停和虚假完成。未触发相关错误的回合不能证明触发语作用，n=2不作统计显著性/留出泛化结论，共享主机时延不受控，早停也不能当动作效率收益。任务失败保留继续原计划；基础设施失败保留并停止对应lane；四格后不追加cell。

supervisor GPU2=4122093、GPU3=4122094均启动，独立appdata、按卡互斥/20000MiB门限，清除错误GPU覆盖变量。来源manifest和单文件差异已核验，真实skill加载及模型/档位仍须逐场运行核验；尚无成绩，不晋升候选。


### 2026-10-25：恢复触发语四格结束，未证明收益，不采用

四格监督进程均已结束；独立原生评分：candidate/r1 2/12、54动作/17失败；baseline/r1 3/12、71动作/18失败（86次motor请求）；baseline/r2 4/12、36动作/9失败；candidate/r2 2/12、21动作/3失败。两条件全任务成功均0/2，均正式报告blocked。早停动作少不能作为效率收益；同一开发实例不能支持迁移或统计稳定性结论。

实际资源交付揭示重要限制：候选两轮均未成功读取被修改的visual-manipulation/SKILL.md；基线两轮读取了原版。四轮均未读取failure-recovery。五次被拒read_skill调用均仅提交decision、缺name/resource；错误反馈明确列出必填字段。当前只确认模型提交参数不完整，尚未完成工具定义交付审计，不据此放宽协议或宣称执行器无缺陷。候选文本未被交付，不能把两组成绩差归因于触发语；按原计划保留全部四格，无追加场次，无候选晋升。

证据位于experiments/skill_recovery_routing_controlled_20261024：cohort_observation_20261025T050411939011Z.json、skill_delivery_failure_review.json、rejected_skill_arguments.json。整体audit=false包含真实任务失败及未报告achieved；调用对齐、模型自主选动作、无demo、像素协议等检查为true，不能描述为整体审计通过。最终源码核验、录像完整性和具体物理失败审阅仍分别跟踪，未完成事项不算已验证。


### 2026-10-25：仅消融长程skill的三场对照完成

完整skill历史参照3/3成功，目标均11/11；其他四skill保留、仅去长程skill的补跑0/3，seed0/11/22分别1/11、0/11、0/11。平均动作112.3对114.3，平均失败动作15.7对60.7（约3.87倍）。三场均加载冻结四skill bundle，其他skill字节不变、九个规划工具保留，机器人调用前decision检查通过，录像与独立状态重放通过。按需读取不等于全部采用：补跑没有读failure-recovery，不能声称四项全被使用。

协议限制：seed0在103动作后未正式模型收尾，由监督器aborted；seed11有两次读取不存在服务器x资源（均报错）和一次资源枚举（空列表），是机器人接口外的客户端调用。两场原始全调用对齐审计失败保留。剔除明确监督器/客户端额外调用后，三场机器人调用严格对齐。seed22全调用对齐且正式收尾，仍0/11。所有失败保留，不补抽。

结论仅为本组描述性证据支持保留长程skill，不是严格因果证明或泛化结果：历史参照、每组3个开发seed、共享主机及服务时间未控制，并有上述协议偏差。全部关闭skill单场不进入本次分母。本轮未改生产skill、任务或执行器。报告与逐场录像：experiments/hot_drink_long_horizon_ablation_20261024/REPORT.md；核验：final_evidence_review.json。报告中发布链接前缀已纠正为实际发布目录。


四格收尾验证补充：runtime_validation_all_cells.json确认每场99运行时源码/skill文件、gpt-6.1-sol low、planning、bundle及原BDDL一致；四场主录像均完整解码成功并保留SHA256。schema_delivery_review.json匹配四场真实MCP预检与完整冻结目录。GROC-014人工选点审阅确认失败放置后持物状态被模型误记；现有skill已有相关原则，未无证据重复增补。20份导航输入均准确复现不可达，去除动态遮罩仍20/20不可达；静态地图/视觉目标正确性未验证，不声称导航缺陷修好。候选不采用，未追加模型cell。


### 2026-10-25：GROC-014持物状态候选准备（未采用、未跑模型）

冻结experiments/skill_hand_state_candidate_20261025，从完成四格的execution_baseline派生，仅修改long-horizon-planning/SKILL.md第5条；把通用“更新状态”具体化为导航期间沿用最后确认的持物身份、失败放置后保留持物或明确不确定状态、以反馈/新RGB建立释放或掉落，视觉贴近支撑面不等于放置成功。选择该入口是因为上一批四场均实际读取它；不能保证下一批会读取或遵循。未加入任务对象ID、私有选点、坐标或通关路径。其他skill、执行器、任务、评分与预算保持基线。

候选bundle 2c4ac95dea51a26979588f92500f4f9fdb9356b0fd79c0e3a9604e5e86c3142a；单文件差异核验、quick_validate、真实SkillLibrary读取通过，均不证明行为改善。下一轮若运行，独立登记新cohort，并检查实际交付、失败放置后的手状态、任务独立成绩及恢复成本；不追加到已完成四格，不替换仓库接受版。

导航进一步审阅：20目标画面索引及4组RGB/静态地图图保存在上一cohort。15次无有界投影，5次无可达候选；所有hint距静态可达单元最近距离0.512–2.021米，超过该批0.5米边界。画面包含玻璃门外、狭窄瓷砖凹区、楼梯下、桌椅附近及门框边界点；未验证全体静态地图物理正确性，不以此放宽导航容差或宣布地图修复。


### 2026-10-25：持物状态四格对照启动（无结果，候选未采用）

独立experiments/skill_hand_state_controlled_20261025，301/seed0，planning，gpt-6.1-sol low（沿用用户指定，覆盖实验指南默认模型）。GPU2 baseline/r1→candidate/r2，GPU3 candidate/r1→baseline/r2；各条件两次，同一未晋升GROC-012设施、BDDL、scene overlay、120动作/20000sim steps/14400秒、原生评分及prompt。与上一恢复路由cohort分开，不合并分母或追加场次。唯一差异long-horizon-planning/SKILL.md第5条，baseline bundle5fd7031b、candidate bundle2c4ac95d。

主指标全任务独立成功；次指标包括实际读取、失败放置后持物断言与反馈是否一致、恢复及冗余成本、早停/虚假完成。未读或未触发失败放置的场次仍计总成绩，但不能证明状态规则效果。小样本开发实例，无统计稳定收益/泛化声明，早停不算效率提升。候选不自动晋升；四场后不追加，真实任务失败保留继续预定场次，基础设施失败停对应lane诊断。

supervisor GPU2=1923466、GPU3=1923467；首次实查两者、launcher1923473/1923472、simulator1923485/1923484均存活。尚在初始化，bundle/model/GPU实际进程成员关系等待运行时核验，不把启动等同模型已运行。全部输入manifest已固定，设施证据哈希门禁通过，完整日志/视频和审计随场保存。


### 2026-10-25：按用户新要求直接修补skill，不另做专门对照

本条覆盖此前“冻结skill开发”的工作安排：用户允许依据判断修补skill，明确不必专门花时间做实验验证。执行器和benchmark设施仍为优化重点。AGENTS.md已同步该约定；已启动hand-state四格仍保持原冻结输入，不追加专门skill实验。

仓库直接应用三处窄补丁：long-horizon-planning明确导航沿用持物身份、失败放置后需核实释放/掉落（GROC-014）；visual-exploration明确容器外表面接近不保证内部物品可达（GROC-008）；failure-recovery将fixed_object像素检查扩展到墙/框/支撑面和细物体空隙。无私有任务ID、坐标或解法注入。三个quick_validate通过，效果标记“工程判断采用、行为未验证”，没有成功率或效率收益声明。

完整Markdown skill快照/逐文件哈希及修改记录保存在experiments/skill_practical_repairs_20261025。对当前运行cohort的baseline/candidate全部冻结清单重新核验一致；新补丁不会影响这批成绩。后续以正常任务运行反馈迭代，不为此单独安排技能消融。
