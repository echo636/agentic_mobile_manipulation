# 热饮修复过程与成功结果的归因

本说明将工程修复、策略轨迹差异和最终成功分开。对照对象为2026-10-26旧hot_drink_lh_tools_pair与新hot_drink_lh_repaired_pair；逐文件核对见repair_history_comparison.json。

## 实际修复与证据链

1. SCENE-HOT-001：较早的房间外壳修复。原JSON补回地板仍被运行时房间过滤，实际只加载4/22地板。引入完整外壳加载规则，保留家具房间筛选；complete_static_shell_v2真实GPU验收250/250外壳、22/22地板碰撞与取景、厨房→客厅→厨房537步及录像通过。证据../hot_drink_scene_shell_fix_v2_20261025/REPORT.md；详细历史在维护库docs/bugfix_journal.md。注意上一轮失败对照已使用v2，本轮没有再次修改场景，不能用这一较早修复解释这一次跨轮结果差异。
2. HOT-HIGH-001：修正错误反馈。烧水前置条件invalid_precondition原被公开边界改成execution_error，unsupported_relation提示只说放置/附着，empty_hand提示遗漏倒水等操作。现在保留正确错误类型，使用动作通用且不泄漏私有状态的提示。原真实错误→红测试→修复及边界测试保留于旧对照analysis_feedback_repair；纳入新维护冻结源。它改善反馈准确性，未增加动作能力，也没有单独的自主A/B证明其效果。
3. HOT-HIGH-002：补普通柜门的高层路由。旧高层只接受任务绑定对象，实际支持开关的普通柜门在执行前被invalid_visual_target拒绝。先在同场景回放原前5动作，重现第6次拒绝，再用同位置/像素验证原生open/close；8动作388步。随后补按Open能力、空手、所选点1.4米距离检查调用已有执行器的高层路径，无隐式导航。真实路由红测试4失败，冻结source_v2回归96测试/4子测试通过。再用全程保持高层模式的真实act验证：7动作均成功/388步，柜门关→开→关，任务状态不变。独立审计把这类操作记为环境操作，核对绑定、图像、像素和状态摘要，不虚增任务完成。两场自主回归各有4个该类环境操作，语义重放均通过。证据../hot_drink_benchmark_repair_20261026/PROBE_REPORT.md、INTERFACE_REGRESSION_REPORT.md、environment_route_v2.patch、router_red.log、frozen_v2_green.log及两场探针完整日志/录像。普通地面暂存和任意非任务物品抓放没有在此补丁中扩充。
4. PROTO-HOT-002：修正blocked收尾协议。旧守卫拒绝预算未耗尽的blocked，历史回合因此有明确“耗预算以关闭”的wait。新默认accept事实blocked，未完成仍独立判失败，保留exhaust_budget以审计旧协议。真实finish边界红→绿和旧协议回归保留。这个修复避免协议强制耗预算；本轮两组都首次achieved成功，没有blocked、拒绝或续接，所以不能把本轮成功归于它。
5. PROTO-LH-002：按用户要求，无长程组只要求state/reason/expected，有长程另含active_subgoal；schema、prompt、记录和HTML同步。两组其他四基础skill相同。这是比较协议修正，不是操作能力修复。

## 冻结与修改范围

新source相对旧对照source有且仅有7个文件不同：scripts/audit_demo_trial.py、scripts/build_review.py、src/manipulation_agent/executors/omnigibson_rgb.py、src/manipulation_agent/observations/boundary.py、src/manipulation_agent/tools/__init__.py、src/manipulation_agent/vision_harness.py、src/manipulation_agent/vision_policy.py。修补过程先冻source，再冻source_v2；本轮完整180文件source与source_v2完全相同。旧源和失败没有覆盖，其他会话的原生research支持另列排除清单。

已核对high_level_hot_drink.py、high_level_visual.bddl、gt_navigation.py、scene_geometry.py和全部skill文件与旧对照相同。两组实际加载的78个Python文件均匹配本轮冻结源，四基础skill和场景输入相同。运行中未修改任何一组执行源。

## 为什么本轮两组都达到11/11

两组均由模型独立选择每个动作与像素，完成水瓶→水壶→boil→杯的操作链，随后把杯、餐巾、餐垫、盘、茶匙、托盘、饼干和蜂蜜送至茶几允许位置。3项水/热饮状态加8项物品摆放构成既有11目标，独立动作轨迹重放与保存状态逐项匹配。模型在失败后继续换位/选点恢复，最终均找到饼干，首次finish即接受。无长程106预算动作（含2look）/11失败；有长程96动作（含3look）/7失败。该评分为high_level_hot_drink_state_v1，official_task_success=null；boil为已声明的高层接口，不是原生热力学成功证明。

成功是修复后完整配置的一次自主结果。跨轮还从gpt-6.1-sol/low改为仓库新实验默认gpt-6-astra/low，结束与意图协议也不同；没有旧模型×新旧执行器的完整交叉对照，不能证明是某个补丁单独把旧失败变成11/11，也不能据单seed证明总体成功率提升。

## 本轮11次失败到7次的具体差异

| 失败码 | 无长程 | 有长程 |
|---|---:|---:|
| out_of_reach | 3 | 0 |
| invalid_visual_target | 1 | 0 |
| navigation_unreachable | 4 | 3 |
| unsupported_relation | 3 | 4 |
| 总计 | 11 | 7 |

这是同一执行器下两条策略轨迹的差异，不是给长程组单独修代码。长程组实际update_plan5次、remember2次、recall0次；无长程保留四基础skill和即时意图。部分少失败来自这条轨迹避免了距离拒绝和部分错误选点，但“为何”还包含模型采样差异，不能把所有差额认定为长程文档的因果效果。长程组合同时改变长程文档、3工具和子目标字段，不是纯文档单变量试验。

代价有反向变化：动作少9.4%、失败少36.4%，墙钟多10.6%、工具时间多25.3%、模拟步多30.6%，导航45次对43次。长程多次单件往返、最终暂放饼干以关门再取回；不能称为全面提效。失败画面、逐次grounding及恢复片段见两组failure_target_inventory.json/failure_contact_sheet.jpg和without_manual_cost_review.json、with_manual_cost_review.json。记录中的恢复窗口为人工核实样例，不能跨重叠或不同覆盖直接求总成本。

## 记录入口

- 总问题日志：/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/docs/bugfix_journal.md。按案例ID及后续终态读，历史“未修复/待验证”文字保留，不代表最新状态。
- skill与版本记录：同库docs/skill_iteration_20261021.md。本轮skill没有新修改，版本随完整source/skill_manifest冻结。
- 设施验收/队列：同库docs/long_horizon_benchmark_suite.md。
- 本轮结果、原始调用、配置和录像：当前目录REPORT.md、manual_reviewed_results.json和两组run/、controller/。
