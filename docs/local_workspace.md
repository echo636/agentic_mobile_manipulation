# 本机唯一开发仓库与历史运行

2026-10-31 完成工作区收拢。唯一开发入口为：

```text
/mnt/data/home/sunyujie/robot_manipulation/agentic_mobile_manipulation
```

开发分支 `feat/long-horizon-tasks` 跟踪同名远端；本次获取并在提交前再次确认的远端版本为 `10e735b189f376bf6b82df0c6910999289124e2b`。当时远端 main 为 `1174b3b8c1bf947b9d33876dbb193cb26f720b85`，已包含在该分支祖先中。

## 目录用途

| 位置（相对工作区根） | 用途 |
| --- | --- |
| `agentic_mobile_manipulation/` | 唯一活动源码；当前模块图见 [code_organization.md](code_organization.md) |
| `experiments/` | 原始回合、冻结源码、视频、诊断证据；路径不变 |
| `review_sites/` | 已发布回放；路径不变 |
| `behavior_challenge_sunyujie/` | 独立参考仓库、BEHAVIOR/OmniGibson 依赖；未修改 |
| `archive/worktrees/` | 锁定的历史工作树，用于原协议复现 |
| `archive/consolidation_20261031/` | 整理前源码包、清单、冲突决议、验证日志 |

三个旧开发目录已移动到归档：

| 原路径名 | 归档目录 |
| --- | --- |
| `agentic_mobile_manipulation_7f388e4` | `archive/worktrees/local_experiments_20261031` |
| `agentic_mobile_manipulation_hot_drink_pr` | `archive/worktrees/hot_drink_pr_20261031` |
| `camera_rig_fastmcp_base` | `archive/worktrees/camera_rig_base_20261031` |

原位置仅是兼容符号链接，没有另一份活动源码。它们维持现有服务与历史绝对路径；归档父目录另有旧脚本所需的 experiments/参考库等相对路径链接。新开发从唯一入口开始。实验目录内本来就冻结的其他 Git 工作树不作开发入口，也没有移动。

## 合并决议

本次先保存三个本地工作区的未提交修改，再以远端模块化实现为基础合并旧开发历史。包含未提交修改的实际合并有 45 个文本冲突路径；逐文件决议见[迁移清单](../../archive/consolidation_20261031/merge_resolution_inventory.json)。之前仅比较两个已提交 HEAD 的 32 个冲突不是最终数量。

| 本地内容 | 处理结果 |
| --- | --- |
| 会话恢复后工具 item ID 重复导致日志漏项 | 迁入 `clients/events.py`；不同 CLI 流采用独立 ID 空间，保留原始事件不改写 |
| CUDA 编号重映射导致 GPU provenance 错误 | 迁入 `executors/omnigibson_backend.py`；按当前进程实际 GPU UUID 成员记录，未知保持未知 |
| MCP 子进程导入了另一 editable checkout | 在 `clients/codex.py` 传递明确的 PYTHONPATH；回归验证选定源码的传播 |
| HTTP 传输追踪、严格工具 schema 与旧 provider 兼容 | 独立保留于 `scripts/transport/`；默认不启用，HTTP 夹具验证请求/回复与敏感信息不落日志 |
| 热饮历史高层评分与逐动作重放 | 保留为 `tasks/hot_drink_state.py`、`reporting/legacy_hot_drink.py`，只做离线分析，不混入当前原生执行器 |
| 热饮、杂货已构建任务定义 | 使用远端已导入的 `task_designs/imported/yujie/`，保持其 provenance 哈希；恢复并核验了热饮三个场景文件 |
| 餐后清台设计候选 | 放入 `task_designs/drafts/clear_dinner_and_store_leftovers/`，仍明确为未构建草案；保留原生目标逻辑回归 |
| 本地 bug 日志、实验选择和设计分析 | 收拢到 [docs/archive/local](archive/local/README.md)，标明历史范围；原始字节保存在快照中 |
| 工作流成本和计划审查 | 使用远端已经适配当前 episode/task_notes 的版本，避免覆盖成旧 DAG 接口 |
| 旧 main 的固定携物偏移、固定停靠距离和墙钟参数 | 由当前携物几何、导航站位和统一 deadline 实现取代；旧差异仍在 main 快照中 |
| 原热饮专用 runner、高层动作路由、research-native 适配及实验性物理绕过 | 随对应冻结运行时归档；不复制回新版公共执行器。需要原协议时使用归档的完整源码 |

同名动作在新旧协议下并不等价。当前远端默认支持 target-locked 局部接近，导航执行使用经过验证的端点位姿提交和原生 settling；早期实验使用显式导航、连续跟随或高层热饮状态。当前 `MAS_LOCAL_MANIPULATION=0` 也不等于恢复整个旧执行器。旧分数、视频与成功结论不因合并而改变；新旧协议不可直接混作相同条件。

本次没有将派对副本中的持物跳过物理、关闭回收桶碰撞、家具类别黑名单或逐帧位姿恢复推广为默认。此前冻结的 party v81 及运行目录没有改动。导入任务定义和离线评分不代表这两个任务已通过当前原生执行器的自主验收；接入范围以 [imported task README](../task_designs/imported/yujie/README.md) 为准。

## 保存与复现

[快照清单](../../archive/consolidation_20261031/snapshots.json)记录原分支、原 HEAD、快照提交、源码 SHA-256 清单和独立 tar.gz 源码包：

- 原主工作区：`archive/consolidation-main-20261031`，快照 `7bc6d04ef828e5cc9fb4cfd5a6e508e4d8617473`。
- 本地实验开发：`archive/consolidation-working-20261031`，快照 `e2e9c20cdd7c900b928f9fd2a9d4f28fccb229f8`。
- 热饮 PR 工作区：`archive/consolidation-hot_drink_pr-20261031`，快照 `1f83dbe9f10aff3d7ea3bb13dd99456a62eed49d`。

当前实验优先使用冻结到该回合的 execution_source。需要历史 runner 时，使用归档中同一版本的 scripts 与 src，并显式设置它的 PYTHONPATH；不要把新 src 配旧 runner，或把旧 skill 配当前动作协议。归档已保留相邻实验和参考库的路径兼容。

## 验证范围

使用本机已存在的 behavior Python，没有安装或升级环境依赖：

```bash
PYTHONPATH=src /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python -m pytest -q
```

整合后结果：**1046 passed，42 skipped，418 subtests passed**。其中 10 项 BDDL 测试在基础命令下缺少导入路径，随后单独启用本机 pinned BDDL 并运行杂货、清台和 overlay 测试：**15 passed，34 subtests passed**。其余跳过项保留原原因，不计通过。

远端基线存在两个旧夹具未接受 placement_roll 参数，以及一个 attachment 旧预期仍要求 setter 内部 physics probe；本次对齐了当前接口，并强化“正常物理步中断开的关节必须返回 postcondition_error”的验证。基线导出目录缺少 Git 元数据导致的两个发布测试失败，在真实 Git 仓库中通过。

本地事件/GPU 修复在迁移前 6 项回归全部失败，迁移后连同客户端检查通过。历史状态审计、传输 HTTP 夹具和模型选点字段保持检查通过。热饮场景恢复按原 provenance 哈希通过。移动后两份工作树共 **696 个文件**与整理前清单哈希完全相同，三个原有服务进程仍存活并指向归档，见[工作区核验](../../archive/consolidation_20261031/workspace_validation.json)。

以上验证是 CPU、接口、静态任务目标和文件完整性证据。本次未启动 GPU 仿真或模型任务，不宣称新执行器的自主任务成功。完整日志保存在 `archive/consolidation_20261031/`。
