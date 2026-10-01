# Agentic Mobile Manipulation

在 OmniGibson 上研究 **RGB 观测 → LLM 选择动作和对象 → 理想低层执行 → 新观测** 的闭环。

维护仓库：[echo636/agentic_mobile_manipulation](https://github.com/echo636/agentic_mobile_manipulation)。独立代码库，保留本项目开发历史；不修改来源仓库或旧导航环境。

## 当前观测：四个固定相机

- 四路 512×512 RGB：**front（前）、back（后）、left（左）、right（右）**，相对机器人朝向。
- 相机刚性跟随机器人。一次 render barrier 后读取四个 render product，使用共同 capture_id、captured_at 和 sim_step。采集不转动机器人、不调用 env.step。
- 不使用腕部相机；仿真器默认的头部/腕部传感器由四相机研究配置替代。相机位于初始机器人最高点上方 5cm 的固定底盘相对高度，安装半径 35cm，水平视场 90°，下倾 20°。这是明确记录的仿真配置，不宣称真实硬件标定。
- 大脑只看 RGB 和采集元信息；深度、相机外参、对象身份与任务真值只在理想执行器或离线评估中使用。

## 异步观测工具

```text
start_observation({}) → job_id, status=planned（立即返回）
get_observation({job_id}) → planned/running 或 passed + 四张 RGB
cancel_observation({job_id}) → 请求取消尚未完成的任务
```

任务在仿真主线程的后台调度点采集，网络请求和 LLM 推理可以继续。没有并发线程操作 OmniGibson。采集本身是一个不可分割的只读操作；已经开始的 render 不会被中途打断。取消与采集竞争时，以最终状态为准。

每个结果带 `stale`：新动作或新采集后，旧结果仍可查看，但不能作为最新动作目标。当前四个方向中的任何一个 RGB 像素均可供 `act` 选取，无需先转身获取后方图片。

默认 9 个 MCP 工具：`start_observation`、`get_observation`、`cancel_observation`、`observe`、`look`、`act`、`finish`、`list_skills`、`read_skill`。`observe` 是同步兼容入口；`look` 是明确的底盘转向动作，不是环视采集。动作后也返回同一状态的四路 RGB。

四个可读取 Skill：visual-manipulation、visual-exploration、pick-and-place、failure-recovery。暂不引入显式 plan/Stack/memory。Skill 是工作流指导，动作仍由 LLM 通过工具调用。

## 安装与运行

Python >=3.11，CPU 核心不依赖仿真器：

```bash
python -m pip install -e '.[mcp]'
PYTHONPATH=src python -m unittest discover -s tests -v
```

真实执行已使用独立的 OmniGibson 3.9.2 / Isaac Sim 5.1.0 / torch 2.7.0+cu128 / BDDL 3.7.0 环境；BEHAVIOR 资产需要自行按官方许可安装，不包含在 Git 仓库中。

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile skills \
  --task turning_on_radio --instance 301 --output runs/radio --port 29440
PYTHONPATH=src python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440
```

[MCP 异步采集探针](scripts/probe_async_observation.py)对运行中的 bridge 验证接口；它是脚本测试，不等于 LLM 完成任务。

## 执行和实验记录

目标由模型在最新 RGB 中选点；执行器用该像素的私有深度和视觉网格射线解析目标，执行 GT 导航、无 FixedJoint 的理想持物、官方状态设置及容器采样。底盘逐控制步理想运动，抓放可能瞬变；不是物理控制排行榜提交。最终 BDDL/TaskMetric 在结束后独立评分，不返回活动模型。[v7 修复、真实组件验证及限制](docs/executor_v7.md)。

导航已接入 jinkai/harness 的 visual-point GT planner 策略：候选落脚点采样、可达性检查、目标距离评分，以及逐步位姿反馈。Habitat 原生 navmesh/follower 由 OmniGibson 的静态可通行网格和理想底盘适配替代；修复了微小位置偏差导致起点落入相邻障碍格的问题。当前仍不包含完整动态碰撞检查。[移植范围与验证](docs/gt_navigation.md)。原 100 条批量评测按用户要求暂停，开发更新不会自动恢复它。

原始视频、图像、完整公开模型消息、工具轨迹、Skill 快照、版本、主机/解释器/GPU/PID 和失败记录保存在各 run 中。Replay 不补写隐藏思考或不存在的机械臂运动。原始数据和资产不提交到 Git。

- [观测与工具协议](docs/rgb_protocol.md)
- [四相机异步设计与验证](docs/async_observation.md)
- [2026-09-30 实测结果](docs/four_camera_validation_20260930.md)：60项CPU测试、真实MCP四相机探针、radio实例301闭环通过；[网页与回放](http://10.76.5.241:8765/surround_observation.html)（实验室网络/VPN）。
- [此前三相机实验记录](docs/history_before_four_camera.md)：历史成功结果不自动代表新相机配置通过。

## 当前执行器修复验证

v8 候选将选定表面作为放置约束，增加盘上物体等刚性承载关系的搬运，保护官方采样物理步中的底盘位姿，并统一导航线段碰撞检查。相机参数采集器提前初始化，四路 RGB、私有深度和内参一起通过有限的只渲染就绪检查；不会用伪造内参或移动机器人绕过初始化故障。

134 项 CPU 测试通过；`preparing_lunch_box` 实例 301 的真实四相机启动复测通过。执行器组件探针与完整模型任务采用独立记录；代码测试或启动成功不代表任务完成，也不能证明执行器已经完美。[每轮修复与实际验证状态](http://10.76.5.241:8765/executor_v8_20261001/index.html)保留失败尝试与原始录像。[我们与 lvzhang、wenbo 的当前实现对比](http://10.76.5.241:8765/executor_v8_20261001/comparison.html)按固定源码快照区分程序策略、导航 harness 和本项目的 RGB 操作闭环。

## 结果可视化

[当前任务回放](http://10.76.5.241:8765/retest32_v7_20261001/replays.html)默认展示简洁进度、任务选择，以及同步的相机画面与模型时间线。全部任务、运行状态、版本与原始记录默认折叠；任务列表自动更新时保留播放位置。页面源码为 [web/replays.html](web/replays.html)，读取同目录的 `behavior100/progress.json`，以嵌入模式加载现有 Replay；不修改录像、模型输出或评分。`scripts/publish_results_page.py --output-dir <批次报告目录>` 同时发布回放入口与分析页。

[v7 同批 32 条复测与修复前后对比](http://10.76.5.241:8765/retest32_v7_20261001/index.html)正在逐条更新；原 2/32 基线保留。[组件回归视频与原始证据](http://10.76.5.241:8765/executor_retest_20261001/components.html)单独展示脚本测试，不计入模型任务成功率。

[实验结果与故障分析页面](http://10.76.5.241:8765/retest32_gt_20260930/results.html)展示 32 项重测的状态、旧新 Q 分数、失败证据、工具耗时，以及逐次工具调用前后的四路 RGB 和模型原文。默认详读版保留 1× 仿真动作，完整模型文本逐页停留；前视最大，另三路缩小，第三人称仅供回放。真实耗时、旧精简版与原始录像仍可切换。接口实际返回的 reasoning summary 与 assistant 输出分别显示，历史缺失不补写。[新版展示与完整 32 条失败复核](http://10.76.5.241:8765/replay_readable_20261001/index.html)。

页面源码为 [web/results.html](web/results.html)，无需构建或外部 CDN。部署在批次报告目录中，读取同目录的 `behavior100/progress.json`、`behavior100/walltime_index.json`、`failure_review/review.json` 和各任务 `replay.json`。进度每 30 秒更新；人工复核的失败分析使用明确标注的冻结快照，新增任务不会自动套用旧结论。分析脚本、输入哈希、发布记录和浏览器验证保存在对应 operations 批次，不修改原始评测数据。

- [回放、接口推理摘要与原始记录](docs/replay_trace.md) · [含推理摘要的新真实测试](http://10.76.5.241:8765/replay_trace_20261001/manipulation_runs/mas_trace_000_turning_on_radio_i301_s0_r1/replay.html#step=9)
- [启动和放置修复的验证范围](docs/executor_repairs_20261001.md) · [每次修复尝试与结果](http://10.76.5.241:8765/executor_fixes_20261001/index.html)
