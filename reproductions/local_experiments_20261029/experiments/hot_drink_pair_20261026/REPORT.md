# 热饮有无长程：执行器修复后的自主对照

两组均自主完成11/11高层热饮目标，首次finish即被接受；动作对齐、独立语义重放、配置与完整视频解码均通过。仅一组开发实例301/seed0配对，不构成总体成功率提升的证明。

| 指标 | 无长程（四基础skill） | 有长程（另含计划记忆工具） |
|---|---:|---:|
| 整任务结果 | 通过 | 通过 |
| 计预算动作（含look） | 106 | 96 |
| 失败动作 | 11 | 7 |
| 动作失败占比 | 10.38% | 7.29% |
| 执行墙钟分钟（不含启动） | 73.62 | 81.43 |
| 工具执行分钟 | 35.79 | 44.83 |
| 模拟步 | 6279 | 8201 |
| 导航次数 | 43 | 45 |
| 失败动作本身耗时/秒 | 18.19 | 10.64 |

有长程比无长程少10动作（9.4%）、少4失败（36.4%）；墙钟多10.6%、工具时间多25.3%、模拟步多30.6%。因此本轮只支持较少动作/失败，未支持更快或全面降低执行成本。两组都成功，成功与否没有区分。

两组共享冻结180文件source（即维护source_v2）、四基础skill、完整外壳v2、任务/场景输入、GPU0 UUID、动作执行器和结束规则。各组实际加载78个Python文件均匹配冻结源，物理环境配置仅回合输出路径不同。新实验按仓库规范使用gpt-6-astra/low，120动作/20000步，无整场墙钟截止，启动900秒/收尾180秒；seed0、先无后有、非随机顺序。GPU3锁被其他会话持有，首回合前留痕改GPU0，未干扰其他会话。

比较的是长程文档＋三个工具＋active_subgoal字段的整体协议。无长程仍有visual-exploration、visual-manipulation、pick-and-place、failure-recovery，意图为state/reason/expected；有长程多active_subgoal，实际update_plan5次、remember2次、recall0次。无长程read_skill7次、有长程8次，读取成功与否以原始记录和配置页为准。请求模型/effort和工具线上strict协议有记录，provider内部模型身份未独立证明。

恢复成本：无长程首次误抓冰箱层板后，6个后续动作才成功取水，工具执行90.38秒；两次明确距离恢复分别再用2动作/31.84秒和2动作/36.44秒。有长程错误倒水落点下一次改选杯壁成功，1动作/2.18秒；茶匙恢复窗口63→74含11后续动作；饼干恢复85→89含4后续动作。不同片段覆盖不相同，不能把这些样例合计当组间总恢复成本；完整目标与时间明细见without_manual_cost_review.json、with_manual_cost_review.json。

冗余审查：自动门动作反向候选无长程0、有长程1；有长程6→7确为误认高柜后的检查/关门复位，有信息增益，未判定为冗余。两组均有下柜遮挡下的重复导航/开门候选；有长程多次单件往返，无长程利用托盘及餐巾组合搬运，仍有较长饼干搜索。有长程为关门额外暂放并取回饼干，属于收尾成本。未给出未经验证的“可省总动作数”。

失败分类：无长程11次=unsupported_relation3、out_of_reach3、navigation_unreachable4、invalid_visual_target1；有长程7次=unsupported_relation4、navigation_unreachable3。复核图片显示多次选中冰箱层板/炉面/柜体而非目标物，不是所有失败都来自执行器bug。两组自主轨迹确实调用新普通柜开关路由，语义重放将其记为环境操作，不虚增任务提交或目标。已查看两组失败对照图和最终四视图；宽视图小物体细节仍应结合动作局部画面，不能仅凭最终画面证明所有物理关系。

本轮未修改机器人skill或冻结执行代码。现有长程skill已包含路线、暂放、返回成本和地标复用指导；少用组合搬运不直接证明缺少一句规则，不据此盲加重复指令。下一设施诊断优先复现两组共有的柜内目标导航不可达，区分选点/障碍与执行器候选路径问题，真实复现后另冻修复，保持本轮原始记录不变。

本轮没有blocked、premature_finish拒绝、监督补finish或同线程续接；动作减少并非提前失败或等待耗尽预算。旧sol模型的历史失败另列，模型及结束协议不同，不把本轮成功归因于单一补丁。结果为high_level_hot_drink_state_v1，official_task_success=null，不能作为原生BDDL/官方成绩。

证据：plan.json、source_manifest.json、runtime_and_scene_equivalence.json、pair_configuration_audit.json、manual_reviewed_results.json、comparison_final.json及两组run/与controller/。失败图、逐次落点与恢复窗口均保留；审查页顶部展示配置，原始视频完整保留。

线上请求核验：无长程187次、有长程170次请求，全部gpt-6-astra / low / strict_mcp_tools_v1，两组codex-cli 0.157.1；证据wire_identity_audit.json。首页、单场配置/审计/视频链接HTTP200，配置位于视频前，见final_link_audit.json。
