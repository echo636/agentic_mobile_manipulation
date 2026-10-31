# 两袋杂货：benchmark 候选卡

状态：研究候选。脚本机制验证与 agent 成绩分别报告；完整自主协议、可发现性和留出布局验证完成后，才能冻结收录。

## 当前状态（2026-10-25，终态汇总）

| 项目 | 已核实结果 | 证据（experiments 下） |
| --- | --- | --- |
| 恢复触发语 / 持物状态两批四格 | 每批两组均0/2成功；持物四场均无初始到最终的净评分进展 | `skill_recovery_routing_controlled_20261024`、`skill_hand_state_controlled_20261025/REPORT.md` |
| 当前执行器普通回归 | 4动作提前blocked，3/12且无净进展；未触发抓放或实际关节操作 | `executor_full_regression_20261025/terminal_review.json` |
| 8厘米位置变体机制 | 22/22私有helper检查通过；确认实际调用体积放置helper；不含RGB发现/导航验收 | `groceries_v2_offset_mechanics_20261025/terminal_review.json`、`placement_protocol_review.json` |
| 8厘米自主位置迁移 | 11动作、2失败、9.843米导航，3/12且无净进展；一次新线程续接；第一袋抓取与门打开有证据 | `groceries_v2_position_transfer_20261025/terminal_review.json` |
| GROC-016公开历史续接修补 | 两轮完整失败保留：22/7动作，均3/12且无净进展，各一次新线程续接；历史交付哈希、视频解码和调用审计已核对 | `early_stop_differential_diagnosis_20261025/groc016_final_reports.json`、`groc016_video_history_review.json` |
| GROC-018提前结束诊断 / 同线程恢复 | 传输诊断13动作blocked；同线程修复回归54动作、11次执行失败，实际恢复原ID、未重置，成功取出butter_cookie但未收纳，原生初末均3/12且目标状态不同；85响应完整，双录像通过 | `early_stop_differential_diagnosis_20261025/groc018_transport_terminal_review.json`、`groc018_same_thread_regression_20261025/terminal_review_v2.json` |
| GROC-018历史应用配置复现 | 冻结历史应用/skill、零偏移，11动作主动blocked、3/12无净变化；24响应完整，调用/RGB/源快照及双录像核对通过；观察器头转发限制保留，不能据此作唯一层级归因 | `groc018_historical_config_reproduction_20261025/terminal_review.json` |
| GROC-018直连复测 | 去除诊断转发器，181冻结文件/8厘米场景/预算保持；4动作主动blocked、3/12无净变化，仍有8次缺参拒绝；双录像通过，排除观察器是唯一原因 | `groc018_direct_provider_regression_20261025/terminal_review.json` |
| GROC-019错误目标反馈 | CPU回归28测试/4子测试通过；完整自主6动作blocked、3/12无净变化，未尝试open而未覆盖新分支；未验证自主收益 | `groc019_target_capability_repair_20261025/review.json`、`groc019_target_feedback_regression_20261025/terminal_review.json` |
| GROC-022工具参数生成约束 | 实际Codex schema为strict:false；真实前缀原格式4次中3次漏参，单改read_skill.strict的4次完整。严格协议候选一轮11响应完整、10工具调用无漏参/错误，仍3动作主动blocked、3/12无净变化；双录像通过。未解决提前放弃，非skill收益或完整成功 | `groc018_argument_deep_diagnosis_20261025/review.json`、`groc022_strict_mcp_regression_20261025/terminal_review.json` |
| 开发 / 独立评估 | 零偏移和8厘米配置均为开发；后者因结果用于harness修补而转开发。独立评估配置需另建 | `evaluation_split.json`（本目录） |

成熟度仍为研究候选。skill允许工程判断修补，不为此专门新增实验；设施修补与策略版本分别记录。上述单次失败不等同场景不可行，私有机制成功也不替代自主/留出证据。

## 当前证据与采用状态（2026-10-24）

本节保留10月24日证据摘要；最新终态以上方10月25日汇总为准，历史配置和分数不变。

| 验证 | 当前结论 | 原始证据（工作区 experiments 下） |
| --- | --- | --- |
| 有无 skill 完整四格 | 有 skill 1/2 全成功，无 skill 0/2；平均动作97/70.5、导航118.03/79.50米。仅同开发实例两次重复，尚未证明稳定收益或效率改善 | `skill_presence_controlled_20261022/four_cell_comparison_reviewed.json` |
| 视觉恢复候选四格 | 原版、候选均1/2全成功；候选未晋升 | `skill_visual_recovery_controlled_20261023/four_cell_comparison_reviewed.json` |
| GROC-012 新旁放设施候选 | 主体边缘补采样通过一次私有旁放及沉降后通路检查；退出到达XY但最终转向耗尽原预算，整条验收未通过，尚未采用 | `groc012_body_perimeter_candidate_20261024/outcome_review.json`、`navigation_budget_review.json` |
| 超时后当前图恢复 | 私有人工选点链通过：超时保留，新增导航240步/57.51秒，最终袋邻接及地面支撑保持；不是模型试验，设施仍未采用 | `groc012_timeout_recovery_20261024/outcome_review.json`、`probe/validation.json` |
| GROC-012 两袋共存与开关门 | 8阶段、12次袋状态检查全部邻接/地面支撑成立；同场景私有机制回归 | `groc012_two_bag_retention_20261024/outcome_review.json` |
| GROC-012 固定v3自主设施回归 | 3动作后blocked，独立评分3/12；首次抓取命中车身，未尝试恢复，未执行旁放。失败保留，候选自主有效性仍未验证 | `groc012_autonomous_facility_regression_20261024/outcome_review.json`；GROC-013 |
| 留出与跨任务 | 独立留出布局未建立；餐后收拾仅设计，不能记作已执行迁移 | [套件覆盖与边界](../../../docs/long_horizon_benchmark_suite.md) |

采用GROC-012设施候选前，已有单次私有退出恢复、退出后袋子关系保持和两袋开关门回归；还须完成其他旁放目标回归及固定skill完整自主复测。新旧设施分别冻结；失败保留，不能合并后归因skill。真实持握状态的失败回滚还须物理检查；离线事务测试只证明其控制范围。细节与失败保留见[修复日志](../../../docs/bugfix_journal.md)。

## 测量目标与任务边界

一个连贯目标：把车库中刚买的两袋食品搬入厨房，按储存条件收纳，归置空袋并关门。测量跨房间探索与运输、容器内物体发现、同类多件清点、可交错的子目标规划及完成核查。食品分类与清空袋子构成真实依赖，不通过添加无关取放凑动作数。

作者侧清单为两盒牛奶、番茄、奶酪和两包饼干。该清单用于场景与评分验收；模型输入仍为 `instruction.txt`、四路 RGB 和有限动作反馈。保持六件食品逐件评分，保留“漏掉同类第二件”这一有效能力失败。

| 配置 | 当前候选 |
| --- | --- |
| 仿真任务 / 场景 / 实例 | `carrying_in_groceries` / `house_double_floor_lower` / 301 |
| 任务定义 | `problem0.bddl`，双柜任选；4 个替代分支，每分支 12 个原子目标 |
| 场景变体 | 纸袋保持宽度与食品大小，袋子高度为源实例的 1.2 倍，消除装袋初态不一致；写入 overlay manifest |
| 初始后备箱 | 当前 BDDL 明确为打开，目标要求关闭；若改为关闭起步，须另立场景/定义版本并验证开箱链 |
| 动作协议 | RGB 选点；显式 `navigate_to`；远距离操作返回 `out_of_reach`，不替模型接近 |
| 结束监督 | 接受模型正式`blocked`；无finish文本结束默认同线程有界恢复。热饮高层会拒绝过早blocked，二者不能按跑满动作数比较持续性；首次finish与所有拒绝单列 |
| 执行与评分 | 理想受控搬运与几何校验；原生物理谓词评分；自定义研究任务，`official_task_success=null` |
| 正式模型回合预算 | 120 次 act/look、20000 仿真步、14400 秒 watchdog；配置改变须另立版本 |
| 开发 / 留出 | 实例 301 是已用于诊断的开发实例；不同 seed 仅验证重复运行，不充当留出布局 |

每包饼干均可进入任一厨房吊柜，也可分柜。两个柜、冰箱和汽车后备箱都须关闭；两只袋子归置冰箱旁。所有目标直接读取独立谓词，工具 `ok` 与模型 `achieved` 不替代评分。关闭目标同样要有反例测试。

## 可重复的机制探针

在仓库根目录运行，输出目录须不存在：

```bash
/mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python \
  tasks/carry_in_and_put_away_groceries/v2_design/run_readiness_probe.py \
  --behavior-root ../behavior_challenge_sunyujie/BEHAVIOR-1K \
  --gpu 3 --appdata ../experiments/appdata_gpu3 \
  --seed 0 --bag-height-scale 1.2 \
  --output ../experiments/<new-probe-name>
```

入口冻结本轮 BDDL、探针和关键执行器源码，构建独立 dataset overlay，记录场景 JSON 哈希、GPU UUID、配置与录像，并以 `result.json` 而非模拟器退出码判断通过。探针使用私有对象几何、直接执行器 helper 和显式机器人重定位；不是 RGB 自主选点，不验证跨房间导航，不产生 benchmark 成绩。

探针分别检查初态六件装袋、静置保持、远距离操作无暗中行走、失败后持握和载荷回滚、两袋暂放后载荷保持、逐件提取与入库、双柜分放的原生整目标评分、开柜反例与完成状态保持。顺序采用私有几何的顶层优先，仅证明存在可行操作链，不提供给模型。

## 已有实证（2026-10-20）

- 相关导航、视觉操作边界、放置和评分回归：56 项测试、17 个子测试通过。
- `groceries_v2_readiness_probe_20261020_r4/r5`：seed 1 / 0 各 22/22 机制检查通过。六件单件提取与原生 Inside 全通过，饼干分柜后整目标为真；分别开任一柜后为假，关回再静置 90 步仍为真。
- [五次探针录像与逐项结果](http://10.130.140.115:8769/groceries_v2_readiness_20261020_index.html)；r4/r5 的不同 GPU 与采样耗时差异不构成受控性能比较。
- 原袋高度的 r1、探针次序不合适的 r2/r3 均保留为失败；r3 最终整目标成立也不能覆盖其单件提取检查失败。
- 探针运行配置与模型成绩分开；当前入口新增源码快照和场景哈希，不追溯声称早期回合已使用这些字段。

## 完整自主回合（2026-10-21）

`groceries_v2_robust_gpt61sol_low_20261021_r1`，gpt-6.1-sol / low、规划 skill 开启、seed 0、无 demo，在本卡配置下自然 `finish(achieved)`；独立原生 BDDL **12/12**，`official_task_success=null`，协议审计全部通过，包括全部路径来自显式 `navigate_to`。105 次 act/look、13586 个 env.step、另 2465 个体积采样物理步，模型执行约 80.6 分钟。

两只较高袋子均经真实模型选点携物穿门；六件食品均有独立 RGB 抓取与入库链，两个牛奶和两个饼干均未漏件。本实例的一条合法可发现路径已有证据。台面袋壁遮挡可通过移至地面解除；牛奶暂放遇到冰箱门遮挡，模型开关门、换位后恢复。15 次超距、1 次误选固定物、2 次导航不可达均保留，不因最终成功抹去。

[完整审阅与录像](http://10.130.140.115:8769/groceries_v2_robust_20261021_index.html)；[实验记录](../../../../experiments/groceries_v2_robust_gpt61sol_low_20261021_r1/experiment_record.md)。当前结果与历史 9/11 的定义、场景和协议不同，不作单变量增益比较。

## 冻结重复对照与当前设施限制（2026-10-22）

固定场景/任务及nav v2执行器的四轮对照已完成：skill v2为10/12、9/12，v3为10/12、11/12；两组均0/2全成功，全部保留。第四轮六件食品Inside全部通过，唯一缺项为bag82 NextTo冰箱；工具旁放验证过该关系，终态却为假，后续独立私有探针已复现开门/关门下 true/false/true/false：袋子基本不动，包含门板的冰箱 AABB 收缩使官方距离检查不通过；这不是历史回合的精确状态回放。仅增加固定主体距离筛选的第一候选采样耗尽，失败已保留；随后改为逐链接包围盒碰撞检查，保留主体距离、官方NextTo与地面支撑检查，bag82和bag81各自的开/关/开/关序列均4/4通过，视频哈希与完整解码通过。该候选已写入自有放置器，真实采样器离线测试先红后绿，相关55测试及7子测试通过，但仍不是已验收设施版本，完整自主回归进行中。它不能被写成漏件失败，也不能以工具ok替代终态评分。

导航成功后操作超距已建立真实规划调用的空网格红色复现，候选执行器增加1.35米物体终点上限，原1.4米操作gate、碰撞和可见性条件保持不变。相关49测试及7子测试通过，仅证明该测试范围，第一项真实导航/抓取对照8/8通过，但旧版也成功，故仅提供回归证据而未复现历史超距；第二个历史坐标附近的边界探针也8/8通过，但两版仍到达同一约1.194m终点，未复现历史失败。保持skill v3的导航候选完整自主回归已启动；另一轮仅更换放置器，分别在GPU3/2运行。候选不是当前benchmark已验收工具版本；两类探针均使用独立冻结源码、独立appdata和按GPU互斥锁，已分配GPU 2/3并行运行。

独立评分、14项完整性检查、预算动作/请求区分、服务错误、连续录像及版本决策见 `../experiments/skill_v3_controlled_20261022/`（相对仓库根目录），[四轮回放](http://10.130.140.115:8769/groceries_skillv3_20261022_index.html)。本任务仍为研究候选，留出布局与设施回归尚未完成。

## 收录前仍需完成

同配置的 skill 候选 v1 复跑 `groceries_v2_skillv1_gpt61sol_low_20261021_r1` 已完成：120 次 act/look、独立 11/12，漏关一个吊柜；六件食品与两袋均到位。模型如实 finish(blocked)。调用、显式导航、视频和底盘检查通过，整体审计因任务失败未通过。它是有效的 agent 预算/收尾失败，不据此放宽关闭目标；相同任务/场景和执行器 hash 已复核。[基线与失败候选完整对照](http://10.130.140.115:8769/groceries_skill_iteration_20261021_index.html)。低矮物体导航分类的GROC-007已有独立修复与探针证据，不能将其外推为全部导航/抓取失败均已解决；新的距离衔接与关系保持调查见当前设施限制。

- 本实例六件已各有真实 RGB 选择证据；继续在重复回合与留出布局检验可发现性、台面/地面暂放和开门遮挡的恢复，不能外推为所有视角都可操作。
- 首个新协议自主回合已通过；补充重复回合，保留失败并分别报告机制问题和 agent 失误。单轮成功不估计成功率，模型必须成功不是收录门槛。
- 本实例两只较高袋子的自主携物过门已通过；其他门型、布局和载荷仍需各自验证。
- 建立独立布局或对象位置组合的留出实例，检查与源实例等价的可观测性和可执行性；每个变体分别冻结配置。
- 隔离底层支撑物先被抓起时的成组搬运限制；检查失败是否可恢复，并明确理想执行器保留关系的边界。

历史失败及修复证据见 [任务失败案例 GROC-001 至 GROC-005](../../../docs/bugfix_journal.md)。旧 9/11 使用单柜和原袋尺寸，与当前场景及协议分开记录。

## 2026-10-22 冻结重复证据

导航 v2（组合 grounding/终点可见性修改）下，v1 skill 两轮为 5/12、8/12；v2 两轮为 9/12、4/12，均未完成。实际技能/执行器版本已核对，动作协议与录像检查通过，整体任务审计失败。v2 一轮错误报告 achieved，另一轮在 80 步提前 blocked；不能按较少动作推广候选。原先 12/12 的开发回合与私有机制通过仍保留，但不代表此配置稳定成功。[四轮完整回放](http://10.130.140.115:8769/groceries_navv2_skill_matrix_20261021_index.html)，详见 skill 迭代记录与失败日志。仍是研究候选，未建立留出泛化。


## 2026-10-23 设施验证更新

placement1b9/nav74bb/skillv3的自主开发回合独立12/12，88预算动作、12412环境步，全部audit检查和录像完整解码通过；另一nav74bb旧放置回合11/12，遗漏cookie86而误报完成。单轮成功不代表稳定成功率或放置器因果增益。结果分别见 `../../../../experiments/groc003_full_regression_20261022/final_goal_review.json` 和 `../../../../experiments/groc010_full_regression_20261022/final_goal_review.json`。

新GROC-011小袋动态障碍检测d57在同初态私有物理对照中通过：静置三件保持、旧导航番茄出袋、候选三件保持。探针源码/失败重试/录像保留，非自主模型成绩。下一批有无skill研究对照统一d57导航与1b9放置器，不改变本卡BDDL、场景或评分；实际前两格协议/源码/skill加载已核验。该新设施版本整轮自主结果及留出泛化仍待验证，任务仍为研究候选。旧记录中“完整回归进行中”为当时状态，以本节及具体实验产物更新为准。

## 2026-10-23 同设施有／无 skill 首轮结果

统一d57/1b9设施、当前BDDL与场景、instance301/seed0、gpt-6.1-sol low、120动作/20000环境步/14400秒：有skill v3独立12/12成功，83预算动作、8次失败、11399环境步；无skill独立11/12失败，77动作、11次失败、8943环境步，漏第二份饼干并误报完成。两格调用/动作完整性与全片录像解码通过。使用skill本轮完成程度更高，但多6动作、17.89米导航和3049.07秒墙钟，不宣称效率改善或稳定成功率。

首轮比较与原始证据哈希见 [first_pair_comparison.json](../../../../experiments/skill_presence_controlled_20261022/first_pair_comparison.json)。第二轮两格运行中，实际profile/skill与执行器源码核验见该目录runtime_source_validation_cells_3_4.json。完整四格、独立留出布局与跨任务迁移尚未完成，本卡成熟度仍为研究候选。新视觉恢复候选保存在独立实验快照，未混入当前对照。

### 2026-10-23：四格对照最终状态

revision3四格已完成，取代上文“第二轮运行中”的历史状态：有skill为12/12与11/12（83、111动作），无skill均11/12（77、64动作）。完整性及录像全片解码均通过；三个失败均漏第二份饼干且误报完成。仍为固定开发实例研究候选，未建立稳定skill收益、留出或跨任务泛化。汇总及原始证据索引见 [four_cell_comparison_reviewed.json](../../../../experiments/skill_presence_controlled_20261022/four_cell_comparison_reviewed.json)；根因范围及造任务检查见bugfix_journal的GROC-003更新。候选skill尚未自主验证，设施与评分维持本组冻结版本。

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
详见[完整审阅与证据索引](../../../../experiments/skill_visual_recovery_controlled_20261023/review.md)。


### 2026-10-25 开发回归补充

恢复触发语固定四格完成：基线0/2、候选0/2全任务成功，分别3/12、4/12与2/12、2/12；候选两场未实际读取修改资源，不能据此确认触发语收益。四场99运行时源码/skill文件、BDDL及模型档位匹配，主录像完整解码通过。GROC-014记录失败放置后误记手状态，仍未修复。20导航失败纯规划重放一致，去动态遮罩后仍不可达；静态地图物理正确性尚未验证。

新持物状态skill四格开发对照已启动，结果待定；仅skill一步规则不同，任务/评分/设施/预算固定。设施仍未晋升，单实例的重复不属于留出迁移；完整benchmark准入及跨实例证据仍不足。证据见experiments/skill_recovery_routing_controlled_20261024及skill_hand_state_controlled_20261025，未用新实验覆盖历史失败。


2026-10-25设施增量：GROC-015自支撑前置校验已在工作树修复，相关15项CPU回归通过；无新版本完整自主成绩。环境/指标分项计时为私有诊断增强，无提速结论。两者均未进入当前hand-state冻结实验，不能把该批后续结果归因于新补丁。skill按用户要求可直接工程修补（skill_practical_repairs_20261025），不追加专门skill实验；行为效果未验证。benchmark研究候选状态不变。


### 2026-10-25：持物状态四场终态与设施回归安排

此前“持物状态四格结果待定”已结束：基线、候选均0/2成功，四场均3/12且最佳绑定满足率未较初始状态改善；全部正式blocked。不可将较少动作当效率收益。四场各187个源码/skill/控制器快照文件匹配，四条录像全片解码通过；结果见experiments/skill_hand_state_controlled_20261025/REPORT.md。

当前准备单次普通执行器完整自主回归executor_full_regression_20261025：原任务/场景/预算与gpt-6.1-sol low保持，冻结当前工程skill补丁与设施源码，获取自支撑拒绝、环境/指标计时和开关门关节诊断的运行证据。不是新增skill消融；多项变化同时存在，不作因果归因，失败不补抽。单轮即使成功也不改变留出/泛化缺失与研究候选状态；实际启动/运行/终态以该目录status.json和真实PID为准，不能把准备目录当已启动证据。


2026-10-25 GROC-026私有链路准入证据：同GROC-024原生杂货301/seed0的门前持袋失败状态，9动作核对重放后，人工RGB选点3步完成近处地面放袋→空手开门→同袋再抓；开门时袋AABB未变且floor支撑成立，三件原内容全程Inside保持。未改reach/scene/score/执行器，证明该已开发状态可恢复，不计自主成功，不代表全实例/跨布局验收。报告experiments/groc026_door_staging_probe_20261025/REPORT.md，后续普通模型结果另记。


GROC-026普通回归终态：仅更新局部距离错误文案，gpt-6.1-sol low，8动作/636步后blocked、初末3/12，未到门前。最后袋身选点1.68156米>1.4米，拒绝正确。16响应正常、无漏参，源码/交付图/双录像核对通过；未证实自主恢复或文案收益，任务仍未准入。详见groc026_boundary_feedback_regression_20261025/REPORT.md。
