# 与远端 main 的差异及协作入口

比较基点：`main@1174b3b8c1bf947b9d33876dbb193cb26f720b85`，2026-10-29合入的仓库整理和长程任务版本。实际工具目录来自各自源码独立导入，完整schema见[MAIN_COMPARISON.json](MAIN_COMPARISON.json)，不是凭函数名推断。

## 可以直接回答“你加了哪些tool”

本地长程实验额外开放 `update_plan`、`remember`、`recall`。但这三个工具的定义文件在新main里仍然存在，且`tools/planning.py`和`tools/memory.py`与热饮冻结源字节相同。主要差异是**开放哪些工具、配套skill、参数协议以及执行器**，无需另造三个同名实现。

| 项目 | 新main | 本地热饮对照及对应实验代码 |
|---|---|---|
| 默认skills工具 | initialize/look/act/finish/list_skills/read_skill/task_notes，7个 | 相同6个基础工具，默认无task_notes |
| 专门planning profile | 未提供 | 6基础工具＋update_plan/remember/recall，9个 |
| 笔记机制 | task_notes：版本化文本、read/write、动作回执检查点 | remember/recall：模型笔记与计划；不是同一数据结构 |
| 调用意图字段 | 此目录没有本地这套强制decision schema | 无长程state/reason/expected；有长程另含active_subgoal |
| 长程文档 | goal-decomposition及working-state参考 | long-horizon-planning及其参考；其他四基础skill另固定 |
| act目录 | 23个primitive，含scoop | 24个primitive，额外含boil/place_near_heating_element，没有scoop |
| act参数 | 无本地confirm_bulk_grasp参数 | 含confirm_bulk_grasp及其确认逻辑 |
| 导航与操作关系 | target_locked_approach_v2可在操作内自动接近 | 热饮冻结协议要求显式navigate_to，不隐式导航 |
| pour含义 | 原生分份材料/液体转移，结合scoop等接口 | 热饮高层液体状态转移；其他原生实验需看具体执行版本 |
| 任务/评分 | 当前task_designs与原生任务系统 | 热饮高层11目标、杂货自定义任务、野派原生研究实例分别固定 |
| 预算 | main的新实验默认已改为unbounded | 本轮热饮历史值120动作/20000步，不能被新默认值覆盖 |

`boil`是`act`的primitive，不是独立MCP工具。枚举中存在某个primitive，也不意味着每个原生任务都实现了相同效果。

## 代码差异规模

按src/skills/scripts/tasks或task_designs/tests/docs比较，当前本地开发捕获与main有49个同路径文件相同、116个同路径文件不同，191个路径仅在本地捕获、285个仅在main。路径差异包含main的目录重组，并不等于新增/删除同等数量的功能。

重点检查：

- 工具与协议：`tools/__init__.py`、`tools/action.py`、`vision_policy.py`、`vision_harness.py`、`clients/`及严格传输代理。
- 执行器：本地`executors/omnigibson_rgb.py`、`executors/gt_navigation.py`、抓放/载荷/异常边界；main已拆出navigation及多种兼容层模块。
- 场景/评分：`scene_geometry.py`、`high_level_hot_drink.py`、本地`tasks/`与main的`task_designs/`。
- 原生任务候选：野派v68与当前本地维护工作树也不同，不能仅合并维护工作树就认为已带上最新野派候选。

当前分支把这些版本置于归档命名空间，并提供恢复入口，没有替换main的活动工具、最小干预执行规则或任务设计。后续移植通用修复应按具体问题选取补丁，并分别验证新main原生协议与历史高层协议；整个旧执行器文件直接覆盖会改变行为和评分边界。
