> 历史开发记录：保留原实验协议与结论。当前代码入口和执行语义以仓库 README、docs/code_organization.md 为准。

# 跨任务执行器复用与合并边界

2026-10-31。范围：当前维护工作区、派对试验 execution_source_v81，以及在隔离 bare 仓库获取的 feat/long-horizon-tasks。只做代码阅读与 Git 预合并；未执行实际合并、修改执行器、运行模拟器或认定跨任务回归通过。

## 结论

每个新任务需要验证所用能力，不应默认重写执行器或修模拟器。复用已有抓取、导航、开关门和关系放置能力时，新增内容主要是指令、对象/场景绑定、初终态和评分。第一次引入布料、粒子或新的资产表示，可能需要通用兼容修复；相同机制后续应复用该修复。策略犯错则保留为模型结果。

不同任务的代码可以共存于一个系统，但独立任务目录不能隔离共享执行器、进程全局补丁、环境变量或评分语义。Git 文本合并、程序接口兼容、动作/物理行为兼容和实验可比性是不同的验收问题。

## 具体例子

| 例子 | 当前代码事实 | 跨任务使用判断 |
| --- | --- | --- |
| 杂货允许任选两个厨房吊柜 | 两枚饼干的合法柜绑定和关柜目标写在任务 BDDL | 应作为独立任务定义保留，不应通过放宽所有任务的 place_inside 来修评分。新任务目录通常容易共存，共享注册表仍可能冲突 |
| 杂货低矮牛奶盒被当作地板 | 当前 `_navigate` 要求命中类别为 floor/floors 才进入地板航点分支，已不只看高度 | 适合推广为通用导航修复；仍要回归地板航点、低物体和普通物体，不意味着所有场景均已验证 |
| 热饮初始化与派对原生粒子 | 热饮有按任务名限制的 GPU 初始化后 CPU 兼容流程；research instance 声明 requires_gpu_dynamics 时拒绝 MAS_CPU_DYNAMICS=1 | 两种模式可以同仓共存。把某任务的 CPU 设置改成全局默认，会使需要 GPU 的实例直接拒绝启动或改变物理条件 |
| 派对布料载入修复 | 仅在 research spec 声明 cloth_compatibility 时安装；实际替换 EntityPrim._post_load | 按机制/仿真版本选择，适合复用给受同类资产问题影响的任务。但补丁作用于整个进程的类，不能把文件分目录当作运行时隔离；复用进程须处理安装/恢复，独立进程可避免跨回合残留 |
| 派对禁止抓家具 | v81 `_ideal_grasp` 对类别字符串包含 coffee_table/dining_table/countertop/cabinet/sofa/bed 直接返回 fixed_object，没有按任务配置限制该分类分支 | 在清理任务中可阻止误抓支撑家具；若原样纳入通用执行器，未来合法搬桌子/家具的任务也会被拒绝。这是从代码可直接推导的适用性风险，未运行一个搬家具反例任务 |
| 派对携物跳过物理、回收桶禁碰撞 | v81 `_step` 在 MAS_CARRY_SKIP_PHYSICS 开启且持物时不调用原生物理；v81 placement 在满足分支条件后对 recycling_bin 调用 `_set_object_collisions(held, False)` | 即使 Git 无冲突，也会改变接触、沉降和物理时间推进条件。特别是回收桶分支没有任务名开关。应保持为明示研究执行模式，并单独验收，不能默认为其他任务的通用修复 |

代码位置：

- [杂货 BDDL](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/tasks/carry_in_and_put_away_groceries/v2_design/problem0.bddl)
- [维护版 RGB 导航与操作分支](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/executors/omnigibson_rgb.py)：`_navigate`、`execute_visual`、高层热饮初始化约束。
- [维护版仿真 adapter](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/omnigibson_backend.py)：requires_gpu_dynamics 检查、research 兼容安装与热饮初始化分支。
- [布料兼容安装](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/src/manipulation_agent/cloth_compatibility.py)：运行时 USD 副本与 EntityPrim 类方法替换；原资产文件不修改。
- [派对 v81 抓取](/mnt/data/home/sunyujie/robot_manipulation/experiments/wild_party_runtime_20261026/execution_source_v81/src/manipulation_agent/executors/carry.py)：`_ideal_grasp`。
- [派对 v81 步进](/mnt/data/home/sunyujie/robot_manipulation/experiments/wild_party_runtime_20261026/execution_source_v81/src/manipulation_agent/executors/omnigibson_rgb.py)：`_step`。
- [派对 v81 放置](/mnt/data/home/sunyujie/robot_manipulation/experiments/wild_party_runtime_20261026/execution_source_v81/src/manipulation_agent/executors/placement.py)：recycling_bin_stage 分支。

这里说的“模拟器修复”需要区分：本项目 adapter/加载配置、运行时兼容包装、上游模拟器实现。以上很多改动属于前两项；修改了运行时行为，也不等于修改 PhysX 源码。保留原生目标谓词不等于保留全部原生物理行为。

## 对截图分支的实际预合并

原仓库本地缓存 origin/feat/long-horizon-tasks 为 aaa0675；只读远端查询发现其已经更新。随后在 `/tmp/robot-task-merge-audit-s8t9dgup` 隔离 bare 仓库获取分支，在该仓库运行 merge-tree，原工作区、index、分支和远端缓存引用均未更新。

- 当前已提交 HEAD：89a3353e9e89a9a20d42f229a2450d0a2d6d7a84。
- 本次获取远端 HEAD：10e735b189f376bf6b82df0c6910999289124e2b。
- 公共祖先：d69df3471cfe7d0670af0c2e9462447bcb067c08。
- `git merge-tree --write-tree --name-only --messages <ours> <theirs>` 返回 1，列出 **32 个冲突路径**。
- [完整输出与路径清单](/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation_7f388e4/docs/cross_task_merge_audit_20261031.json)。32 是路径数，包含文档、skill、测试与代码，不是 bug 数。

共享的 carry.py、placement.py、omnigibson_rgb.py 都有内容冲突。旧 executors/gt_navigation.py 则是 modify/delete：本地改了旧文件，远端删去旧位置，远端 RGB 实际导入 executors/navigation/gt_navigation.py 等拆分模块。应把仍适用的行为迁移到新的真实调用路径，直接保留旧文件可能留下无人调用的实现。

这次只比较已提交树。维护工作区有大量未提交和未跟踪内容，派对 execution_source_v81 也不等同于该 HEAD；这些内容没有进入此预合并，故 32 不能当作最终整合工作量或全部冲突清单。

## 建议的整合方式

统一平台按能力承载通用导航、抓放、关节操作与错误反馈；任务包承载对象绑定、初终态、合法解和独立评分。GPU/CPU、原生/高层、仿真版本兼容与诊断性绕过通过有名称、有版本的执行配置选择。任务中的特殊资产问题按明确条件处理，避免按任务名无限堆分支，也避免把局部绕过变为全局默认。

整合时先固定目标分支和基准，再逐项移植本地未提交修复与试验副本差异；文件重构按真实调用路径迁移。每一项需说明改变的能力、适用条件、未启用时的行为，以及受影响任务的回归范围。

最小跨任务回归建议覆盖：普通刚体抓放、地板/低物体导航、开门后携物通过、放置后退出与关门关系保持；若改粒子/布料，再加相关材料链。需要自然物理推进的任务必须检查没有启用跳过物理/禁碰撞等研究模式。以上是后续验收建议，本次未运行这些测试。
