# 原子动作覆盖审计（2026-10-15）

运行 `scripts/check_atomic_action_coverage.py` 扫描了 BEHAVIOR-1K 的 1,017 个 `problem0.bddl` 定义。当前热饮任务的 `ontop`、`filled`、`contains` 和 `real` 目标均有动作映射，并通过了严格单任务检查：

```text
task_count=1, catalog_size=23, unsupported_predicates={}
```

热饮链条新增了 `pour`（水容器到可填充容器的官方 `Filled` / `Contains` 转移）和 `place_near_heating_element`（OmniGibson 官方加热原子动作），并把已有的放置、材料和切割动作全部加入 MCP 动作目录。

全目录报告仍会标出 BEHAVIOR 中需要专门状态变换器的 `frozen`、`folded`、`unfolded`、`broken` 和 `on_fire` 目标。这些不是热饮任务的缺口，不能被普通抓取/放置动作伪装成已覆盖。
