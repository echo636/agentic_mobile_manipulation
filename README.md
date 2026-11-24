# Agentic Mobile Manipulation

An independent research system for **RGB observation -> LLM action and target selection -> idealized execution -> fresh observation** in OmniGibson.

Maintained at [echo636/agentic_mobile_manipulation](https://github.com/echo636/agentic_mobile_manipulation). Reference repositories and existing navigation environments remain separate from this project.

## 论文核心贡献定位（路线 A + 路线 B 结合：严禁针对测试集写定制策略）

本项目的核心方法贡献**绝不是针对某个特定测试集或某几类题型的专用策略**（严禁在提示词、技能或规划工具参数中硬编码“家电计时”、“托盘配货”、“先擦净再擦脏”等题型口诀或专用字段），而是构建一套**模型无关、任务无关的双层通用具身智能体架构**，同时在 **原版 BEHAVIOR-1K 长程任务集** 与 **多模式自建长程规划基准（[`task_designs/scheduling_v1`](task_designs/scheduling_v1/README.md)）** 上验证普适收益：

1. **底层（路线 A）：通用具身闭环感知与记忆 Harness（保证“做得对、不忘事”）**
   - **四向视觉锚定 + 选点局部放大回显**：四路固定机身视角同步观测锚定方向感，配合像素点击局部放大回显，消除小目标与边缘透视点偏；
   - **空间/物体状态外部记忆压缩**：仅从公开动作回执与历史视觉观测自动抽取结构化状态记忆（去过的地点、见过的物体位置、容器开合、手部持有物与公开状态），并对远古图像帧做滑动窗口压缩（或滚动会话续跑），解决超 100 步失忆、千万级缓存 Token 膨胀与单会话 600 张图像硬上限；
   - **预期偏差自纠错 + 防原地早退**：公开返回物理受阻因果归因，拦截同一动作原地重复报错混早退，促使模型在预期落空时切换动作自纠错；
   - **结束前逐项视觉核验**：在声明完成前对照公开任务要求逐条核对已观测视觉与回执证据，杜绝长程尾段漏件与虚假完成。
2. **上层（路线 B）：零领域假设的通用前置条件与时空代价图规划器（保证“做得快、不死锁”）**
   - **将早期先导实验中写死电器（`timed_processes`）与托盘（`carriers`）的旧版 `schedule_planner` 彻底推倒重做**，抽象为零领域假设的通用前置条件与时空代价图规划器（General Precondition & Cost-Aware Task Graph Planner）；
   - 输入与内部表示**只认物理世界通用的四个基本原语：“状态变量、前置条件与后置效果、持续时间（及是否占用单手）、空间移动代价”**，让**异步等待并行、跨区顺路合并、最少状态重置排序、有限空间无死锁借位腾挪**全部从同一个通用图搜索里**自然涌现**。
3. **三级通用消融阶梯**：① **裸模型最小基线** → ② **+ 通用具身感知记忆 Harness（路线 A）** → ③ **+ 零领域假设通用前置条件与时空代价图规划器（路线 A + B 结合）**。最新进展与六项指标对照见 **[阶段进展与实验成果报告（2026-11-24）](docs/progress_and_results_20261124.md)**；早期先导汇总见 **[总进度与实验报告（2026-11-15）](docs/progress_and_results_20261115.md)** 与 **[多模式规划难任务构造规范](docs/multi_mode_planning_benchmark_design_20261115.md)**。

---

On this host, use the single checkout `agentic_mobile_manipulation`; see the
[local workspace and archived-runtime guide](docs/local_workspace.md) for source,
data, compatibility paths and validation commands.

Start with the [code map and entry points](docs/code_organization.md) to locate
runtime, executor, task, skill and reporting code. See
[task cards and the readiness protocol](docs/custom_long_horizon.md) for custom
long-horizon tasks and their versioned public/scoring contracts. Recipes,
scripted references and CPU tests alone do not establish autonomous task success.
The [constructed custom-task catalog](task_designs/README.md) lists only tasks
with built scenes, including the two constructed tasks from yujie's PR #6 and the
6-task multi-mode planning & scheduling benchmark suite ([`task_designs/scheduling_v1`](task_designs/scheduling_v1/README.md)).

## Observation: four fixed cameras

- Four 512 x 512 RGB views: **front, back, left, and right**, relative to the robot heading.
- Cameras follow the robot rigidly. All four render products are read after a shared render barrier and carry the same `capture_id`, `captured_at`, and `sim_step`. Observation does not rotate the robot or advance physics.
- The research rig replaces stock head and wrist sensors. The four cameras are mounted 35 cm outward in their respective viewing directions; their fixed base-relative height is fitted to the robot's initial visual geometry, with 5 cm clearance. They use a 90-degree horizontal field of view and 35-degree downward pitch. This is a recorded simulation configuration, not a real-hardware calibration claim.
- The model receives RGB and capture metadata. Depth, camera extrinsics, simulator object identity, and task ground truth remain inside the executor or offline evaluator.

## Observation and action tools

```text
initialize({}) -> current revision and the prepared episode's first four RGB views
act({...})     -> execution feedback, next revision and four RGB views
look({...})    -> turn feedback, next revision and four RGB views
finish({...})  -> formal episode closure
```

Call `initialize` first and inspect the returned images. It exposes the prepared episode's current snapshot without resetting the scene or rendering an extra frame. Each subsequent `act` or `look` completes on the simulator's owning thread and returns its resulting four-camera snapshot in the same response. The model receives those pixels before choosing its next action.

Use the latest returned revision and image references for the next action. Older captures remain historical evidence and cannot target a new action after they are superseded. Any current camera view can supply an action target without rotating the robot first. `look` explicitly turns the base; it is not surround capture. If the world needs time to change, use `act` with `primitive="wait"`, `target=null` and an appropriate `wait_seconds`, then inspect the returned RGB. The latest action images can support final verification; no extra observation request is required before `finish`.

The default `skills` profile exposes seven MCP tools: `initialize`, `look`, `act`, `finish`, `list_skills`, `read_skill`, and `task_notes`. `minimal` exposes the first four. The legacy `workflow` profile retains `observe` and asynchronous observation jobs for compatibility; they are absent from the default tool catalog.

Five readable workflow skills are available: `visual-manipulation`, `goal-decomposition`, `visual-exploration`, `pick-and-place`, and `failure-recovery`. The goal-decomposition skill guides the model to write the task end state, recursively refine subgoals, and track prerequisites and item coverage in versioned, episode-local `task_notes`. Tree depth and child counts follow the task; the current action plan is stored separately. Skills provide guidance; the LLM selects actions through tools.

## Local manipulation control

The default RGB executor uses `target_locked_approach_v2`: the brain chooses the task and current-image target; the motor controller approaches that same target and executes the operation. `initialize.control` declares the protocol. Set `MAS_LOCAL_MANIPULATION=0` for the previous explicit-navigation behavior. See [coordination, limits and validation](docs/local_manipulation.md).

## Installation and execution

Python >=3.11 is required. The CPU core does not require simulator packages:

```bash
python -m pip install -e '.[mcp,navigation]'
PYTHONPATH=src python -m unittest discover -s tests -v
```

The recorded simulator environment uses OmniGibson 3.9.2, Isaac Sim 5.1.0, torch 2.7.0+cu128, and BDDL 3.7.0. BEHAVIOR assets must be installed under their official license and are not included in this repository. Default navigation uses private GT floor support and current scene collision geometry and does not require Cartographer or `MAS_CARTOGRAPHER_RUNTIME`. The former online-mapping implementation has been removed; its [historical evidence](docs/online_navigation.md) remains archived.

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile skills \
  --task turning_on_radio --instance 301 --output runs/radio --port 29440
PYTHONPATH=src python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440
```

The [asynchronous MCP observation probe](scripts/probe_async_observation.py) covers the legacy `workflow` observation interface. It is a scripted compatibility test, not a test of the default tool catalog or an autonomous task result.

## Harness and model clients

All MCP transports use the official SDK's `FastMCP`: stdio by default, plus SSE and
Streamable HTTP on loopback. A shared tool registry and episode `ToolContext` serve
both MCP clients and the native RGB loop. Each server represents one episode.

The default Codex controller is preserved. A common `prepare_project -> run -> parse`
interface also provides OpenCode and Kimi adapters; their live RGB integration is
not yet validated. The native Responses loop has configurable turn/token budgets,
verified image attachments and optional historical-image retention, with replay
records for each tool call. Both paths use the same episode-local task notes;
the optional provider gateway can also prune old image payloads from CLI-client
requests. Context retention settings are recorded per experiment.

See [harness architecture and transport usage](docs/architecture.md) and
[client capabilities and validation limits](src/manipulation_agent/clients/README.md).

The 2026-10-04 acceptance used frozen `0760f64`: Astra through Codex and FastMCP
completed radio instance 301 with Q=1 in 73.942 seconds of agent execution, excluding
initialization. [Recorded replay](http://10.76.5.241:8765/manipulation_runs/mas_harness_phase1_original_000_turning_on_radio_i301_s0_r1/replay.html).
This validates one Original episode; native Responses, OpenCode and Kimi do not
inherit this real-client acceptance. CPU/interface validation passed 224 tests,
with nine tests skipped for unavailable simulator dependencies.

## Execution and evidence

The model selects a pixel in a current RGB image. The private executor directly backprojects that pixel with its captured depth to obtain the target position, without a depth-to-mesh consistency check. Navigation uses that point without an object lookup. Manipulation additionally identifies the first visual object on the selected ray, solely to obtain its simulator handle; the mesh intersection never replaces the backprojected position. The executor then performs navigation, native assisted grasp, official state operations, or placement sampling. Grasp transfers the object and its payload once and creates the simulator's native grasp joint; subsequent carrying is handled by native physics. Base motion certifies a route and transfers to its endpoint once, then lets physics settle; grasp and placement can also be discontinuous. This is not a physical-control leaderboard submission. Final BDDL predicates and TaskMetric are evaluated independently after termination and are not returned to the active model. See the [RGB protocol](docs/rgb_protocol.md) and [executor v7 history](docs/executor_v7.md).

For `place_on_top`, the selected surface and shelf determine the landing region. Candidate poses use the carried object's collision geometry and requested relative yaw, without expanding its footprint into a diagonal-sized square. The executor checks physical contacts, lets candidates settle under gravity, and verifies support near the selected surface after release. Rejected candidates restore the scene; failed actions also restore the carry relationship. Collision geometry and diagnostics remain private to the executor. `place_inside` uses official volume sampling with a geometric fallback that covers distinct poses and supports on existing contents. Ideal grasps transfer the assembly directly to a checked carry destination; pickup trajectories are not required. Loaded base motion still checks continuous collision sweeps. Paired scripted placement probes are component evidence, not autonomous task success or a benchmark success-rate estimate.

Default navigation uses private floor support and current scene collision geometry
in `executors/navigation/`. It selects a reachable point or target approach and
checks the route against robot/payload geometry. The current RGB actuator commits
the endpoint pose once, advances native physics for settling, and checks the
result. It does not simulate continuous driving along the planned route; recorded
route distance is a planning measure. Maps, depth and world coordinates stay
private. See [GT-grid planning](docs/gt_navigation.md) for the planner and its
historical controller stages. Online Cartographer code and its dedicated probes
have been removed. Frozen evaluations retain their original source and results.

## Batch evaluation

The integration target is `main`; this long-horizon worktree uses
`feat/long-horizon-tasks`. Archived attempts retain their actual frozen source and
protocol. The [2026-10-02 comparison](http://10.76.5.241:8765/compare100_20261002/)
is a historical cohort, separate from current long-horizon experiments and the
historical 32-task replay portal. Official development is outside this worktree's scope.

New long-horizon runs explicitly select `unbounded` episode policy: action counts
are recorded without an episode action, physics-step or wall-time cutoff. This
does not change historical runs or the generic CLI's defaults. Initialization and
post-finish cleanup have separate watchdogs; each frozen batch configuration is
authoritative. Independent evaluator success, controller completion and recording
quality remain separate fields. A native crash may prevent a final score; missing
scores are never invented.

Batches can use multiple workers. Shared host-memory admission and GPU leases
coordinate resource claims. Workers claim tasks in list order after admission;
concurrent tasks may finish out of order. Cleanup must confirm the owned simulator
has exited before its resource lease is released. Media validation and replay
publication run in a bounded CPU queue after simulator cleanup.

Launch a frozen checkout with `scripts/start_behavior100_service.py --config <config.json> --unit mas-b100-<name>.service`. The supervisor restarts after an unexpected exit and retains task identities, configured budgets and original records. Batches sharing resources must use the shared lease directory and respect each host's actual GPU and memory capacity. A supervisor may finish the cohort only when every manifest task has a terminal outcome; unexpected worker exit cannot silently drop a task. Source versions and protocol changes remain attached to individual attempts; earlier completed attempts are not silently reclassified as new-code experiments.

Each run retains video, images, available model messages and returned reasoning summaries, tool traces, skill snapshots, versions, host/interpreter/GPU/PID provenance, and failure records. Replays do not invent hidden reasoning or unrecorded arm motion. Raw experiment data, credentials, and assets are excluded from Git.

`scripts/prepare_behavior100.py --instruction-mode mixed-coarse` preserves the 50 official gallery instructions and gives the other 50 tasks short task-level goals instead of detailed BDDL translations. The official evaluator is unchanged; omitted goal information can affect scores, so this condition must be reported separately from detailed-instruction runs. Batch configuration `export_spectator_video: true` exports every third-person frame to `spectator.mp4` and produces `spectator_walltime.mp4` from actual frame timestamps and the controller interval. The latter preserves model/network waiting by holding recorded frames and excludes simulator initialization. Both editions validate their full duration and remain separate from the original recording; neither invents motion. The replay links directly to the full wall-time third-person video when available.

- [Observation and tool protocol](docs/rgb_protocol.md)
- [Legacy asynchronous four-camera compatibility](docs/async_observation.md)
- [2026-09-30 validation](docs/four_camera_validation_20260930.md): 60 CPU checks, a real four-camera MCP probe, and a successful radio instance 301 loop; [report and replay](http://10.76.5.241:8765/surround_observation.html) require the lab network or VPN.
- [Earlier three-camera experiments](docs/history_before_four_camera.md): historical results do not validate a different camera configuration.

## Historical executor v8 validation (2026-10-01)

The historical v8 candidate enforced the selected placement surface, carried rigid supported contents such as food on plates, anchored the base during sampling physics, and used consistent navigation segment checks. These records describe that source version, not all subsequent executor changes.

The recorded v8 validation includes 134 CPU tests and a real four-camera startup retest on `preparing_lunch_box`, instance 301. Component probes and complete model episodes have separate records. Passing code tests or initialization does not establish task success or a perfect executor. The [repair report](http://10.76.5.241:8765/executor_v8_20261001/index.html) preserves failed attempts and recordings. The [comparison with lvzhang and wenbo](http://10.76.5.241:8765/executor_v8_20261001/comparison.html) distinguishes program policies, navigation harnesses, and this project's RGB manipulation loop using pinned source snapshots.

## Results and replay

The repository retains reusable evaluation and replay interfaces:
`web/replays.html` and `web/replay_portal.js` for result navigation,
`web/evaluation/` for batch/comparison templates, and the packaged
`replay_assets/` / `report_assets/` for episode playback and shared presentation.
Cohort-specific dashboards, task-story pages and system/demo explainers have been
removed from source. Previously published pages and experimental evidence remain
at their original report locations. `goal_ablation_summary.py` exports comparison
JSON without publishing a dedicated cohort page.

The [2026-10-02 comparison](http://10.76.5.241:8765/compare100_20261002/index.html) shows the historical Original and Official 100-task queues. The [historical 32-task replay portal](http://10.76.5.241:8765/retest32_v7_20261001/replays.html) retains only that completed cohort, synchronized camera views, and the model timeline. The complete task table is collapsed by default. Refreshes preserve playback. Retried tasks remain in the original batch without increasing its task count; previous attempts and bookmarked URLs retain their original execution versions. Bookmarked replays load independently of live progress metadata.

The portal source is [web/replays.html](web/replays.html). It reads `behavior100/progress.json` from the report directory and embeds existing replays without changing recordings, model outputs, or scores. `scripts/publish_results_page.py --output-dir <report-directory>` publishes the replay portal and analysis page.

The [historical 32-task comparison](http://10.76.5.241:8765/retest32_v7_20261001/index.html) retains that cohort and the earlier 2/32 baseline. [Scripted component regressions](http://10.76.5.241:8765/executor_retest_20261001/components.html) are displayed separately and do not count as model task successes.

The [earlier results and failure analysis](http://10.76.5.241:8765/retest32_gt_20260930/results.html) contains task status, old/new Q scores, failure evidence, timing, four-view observations, and model output. The detailed replay edition retains 1x simulation actions and reading intervals for available model text. The front view is largest; the other three views are smaller, and the spectator view is replay-only. Wall-time, earlier compact, and original video editions remain selectable. Returned reasoning summaries and assistant messages are displayed separately; missing history is not reconstructed. See the [replay review](http://10.76.5.241:8765/replay_readable_20261001/index.html).

[web/results.html](web/results.html) requires no build step or external CDN. It reads the batch's `behavior100/progress.json`, `behavior100/walltime_index.json`, `failure_review/review.json`, and per-task `replay.json`. Progress refreshes every 30 seconds. Manual failure analyses use explicitly frozen snapshots; their conclusions do not automatically extend to new runs. Audit scripts, input hashes, publication records, and browser checks remain in the corresponding operations batch.

- [Replay and model trace recording](docs/replay_trace.md) and a [recorded model episode](http://10.76.5.241:8765/replay_trace_20261001/manipulation_runs/mas_trace_000_turning_on_radio_i301_s0_r1/replay.html#step=9)
- [Startup and placement repair scope](docs/executor_repairs_20261001.md) and [repair attempts](http://10.76.5.241:8765/executor_fixes_20261001/index.html)
- [Measured host memory admission and shared startup coordination](docs/memory_admission.md)
