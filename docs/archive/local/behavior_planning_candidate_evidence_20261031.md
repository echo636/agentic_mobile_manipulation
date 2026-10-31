> 历史开发记录：保留原实验协议与结论。当前代码入口和执行语义以仓库 README、docs/code_organization.md 为准。

# BEHAVIOR 长程任务候选：规划结构的源码证据

日期：2026-10-31（Asia/Shanghai）。范围：只读本地任务定义、执行器和上游状态/转换源码；未启动仿真或模型回合，未修改任务、技能、上游或评分。本笔记服务于“筛选真正有规划结构的长程任务”，不把短关门练习当作主研究任务。

## 先区分三件事

1. **有很多动作**不等于**有跨子目标规划依赖**。多个独立物品依次搬运主要测执行稳定性、计数和搜索；需要查看任务的 `init/goal`、实际动作前置条件、对象转换规则，才能说明顺序/资源/汇合关系。
2. **终态关系**不等于**强制操作顺序**。例如 `inside(camera, box) ∧ inside(box, car)` 并不逻辑强制“先装相机再搬箱”；是否先搬空箱会多一次长途往返，要由具体场景的距离、可达性与单手限制确定。
3. **任务注册**不等于**实例可执行**。本地 `tasks.json` 有 101 个条目，包含自建 `prepare_and_serve_hot_drink`；主调查代理已于本日从官方 `task_data.json` 校对，其余 100 个 ID 与当前 Challenge 100 个任务一致。下文“Challenge”指这组 ID，不代表其全部已通过本项目实例准入。可复查[注册表](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L1)。

### 当前动作接口提供的硬约束

- 当前原子动作名单包括导航、抓取、容器/表面放置、开关、倾倒、清洁、浸湿、切割和等待；**没有 `fold` 或 `drape`**。[primitives.py L2–5](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/primitives.py#L2)
- 单手占用禁止再次抓物；持物时不能开关另一个物体；向已关闭的可开容器放置会得到 `container_closed`。这些是可用于构建真实资源/访问依赖的执行器前置条件。[omnigibson_rgb.py L1412–1434](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/omnigibson_rgb.py#L1412)
- `cut` 要求手持有 `slicer` 能力的工具，目标必须支持切分/切碎；当前优先 `sliceable`，否则才选 `diceable`，因此不能把一次 `cut` 等同于必然得到 diced 产物。[material_actions.py L129–157](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/material_actions.py#L129)
- 原生 `DicingRule` 在目标所在位置生成物质粒子，然后删除原刚体；若目标已经 cooked，生成 cooked-diced 系统。它没有“自动把成品放进正确碗”的语义。这支持“接料位置与后续用途应提前考虑”的规划假说，但还要验证当前具体资产/粒子协议。[transition_rules.py L1018–1034](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson/omnigibson/transition_rules.py#L1018)
- 清洁要求持有兼容工具且满足对应材料的 readiness；`soak` 也要求兼容可吸收工具与实际存在的材料来源。不能把存在 `wipe/soak` 名称当成任意污渍均可清除。[material_actions.py L27–91](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/material_actions.py#L27)、[L93–127](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/material_actions.py#L93)

## 候选总表

“结构强”表示源码展示了值得测的相互依赖，不表示已有长期规划增益证据。优先级综合结构和工程成熟度，保留不适合当前动作集的反例。

| 候选 | Challenge 注册 | 核心结构 | 本次判断 |
|---|---|---|---|
| `canning_food` | 是 | 取容器/材料、切割转换、两类产物互斥分配、回柜与关门 | 最值得优先做可执行性审计；粒子操作风险高 |
| `make_pizza` | 是 | 多个加工分支汇合为配方、生成新对象、共享刀与接料/烤盘 | 规划语义最强之一；配方与当前执行可达性必须先核对 |
| `can_meat` | 是 | 两个嵌套容器、每罐精确数量、单手、内外关闭义务 | 最有历史真实执行证据的结构化起点；不应只测记得关门 |
| `cook_cabbage` | 是 | 两类加工分支汇合到同锅，切割/加热与工具切换 | 中强；不可未经验证写死“必须先切后煮” |
| `freeze_pies` | 是 | 一对一装盒、嵌套搬运、最终冷冻状态与关门 | 中等；任务本身缺少第二项可与冷冻等待交错的工作 |
| `loading_the_car` | 是 | 内外容器组合、跨房间运输、分支装车 | 可测先组合再运输的效率；发现原生关门目标解析遗漏 |
| `cleaning_up_plates_and_food` | 是 | 承托复合物运输、两路目的地、关闭全部冰箱 | 较低工程门槛的组合运输对照；逻辑依赖弱于加工任务 |
| `washing_pots_and_pans` | 否，broader BEHAVIOR | 5件器具清洁后收纳、共享刷子/水槽/柜 | 工具准备与批处理假说合理；需确认材料条件与状态链 |
| `clean_up_after_a_dinner_party` | 否，broader BEHAVIOR | 厨房取两盒、餐厅分组装剩菜、回厨房冷藏 | 长途批次/容器分配候选；无原生关门目标 |
| `doing_laundry` | 否，broader BEHAVIOR | 清洁、去湿、折叠/悬挂的多阶段终态 | 结构有价值，但当前缺 `fold/drape`，不适合作为立即执行候选 |

## 1. canning_food：转换产物与互斥容器分配

**定义事实。** 冰箱中有一块 steak、一只 pineapple；柜内有两个 bowl；台面上有砧板，刀在砧板上。两种 diced 产物初始为 `future`。目标要求两个产物 `real`；存在一个碗 `filled(steak)` 且 `not contains(pineapple)`，另一个碗对 pineapple/steak 反向要求；所有碗都在柜内，冰箱与柜均关闭。[init L19–32](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/canning_food/problem0.bddl#L19)、[goal L35–68](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/canning_food/problem0.bddl#L35)、[注册 L250–254](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L250)

**确实要协调的关系。** 两个加工分支最终不能混在一个容器；有共享刀、单手和两个有限的接料容器；最终回柜/关门与中途访问存在冲突。切割会替换对象，因此计划不能只记住原食物名，需把中间产物与接料容器绑定并根据观察更新。

**合理但尚未证实的机制。** “先把接料碗摆好，再处理对应食物”应比切完才找容器减少返工/粒子散落；“完成一种材料再切另一种”是否更优取决于接料路径和混料条件。BDDL 未规定必须在砧板上切；当前 `cut` 也没有检查砧板。不能把砧板准备当作已经证实的硬前置条件。混料可能增加恢复成本，但本次没有证明它不可恢复。

**动作与风险。** 导航、开关、抓放、cut、pour 名称覆盖所需操作类别；尚未验证完整的“切分/切碎→收集物质→达到 Filled 而不是仅 Contains→搬运→归柜”链。若初始食物先 slice 再 dice，动作/产物身份也不同。只读源码证据，不称可跑通。

**候选 skill 机制。** 从终态回推产物与容器绑定；在破坏性转换前保证下游接料条件；保留两类产物互斥约束；按手和工具资源选择下一可执行分支。

## 2. make_pizza：多分支加工汇合到新对象

**定义事实。** 面团已在 cookie sheet 上；奶酪物质、四片 pepperoni、两只 mushroom 在冰箱中的不同容器；洋葱在砧板上；刀、碗、烤盘在台面。目标只有 `real(pizza)` 与 `ontop(pizza, cookie_sheet)`，并不枚举所有制作步骤。[objects/init L4–47](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/make_pizza/problem0.bddl#L4)、[goal L50–54](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/make_pizza/problem0.bddl#L50)、[注册 L255–259](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L255)

**转换证据。** 不能拿完整 `pizza` 食谱要求的额外番茄酱、香草和7片pepperoni来断言本任务缺料；同文件有 `simple_pizza`：面团、grated cheese、1片pepperoni、1个half mushroom、diced onion，要求奶酪/洋葱覆盖面团以及固体配料在面团上，使用 cookie sheet 与 oven，生成 pizza。[simple_pizza 规则 L717–769](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/generated_data/transition_map/tm_jsons/heat_cook.json#L717)。

原生 `CookingRule` 会核验可执行配方并累计连续加热步；未配置 timesteps 时默认1，不应凭现实常识把它包装成需要长时间并行烘焙调度的任务。[transition_rules.py L2338–2347](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson/omnigibson/transition_rules.py#L2338)、[L2383–2396](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson/omnigibson/transition_rules.py#L2383)

**规划点。** 切蘑菇、切洋葱、处理奶酪和组装烤盘是可部分独立的分支，必须在有效配方状态汇合后才能生成目标。加工会改变对象类型；烤盘是多个分支共用的最终承托物。

**限制。** 本次读到的是知识库配方与运行时通用机制，未实例化当前场景来证明哪个 recipe 被激活；当前注册指令写“four pieces / two mushrooms”，原生简易食谱数量更少，是否强制全用必须审计，不能靠增加文字规则暗中提高成功条件。粒子覆盖、半物体、切割、配方触发/替换及炉内放置都比纯刚体任务风险大。

**候选 skill 机制。** 显式维护中间产物和汇合前置条件，处理共享刀/烤盘；只有配方条件得到执行反馈支持后再进入合成阶段。

## 3. can_meat：嵌套容器、准确分配和关闭顺序

**定义事实。** 两个 hinged jar 最初在柜里，四根已经 cooked 的 bratwurst 在台面砧板上。目标要求每罐回柜、每罐关闭、每罐恰有两根香肠，以及外柜关闭。[init L14–29](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/can_meat/problem0.bddl#L14)、[goal L32–54](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/can_meat/problem0.bddl#L32)、[注册 L25–29](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L25)

`forn(2)` 是**恰好2**，不是“至少2”：本地 BDDL `NQuantifier.evaluate` 返回 `sum(child_values) == N`。[condition_evaluation.py L267–305](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/condition_evaluation.py#L267)

**规划点。** `开柜 → 访问罐子 → 开罐 → 分配物品 → 关罐/归柜 → 关柜` 是包含选择的访问结构；单手限制要求提前腾手。模型若把“每罐在柜内/关闭”等终态过早完成，会让尚未装载的分支重新需要打开和取放。具体最省动作顺序与罐子能否原地开合/装填相关，不能从 BDDL 断言每个罐都必须先搬出台面。

**历史成熟度。** 仓库记录 can-meat r30 有独立审计的自主成功，但同文明确单一配置只有一次成功、没有满足重复性门槛，并有多次失败动作。该证据说明存在历史可执行路径，不能证明当前提交/所有实例稳定，更不能证明 skill 提升。[demo_reliability.md L67–84](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/docs/demo_reliability.md#L67)

**候选 skill 机制。** 记录每个内层容器的剩余装载、关闭和归位义务，基于访问关系选择最后关闭时机；跨罐分配数量需要持续核验。主效应应是完整多约束成功率与返工，不是单独统计“会不会关门”。

## 4. cook_cabbage：加工与加热分支汇合

**定义事实。** cabbage 与 chili 在冰箱，锅在台面；刀、砧板和炉具存在。两种 cooked-diced 产物初始 `future`；终态要求二者 `real` 且均 `contains(frying_pan, product)`。[init L20–36](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/cook_cabbage/problem0.bddl#L20)、[goal L39–44](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/cook_cabbage/problem0.bddl#L39)、[注册 L215–219](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L215)

**规划点与限制。** 两个产物要汇入同一锅，需安排接料/锅位置、共享刀与热源。目标并未规定先切再煮；原生 DicingRule 会保留 cooked 属性到产物名称，所以“先煮再切”至少在转换代码层面不能直接排除。应检验合法路径，而非把人类烹饪习惯当硬约束。

**风险/skill。** 与 canning_food 共用切割/粒子设施，可作为同能力不同目标的迁移任务：前者应隔离，后者应合并。可以直接检验 skill 是否读取目标关系而不是一律“分开处理”或“一律批量汇总”。热源是否正确影响锅内粒子、加热持续性、观察可辨识性仍待实际验收。

## 5. freeze_pies：配对装盒、嵌套运输与冷冻终态

**定义事实。** 两个未冷冻 apple pie 在盘上，两个 tupperware 在柜里。目标是一对一 `inside(pie, tupperware)`，所有盒子进入冰箱，所有派 `frozen`，冰箱关闭。[init L15–32](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/freeze_pies/problem0.bddl#L15)、[goal L35–52](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/freeze_pies/problem0.bddl#L35)、[注册 L245–249](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L245)

**规划点。** 空盒准备、两组装载、运输、终态热状态形成中等长度链；几何允许时可比较在台面装盒再送入与先放空盒入冰箱再逐物搬送的成本。

**限制与风险。** 本任务没有独立的长工作分支，因此不足以天然证明“等待时并行做其他事”的收益；冷冻耗时与门状态耦合须用实际冰箱资产验证。`Frozen` 依赖温度阈值；`HeatSourceOrSink` 是否要求关闭由实例参数 `requires_closed` 决定，而非所有热源统一强制。[frozen.py L18–43](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson/omnigibson/object_states/frozen.py#L18)、[heat_source_or_sink.py L38–64](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson/omnigibson/object_states/heat_source_or_sink.py#L38)、[L165–174](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/OmniGibson/omnigibson/object_states/heat_source_or_sink.py#L165)

## 6. loading_the_car：组合后运输，但须先处理评分契约疑点

**定义事实。** 相机和网球拍在客厅桌上，箱子在客厅地面，车在车库，机器人起点在车库。有效解析目标要求 `inside(box,car) ∧ inside(camera,box) ∧ inside(racket,car)`。[init L14–22](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/loading_the_car/problem0.bddl#L14)、[goal L25–33](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/loading_the_car/problem0.bddl#L25)、[注册 L70–74](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L70)

**重要静态发现。** BDDL 文件中的 `not(open car)` 放在 `and` 外。`parse_problem` 只对 `:goal` 的 `group[1]` 调用 `package_predicates`。[parsing.py L280–281](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/parsing.py#L280) 本次在该上游 `PYTHONPATH` 下实际调用 `parse_problem('loading_the_car', 0, 'behavior-1k')`，返回的 goal_state 只有上述3个 Inside，没有关闭谓词；本地注册指令却明确要求关闭车尾箱。这是本地定义/指令契约待处理项，未更改文件，也没有将其直接归类为当前真实运行故障。

**规划假说。** 在客厅先把相机装入箱子再运箱，可以减少独立搬相机的跨房间行程；若先把空箱运走，之后仍可能把相机装入车内箱，因而属于成本差别，不是逻辑死锁。单手限制、箱载荷保持与实际距离使效果可测。只有两个运输分支，作为机制迁移/对照更合适，不能单凭名称作为最强长程任务。

## 7. cleaning_up_plates_and_food：复合物搬运和目的地分流

**定义事实。** 两张盘各承托一个 pizza，另有两只 bowl，均在厨房早餐桌。目标要求 pizza 与 plate 的 `forpairs(ontop)`、所有 pizza 在同一个冰箱、所有 bowl 在同一个 sink、所有可绑定冰箱关闭。[init L15–28](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/cleaning_up_plates_and_food/problem0.bddl#L15)、[goal L31–57](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/cleaning_up_plates_and_food/problem0.bddl#L31)、[注册 L20–24](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/tasks.json#L20)

**规划点。** 应识别承托关系，比较搬“盘+食物”与拆开搬运再重组；同时有冷藏与水槽两个目的地，可测减少返回/重复操作。历史记录 plates/food r19 有一次独立审计成功，成熟度限制同 can_meat。[demo_reliability.md L67–74](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/docs/demo_reliability.md#L67)

**不能声称的内容。** BDDL 只检查最终 pizza/plate 配对，不要求始终保持，也不限定原始配对；注册指令“keeping each on its plate”更强。若研究轨迹中保持关系，须单列轨迹指标并说明原生评分不测此项。该任务本身因果链不如加工任务深，适合较稳定的组合运输验证/迁移，不应占据全部主任务。

## 8. washing_pots_and_pans：broader BEHAVIOR 中的工具共享候选

**定义事实。** teapot、kettle、三口 pan 分布在两个台面，均有 stain；刷子在台面，bar soap 在 sink，存在两柜。目标对5件器具均要求 `not covered(stain)` 且进入任一 cabinet，没有关柜门目标。[init L18–37](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/washing_pots_and_pans/problem0.bddl#L18)、[goal L40–78](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/washing_pots_and_pans/problem0.bddl#L40)

**规划假说。** 收集/摆放待清洁器具后持刷批量清洁，或逐件从收集做到归柜，两种日程的工具切换、去水槽补充材料和运输成本可能不同。若先把仍脏的器具归柜，则清洁终态仍未完成且可达性可能变差。BDDL 未要求清洁必须发生在 sink，也未要求所有器具用同一柜；不得凭任务名强制这两项。

**能力与风险。** wipe/soak 可表达相应工具动作，但 bar soap/水/污渍的实际准备条件要查任务资产和原生 material rules；该定义没有显式初始水系统。缺少当前场景注册与完整链路证据，不能立即作为可用实验。它可成为中期工具准备/切换任务，不能因为“洗碗听起来长程”就入组。

## 9. clean_up_after_a_dinner_party：broader BEHAVIOR 的跨房间分装

**定义事实。** 餐厅桌上两只 hamburger、两只 burrito 和一瓶 wine；两个 tupperware 在厨房柜中。目标要求 wine 冷藏；两个 hamburger 同盒且该盒不含指定 burrito；两个 burrito 同另盒且不含指定 hamburger；两个被选盒均进冰箱。[init L16–28](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/clean_up_after_a_dinner_party/problem0.bddl#L16)、[goal L31–63](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/clean_up_after_a_dinner_party/problem0.bddl#L31)

**规划点。** 空盒从厨房到餐厅、装载后再回厨房，是可利用的往返依赖；两盒有类别分配约束；酒瓶不依赖盒子，提供可重排分支。盒的位置和数量会改变路线选择。自然形成“到哪里汇合材料”的规划问题。

**限制。** 原生目标没有关冰箱/柜要求；禁止混放只对指定对类实例写了否定，但因为各组所有对象都被要求在同一盒，正常不重叠容器下仍需分盒。其复杂性主要来自跨房间物流与分配，不涉及烹饪或清洗。与已有 dinner 自建任务的研究范围需由主代理对照，避免重复包装成新类别。

## 10. doing_laundry：结构成立，但当前不能覆盖

**定义事实。** 染污 undergarment 在 hamper，染污 blouse 在地上，有 detergent、washer、dryer 与两个 hanger。终态要求 undergarment 无 stain、未 saturated(water)、folded；blouse 无 stain、未 saturated(water)、draped 到 hanger。[init L19–33](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/doing_laundry/problem0.bddl#L19)、[goal L36–63](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/doing_laundry/problem0.bddl#L36)

**结构与限制。** 清洁/去湿/不同衣物终态确实形成多阶段任务，但 BDDL 终态本身不强制必须逐台走 washer→dryer，湿态生成与去除要由实际状态转换验证。当前无 fold/drape 原子动作；`hang` 的实现调用 attachment，并不等于 `Draped` 布料关系。[omnigibson_rgb.py L1509–1514](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/omnigibson_rgb.py#L1509)

**结论。** 可作为“规划结构好但动作/资产不准入”的明确反例，不推荐把它塞入第一批十任务。布料不是仅加一句 skill 指令可解决的缺口。

## 为什么不把这些任务作为首选

- `setting_the_table`（Challenge）：两个 plate、两个 cupcake、两套刀叉；目标是餐桌上的 plate、cupcake/plate 配对及刀叉 nextto。没有清洗、烹饪、倒饮品，也没有关门要求；可测空间配对/承托运输，但从名字推断“完整备餐工作流”不成立。[定义 L17–54](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/setting_the_table/problem0.bddl#L17)
- `setup_a_bar_for_a_cocktail_party`（Challenge）：目标是瓶、杯、开瓶器、soda 在台面上的关系与冰块入桶，没有开瓶、调酒、倒饮料的状态目标。物体多、空间布局复杂，不自动意味着深因果规划。[定义 L19–71](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/setup_a_bar_for_a_cocktail_party/problem0.bddl#L19)
- `packing_meal_for_delivery`（Challenge）：3个 hamburger 与3个 sack 一对一配对，袋子放到 storage_container 顶上。名称像送餐流程，实际定义不要求跨房间送达；同台面上的配对打包是较浅结构。[定义 L14–38](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/packing_meal_for_delivery/problem0.bddl#L14)
- `cooking_dinner`（broader）：只有一份 frozen lasagna，目标是 hot、not frozen、inside casserole，不能按日常“做一顿晚饭”想象出多菜并行。[定义 L15–34](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/cooking_dinner/problem0.bddl#L15)
- `packing_picnic_food_into_car`（broader）：真实具有装袋再装车的嵌套结构，但其 init 中 muffin 不出现在 goal；筛选时必须按 goal 清点，不得把所有出现物品都当成必需。[init L20–37](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/packing_picnic_food_into_car/problem0.bddl#L20)、[goal L40–84](/mnt/data/home/sunyujie/robot_manipulation/behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3/bddl/activity_definitions/packing_picnic_food_into_car/problem0.bddl#L40)

## 建议交给主实验设计的最小证据包

1. **首个较成熟实例：can_meat。** 用完整嵌套装载任务测访问/手资源/准确分配；先在当前冻结协议确认关键链，而非另造只有关门的短主任务。
2. **主要规划复杂度：canning_food 与 make_pizza。** 两者分别提供“产物隔离”和“多个分支汇合”；先验证 cut/粒子/配方链是否覆盖，避免将仿真缺口算成规划失误。
3. **迁移：cook_cabbage。** 同样的食材/刀/锅设施，目标从分离变为汇合；可检验 policy 是否基于依赖关系而非硬编码菜谱。
4. **较低设施风险的参照：cleaning_up_plates_and_food。** 用承托保持和不同目的地检验组合搬运；成功也不能替代前三类任务的因果证据。

对照应保留同样的物体数量与近似执行链长度，仅改变一个相互依赖因素，例如容器先在源端还是目的端、同类产物必须分离还是允许汇合、终态需要内外关闭还是容器持续开放。若这改变官方任务定义，必须作为明确版本的机制研究实例，原生 Challenge 分数单独报告。要观察 skill 优势是否随着依赖出现而增大，而不是只证明“更长提示有时多做对几个动作”。

本次验证级别：源码与 BDDL parser 检查；没有给任何新候选授予 benchmark-ready、自主成功或 skill 增益标签。
