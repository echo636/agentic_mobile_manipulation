# 实验配置清单

**2026-10-26 决定：** 后续长程对照只采用“有长程 skill＋三个计划/记忆工具”和“无长程 skill＋不开放三个工具”，两组均保留 decision、相同四个基础 skill。本文六种 profile 是代码/历史能力清点，不代表后续六组实验。下文旧的 skill-only 消融方法只解释历史与其他研究问题。回放必须遵循[配置标注要求](replay_configuration_review.md)。

核对日期：2026-10-25，Asia/Shanghai。依据当前工作区的工具目录、prompt、runner、harness，以及已冻结的实验记录。本文记录配置，不宣布新默认值；历史回合以自己的源码快照和实际请求为准。

## 1. 数量和边界

当前代码明确支持 **6 种 agent profile**。完整实验不是只选 profile：还需要固定下文的 9 个配置维度，因此不存在只有六种完整实验组合的说法。支持某个模式不表示该模式已完成真实实验或通过任务。

| Profile | 工具数 | Skill 访问 | 额外要求与适用范围 |
|---|---:|---|---|
| minimal | 4 | 无 | initialize/look/act/finish；用对话历史追踪状态；不要求 decision，无显式计划/记忆工具 |
| skills | 6 | 有 | minimal 加 list_skills/read_skill；先 initialize，再读 visual-manipulation；搜索、搬运、失败恢复时读相关文档；不要求 decision，无显式计划/记忆工具 |
| planning | 9 | 有 | skills 加 update_plan/remember/recall；每次工具调用必须有 decision；首个运动动作前读 long-horizon-planning，维护可修订依赖图；BDDL 输入另读 bddl-goals |
| planning_no_skills | 7 | 无 | 保留 planning 的三个计划/记忆工具、decision 和依赖规划要求；移除 skill 工具、SkillLibrary 和读取指令。用于全部 skill 文档访问的消融 |
| workflow | 13 | 有 | 兼容旧流程：planning 的九工具再加 observe/start_observation/get_observation/cancel_observation；无 planning 的强制 decision schema。保留旧 prompt/接口，不作为当前默认实验协议 |
| semantic | 3 | 无 | initialize/act_semantic/finish；目标为名称，执行器内部接近；历史热饮对照，不属于 RGB 自主像素选择与显式导航实验 |

工具数由本工作区 `tool_specs(profile)` 实际导入核对。前三种 RGB 基础工具、planning_no_skills 使用同一 act 动作目录；当前目录有 24 个 primitive，工具数与动作数不是同一概念。

事实来源：[工具目录](../src/manipulation_agent/tools/__init__.py)、[prompt](../src/manipulation_agent/vision_policy.py)、[动作目录](../src/manipulation_agent/executors/primitives.py)。Skill 可用与实际读取分开报告；文档要求也不等于模型实际遵循。

## 2. 启动开关实际如何映射

`run_demo_trial.py` 与 `run_no_demo_trial.py` 共用 profile 选择逻辑：

| 启动条件 | 实际 profile |
|---|---|
| 不开下面的选项 | skills |
| --long-horizon-planning-skill off | skills，仍有普通 skill |
| --long-horizon-planning-skill on | planning |
| 上一项加 --without-skills | planning_no_skills |
| --semantic-magic | semantic，仅允许热饮任务，不能与 planning on/without-skills 同用 |

这两个 wrapper 不直接提供 minimal/workflow 选择。底层 controller 接受六种 profile；服务器也必须配置相同目录，不能只改 controller 参数。

`--high-level-harness` 不负责选择 profile；它影响高层热饮执行、评分和结束监督。函数参数仍保留此名字，但 profile 选择分支不使用它。

**命名陷阱：** `--long-horizon-planning-skill on` 不仅添加一份文档，还切换工具集合、prompt 和必填 decision。不能把 on/off 的结果直接归因于长程 skill 文档。

## 3. 每场必须固定并记录的 9 个维度

| 维度 | 可变条件 | 核对要求 |
|---|---|---|
| 1. Agent 合约 | 上述六 profile、实际 prompt、tool schema、decision | 冻结模型实际收到的内容；工具集合与服务器一致；不能只写“有 skill” |
| 2. Skill 处理 | 全部可用、全部关闭、仅移除长程 skill、版本/参考文件 | 保存 bundle/hash/快照、实际 read_skill。仅移除长程 skill 是历史冻结处理，不是第七个 profile |
| 3. 模型客户端 | 模型、effort、provider、CLI/直接 Responses、客户端版本 | 当前用户指定 gpt-6.1-sol low；显式参数覆盖脚本/原子动作指南的 gpt-6-astra 默认；provider 名称不是模型身份证明 |
| 4. 任务与输入 | 公共原生 BDDL、自定义原生 BDDL、高层热饮；自然语言/BDDL 输入 | 分别冻结 instruction、BDDL、scene/instance/seed、资产 overlay。私有 BDDL 初始化不等于把 BDDL 文本交给模型 |
| 5. 观察和执行 | 四相机尺寸/俯角、类别反馈、ideal motor、demo_motion、semantic_magic、CPU/GPU、导航/放置版本 | 区分模型输入与执行器私有几何。成功抓取的类别反馈须披露。demo_motion、semantic_magic、高层 harness 是不同开关 |
| 6. 评分 | 原生 evaluator vs 高层热饮 11 项 | 独立整任务评分；goal_options 取最佳等价绑定。自定义原生任务不称公共官方任务成绩；高层热饮不称原生粒子/接触成功 |
| 7. 预算 | act/look、总调用、仿真步、单动作限制、墙钟、初始化/收尾 watchdog | 使用本轮实际预算；不能将 wrapper 默认 80/20000/3600 秒套到所有回合。unbounded 仅移除整场墙钟截止 |
| 8. 结束与恢复 | native 接受 blocked、高层结束守卫、clock_claim_correction_v1、same_thread/fresh_thread、次数 | 记录第一次正式 finish、每次拒绝、监督器收尾。无 finish 续接与正式 blocked 后强制继续是不同情况 |
| 9. 线上 schema/源码环境 | unchanged_bytes vs strict_mcp_tools_v1，源快照、上游、环境、GPU UUID | 记录实际 API schema/strict、代理源码和请求审计。本地 required 不等于线上 strict；不得代填模型参数 |

runner 当前会复制父环境，未显式清理的 MAS/OG 变量可能继承。不能从命令缺少某开关推断它未生效；缺失历史环境值应标为未知。完整开关级复现清点另见[热饮复现清点](hot_drink_reproduction_inventory_20261025.md)，不能将其历史环境直接套到杂货。

## 4. 结束协议的实质差别

| 条件 | 实际规则 |
|---|---|
| 原生杂货，未启用额外纠正 | 接受正式 blocked；不向 achieved 返回私有目标完成校验 |
| high_level_harness | 有预算且无指定不可恢复错误时拒绝 blocked；模型 aborted 受限；achieved 未通过私有整任务校验时拒绝，不返回逐项目标 |
| MAS_REJECT_FALSE_TIME_CLAIMS=1 | 显式虚假时间/会话耗尽声明，在各项预算可用且剩余超过 600 秒时纠正；具体技术 blocked 仍可接受；不是全面提前退出守卫 |
| demo_motion 历史行为 | 默认已有部分虚假时间声明纠正；不能把“原生未开额外开关”的描述套到 demo |
| unfinished recovery | 模型文本结束却没正式 finish 才续接；same_thread 保留原对话，fresh_thread 是旧的新对话协议；不代表环境重置 |

这些规则和评分分开版本化。被守卫拒绝后继续动作不能证明模型自主坚持，私有 achieved 校验也是额外反馈。

## 5. 已有实验中的三种 skill 问题

| 问题 | 正确处理 | 不能混称 |
|---|---|---|
| 全部 skill 文档是否有帮助 | planning 对 planning_no_skills，固定共同工具/schema/decision、动作提示、任务、设施、预算和结束规则 | 不用 planning 对 minimal 归因于 skill |
| 长程 skill 是否有增量 | 保持 planning 的九工具与其他四 skill，移除长程文档及其专属读取要求；历史热饮六场使用冻结的两套源码 | “无长程 skill”仍可访问其他四 skill，不是 planning_no_skills |
| 增加显式计划机制是否有效 | skills 对 planning 是复合处理：工具、decision、提示和长程文档都变化 | 只能称规划配置整体对照，不能单独归因于文档 |

这些是配置解释，不是授权启动新的 skill 对照。按用户当前要求，skill 做针对性修补，不专门花实验验证；普通任务实验如实记录行为结果。

历史六场及冻结来源见[热饮复现清点](hot_drink_reproduction_inventory_20261025.md)。旧 task-decomposition 开关仅在当时冻结源码中解释；当前 wrapper 不支持该名字，不算第七种当前 profile。

## 6. 最新 GROC-026 实际配置

- 自主回合：`../experiments/groceries_v2_groc026_boundary_gpt61sol_low_20261025_r1`（相对仓库父目录）；冻结批次为 `../experiments/groc026_boundary_feedback_regression_20261025`。
- gpt-6.1-sol / low；Codex CLI 0.157.1；provider api.gpt.ge；planning 九工具，每次必填 decision。
- 五 skill 可用，bundle `f7a1993ac9dfabe73e09667e517b14f19d07e8f6a88272d04496cba6f4b64d8f`。实际只读 long-horizon-planning、visual-manipulation、visual-exploration；未读 pick-and-place/failure-recovery。
- 自定义杂货原生 BDDL；模型收到自然语言任务；house_double_floor_lower / instance301 / seed0；货物偏移 [0.08,0]m；BDDL `b38c56b63e8df759072e2736d1471e051b0d7392c1f744743d3d7757c3348da1`。
- 四固定 RGB，512×512；ideal motor，CPU dynamics，GPU2 渲染；demo_motion=false，semantic_magic=false，high_level_harness=false；非导航瞬移、非放宽导航。
- 120 act/look，20000 仿真步，14400 秒墙钟；无 finish 续接 same_thread，最多3次。线上 strict_mcp_tools_v1；clock_claim_correction_v1；无全面 early_blocked 守卫、无私有 achieved 目标反馈。
- 模型不可访问仓库 AGENTS.md、shell、reset、evaluator。模型输入未包含人工探针的路线、动作或像素。
- 正式 blocked；8动作、636仿真步、独立3/12，无净目标变化。时钟纠正触发0次。人工三步恢复探针独立归档，不计本回合成功。

## 7. 启动前与交付时检查

启动前：明确问题和唯一处理变量；写全九维配置和预定回合数；冻结任务/源码/skill/prompt；核对实际 MCP catalog 与线上 schema；检查继承环境和资源。配置不全时补齐记录，不从旧指南默认值猜测。

交付时：核对 controller/run/传输/源码快照；报告 skill 可用与实际读取、首次 finish 和全部拒绝、独立评分、调用对齐和连续视频；失败同样保留。遵循[运行指南](experiment_handoff_guide_20261014.md)及适用的[原子动作要求](atomic_action_experiment_requirements.md)。私有脚本探针与自主回合分开。

历史时间更正：`89a3353` 是 2026-10-19 的 planning profile 提交记录；运行指南已记录 2026-10-18 使用规划 skill 的回合，故不能将提交日期断言为首次使用日期。工具实现提交为 2026-09-30 的 `8c28875`。
