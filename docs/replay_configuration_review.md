# 实验回放 HTML 的配置标注要求

生效日期：2026-10-26。适用于新增或重新生成的自主实验审查页和比较首页。历史原始证据保持不变；重新展示不补造历史配置。

## 两组实验的明确名称

当前长程对照只有两组：

| 项目 | 有长程 skill | 无长程 skill |
|---|---|---|
| initialize/look/act/finish/list_skills/read_skill | 开放 | 开放 |
| update_plan/remember/recall | 开放 | 不开放 |
| long-horizon-planning 与参考文件 | 提供 | 不提供 |
| 其他四个基础 skill | 相同版本 | 相同版本 |
| 人类审查用 decision | state / active_subgoal / reason / expected | state / reason / expected |

比较的是“长程 skill＋配套计划记忆工具”整体效果。无长程组按用户2026-10-26更新要求记录每次调用的即时意图，不要求当前子目标；该字段差异也是新协议的一部分，不再宣称两组decision schema相同。启动无长程比较时设MAS_REQUIRE_DECISION=1；skills默认兼容模式仍无额外decision。实际schema与prompt必须一致，并在配置中列出每工具的必填字段和public_decision_contract版本。

历史两组四字段decision、planning_no_skills及其他配置保留实际名称和原始证据，不重新归入新协议。新版本为profiled_public_intent_v2；重新生成旧审查页时从保存schema读取字段，不用新默认值推断。

新热饮维护协议默认MAS_HIGH_LEVEL_BLOCKED_POLICY=accept：接受带事实理由的blocked，独立任务评分保持原样，未完成仍失败；不再要求用等待耗尽预算才能关闭。exhaust_budget显式选择历史守卫，run.json记录termination_contract的版本/blocked_policy/early_blocked_guard。改变结束监督后另冻实验版本，两组采用相同结束规则。动作减少须结合目标完成、首次停止和整任务结果解读，不能把提前失败造成的次数减少当作执行能力提升。

## 单场审查页必填内容

HTML 顶部、连续视频之前必须有清楚可见的实验配置区域，不得只在文件名、日志或另一个页面中说明。概要显示组名、实际 profile、模型/effort、是否为自主模型/人工/脚本、任务实例和独立结果；详细内容可折叠展开。

| 类别 | 必须标注 |
|---|---|
| 身份 | run ID/时间、对照组、实际 profile、配置版本；开发实例或留出实例；人工/脚本探针独立标签 |
| 模型 | 模型、reasoning effort、provider host、客户端及版本 |
| Skill | 所有可用 skill、参考文件、bundle 哈希；实际读取的 skill/资源；读取失败不能当成功读取 |
| 工具 | 实际开放工具的完整名称与数量；三个计划/记忆工具分别是否开放；schema 版本/哈希、decision 要求及实际记录情况；act primitive 目录版本 |
| Prompt | 原始任务指令、实际自定义 system/developer prompt 全文及哈希；读取 skill 的指令；明确文本覆盖范围，不把自定义文本冒充客户端全部基础指令 |
| 任务与场景 | task/scene/instance/seed、任务定义来源与版本/哈希、场景/资产 overlay 版本；区分模型输入与私有初始化定义 |
| 观察与执行 | 相机方向/尺寸、允许的反馈；ideal motor/demo_motion/semantic_magic、导航与放置协议、CPU/GPU 执行条件 |
| 预算 | 动作/工具调用/仿真步上限、实际执行墙钟上限、初始化和收尾 watchdog、无整场时限标记；实际使用量分开写 |
| 结束监督 | 提前 blocked 是否接受；achieved 是否使用私有完成反馈；时钟纠正协议；无 finish 时同线程/新线程续接及次数；首次 finish、所有拒绝与最终收尾 |
| 评分与来源 | 原生公共任务/自定义原生/高层热饮的评分协议、独立整任务结果；源码快照/hash、GPU UUID、模型调用/仿真记录对齐和连续视频审计链接 |
| 传输 | 实际线上 strict/schema 协议、版本和验证证据；不从 MCP required 或当前脚本默认值推断历史实际值 |

## 数据来源与缺失处理

从本回合的 run.json、controller.json、实际 MCP 预检、模型 API 请求、skill_manifest/快照、事件日志、冻结配置和源码清单取值。HTML 同时提供可下载的 `review_configuration.json`。配置摘要只发布所需白名单字段，不发布认证、完整客户端私有目录或凭据。

“关闭”“未使用”“未记录”“不适用”必须区分；缺失字段写未记录，矛盾字段展示来源差异并标待核对。不得从目录名、当前工作区 profile、最新 skill、默认参数或模型是否调用过某工具，反推历史配置。配置展示本身不代表审计通过。

`scripts/build_review.py` 已从保存记录生成配置区和 JSON，并将长程 skill/三个工具组合标为当前两组或其他历史配置；没有完整线上协议或 prompt 的记录仍明确缺失。页面列出 read_skill 请求，不把请求等同成功读取。仍缺少的审计材料由发布者补取同一回合证据；禁止补猜。

## 比较首页与交付检查

比较首页的每一行显示组名、模型/effort、skill 版本、计划/记忆工具开关、预算、评分协议、结束监督版本及结果，并链接单场完整配置。多场相同项可以在首页固定配置区集中说明，但各单场仍须自包含。历史不同协议、私有探针及自主实验不能合并为一个成功率。

发布前检查：页面配置与原始记录一致；两组差异符合上表；所有其余差异显式列出；配置 JSON 可下载；审查页/视频/审计链接可打开。新对照缺少必要配置证据时标“配置未核验”，不得作为有效组间结论。旧页面允许保留“未记录”，不能靠重建页面变成已核验实验。
