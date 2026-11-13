# `agentic_mobile_manipulation` 开发与实验规范

全局目标、benchmark/harness 划分原则与实验规则以上级 `../AGENTS.md` 为准；本文件仅规定本仓库的代码结构、测试方式与工程红线。

## 1. 分支与模块归属

当前开发分支为 `feat/long-horizon-tasks`（上游 `echo636/agentic_mobile_manipulation`）。新改动只在本仓库进行，严禁直接修改 `../archive/` 或 `../experiments/` 下已冻结的 `source_*` 副本。详细文件映射见 [docs/code_organization.md](docs/code_organization.md)。

按上级 `../AGENTS.md` 的划分，源码（`src/manipulation_agent/`）对应关系如下：
- **Benchmark 侧（对所有方法固定）**：
  - `tasks/` + 仓库根 `task_designs/`：任务契约、场景构建与独立评估器（`task_designs/drafts/` 为未构建草稿；新建任务参考 [docs/custom_long_horizon.md](docs/custom_long_horizon.md)）。
  - `observations/` + `executors/`：四路 RGB 采样、像素落点解析、导航与动作执行（精细物理过程可按上级规则简化，但须保留规划约束并做成冻结副本测试）。
  - `references/`：不调模型的脚本参考流程，用于验证任务可达性与测算标准杆指标。
- **Harness 侧（对照项，必须做成带独立开关的组件，默认不改变基线）**：
  - `agent/` + 仓库根 `skills/`：系统提示词、技能加载、历史图像裁剪、任务笔记与规划策略。
  - `tools/` + `runtime/`：公开工具定义、反馈/报错文案、预算控制、结束保护等回合护栏。
  - `clients/` + `mcp_server.py` + `transport/`：外部模型客户端适配与 MCP 通信桥接。
- **运行与统计**：`batch/` + `scripts/`（启动、显存锁与源码冻结）、`reporting/` + `web/`（录像导出、回放网页与指标统计）。

**依赖隔离**：OmniGibson 仿真器依赖只能在 `executors/` 适配层内导入；其余核心代码和单元测试必须在纯 CPU 环境下可导入、可运行，且仿真器操作限定在单所有者线程串行执行。

## 2. 运行测试

使用主机已配好的 `behavior` 环境（系统 Python 缺测试依赖），跑全量测试前必须加虚拟内存上限（防止 `tests/test_episode_deadline.py` 耗尽内存）：

```bash
ulimit -v 16000000 && PYTHONPATH=src /mnt/data/home/sunyujie/miniforge3/envs/behavior/bin/python -m pytest -q
```

- 原生 BDDL 目标测试需额外在 `PYTHONPATH` 中加入 `../behavior_challenge_sunyujie/BEHAVIOR-1K/bddl3`。
- 测试被 `skipped` 不视为验证通过；脚本探测或 CPU 单测通过不等同于模型自主回合通过。

## 3. 协议红线与实验留痕

- **严禁泄题**：模型只接收四路 RGB 快照与公开工具回执；私有几何、任务真值绑定与独立评估器内部状态严禁进入模型反馈或错误提示。
- **先冻结再开跑**：跑实验前将当前源码冻结到 `../experiments/.../source_*`，记录模型与推理档位、任务/场景/源码/技能哈希、开关配置、预算及 GPU，保留所有失败回合。
- **指标与评估口径**：独立评估器判定的“任务成功”须与“控制器正常结束”“录像完整性”分开记录；轨迹长度取 `navigation_plans.jsonl` 的认证路线长度之和，**不可**使用 `ideal_navigation_distance_m`（起止点直线位移）。
- **仓库清洁与安全**：实验产物、录像、网页报告和运行时统一放仓库外的 `../experiments/`、`../review_sites/`、`../runtimes/`，不入 Git；密钥与凭证不入库、不打日志。
