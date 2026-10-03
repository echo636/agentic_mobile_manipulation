# Agentic Mobile Manipulation

An independent research system for **RGB observation -> LLM action and target selection -> idealized execution -> fresh observation** in OmniGibson.

Maintained at [echo636/agentic_mobile_manipulation](https://github.com/echo636/agentic_mobile_manipulation). Reference repositories and existing navigation environments remain separate from this project.

## Observation: four fixed cameras

- Four 512 x 512 RGB views: **front, back, left, and right**, relative to the robot heading.
- Cameras follow the robot rigidly. All four render products are read after a shared render barrier and carry the same `capture_id`, `captured_at`, and `sim_step`. Observation does not rotate the robot or advance physics.
- The research rig replaces stock head and wrist sensors. Its fixed base-relative height is 5 cm above the robot's initial highest point, with a 35 cm mounting radius, 90-degree horizontal field of view, and 20-degree downward pitch. This is a recorded simulation configuration, not a real-hardware calibration claim.
- The model receives RGB and capture metadata. Depth, camera extrinsics, simulator object identity, and task ground truth remain inside the executor or offline evaluator.

## Observation and action tools

```text
start_observation({}) -> job_id, status=planned (returns immediately)
get_observation({job_id}) -> planned/running, or passed with four RGB images
cancel_observation({job_id}) -> request cancellation of an unfinished job
```

Capture runs at background scheduling points on the simulator's owning thread while network requests and model inference can continue. No concurrent thread manipulates OmniGibson. Capture is an indivisible read-only operation; a render already in progress cannot be interrupted. The final job status resolves cancellation races.

Results include `stale`. Older captures remain available, but cannot target a new action after another action or capture supersedes them. Any current camera view can supply an action target without rotating the robot first.

The default profile exposes nine MCP tools: `start_observation`, `get_observation`, `cancel_observation`, `observe`, `look`, `act`, `finish`, `list_skills`, and `read_skill`. `observe` is the synchronous compatibility entry point. `look` explicitly turns the base; it is not surround capture. Actions return four RGB views from one simulation state.

Four readable workflow skills are available: `visual-manipulation`, `visual-exploration`, `pick-and-place`, and `failure-recovery`. Explicit planning stacks and memory are currently deferred. Skills provide guidance; the LLM selects actions through tools.

## Installation and execution

Python >=3.11 is required. The CPU core does not require simulator packages:

```bash
python -m pip install -e '.[mcp]'
PYTHONPATH=src python -m unittest discover -s tests -v
```

The recorded simulator environment uses OmniGibson 3.9.2, Isaac Sim 5.1.0, torch 2.7.0+cu128, and BDDL 3.7.0. BEHAVIOR assets must be installed under their official license and are not included in this repository.

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile skills \
  --task turning_on_radio --instance 301 --output runs/radio --port 29440
PYTHONPATH=src python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440
```

The [asynchronous MCP observation probe](scripts/probe_async_observation.py) validates a running bridge. It is a scripted interface test, not an autonomous task result.

## Harness and model clients

All MCP transports use the official SDK's `FastMCP`: stdio by default, plus SSE and
Streamable HTTP on loopback. A shared tool registry and episode `ToolContext` serve
both MCP clients and the native RGB loop. Each server represents one episode.

The default Codex controller is preserved. A common `prepare_project -> run -> parse`
interface also provides OpenCode and Kimi adapters; their live RGB integration is
not yet validated. The native Responses loop has configurable turn/token budgets,
verified image attachments and optional historical-image retention, with replay
records for each tool call. Explicit planning and memory remain deferred.

See [harness architecture and transport usage](docs/architecture.md) and
[client capabilities and validation limits](src/manipulation_agent/clients/README.md).

## Execution and evidence

The model selects a pixel in a current RGB image. The private executor resolves that pixel through depth and a visual-mesh ray, then performs GT navigation, controlled carrying without a robot/object fixed joint, official state operations, or placement sampling. Base motion uses incremental idealized pose control; grasp and placement can be discontinuous. This is not a physical-control leaderboard submission. Final BDDL predicates and TaskMetric are evaluated independently after termination and are not returned to the active model. See [executor v7 and validation limits](docs/executor_v7.md).

Navigation adapts the visual-point GT planning strategy from `jinkai/harness`: approach candidates, reachability checks, target-distance ranking, and pose-feedback following. OmniGibson's static traversability grid and idealized base adapter replace Habitat's native navmesh and follower. Small pose offsets no longer incorrectly map a valid start into an adjacent obstacle cell. Full dynamic collision checking is still absent. See [navigation provenance and validation](docs/gt_navigation.md).

## Batch evaluation

Original (`main`) and Official (`explore/official-symbolic`) evaluate the same task list. Motor exploration is paused. The [current comparison](http://10.76.5.241:8765/compare100_20261002/) is separate from the historical 32-task replay portal.

The current queue protocol gives each task 30 minutes of actual agent execution, starting immediately before model launch after simulator initialization and MCP setup. Model inference, observations and actions consume this budget; resource waiting and initialization do not. Initialization has a separate watchdog and an initialization failure is recorded separately from a task execution timeout. A task has one terminal outcome: `success`, `failure` or `timeout`. Independent evaluator scores, controller completion and recording quality remain separate fields. A native crash or timeout may prevent a final Q score; the supervisor still records an explicit terminal outcome without inventing a score.

Both methods can use multiple workers in separately assigned resource pools. Within each method, host-memory and GPU leases prevent duplicate resource claims. Workers claim tasks in list order after resource admission; concurrently executing tasks may finish out of order. Cleanup must confirm the owned simulator has exited before its resource lease is released. A faulty worker waits for cleanup while other workers continue. Media validation and replay publication run in a bounded CPU queue after simulator cleanup.

Launch a frozen checkout with `scripts/start_behavior100_service.py --config <config.json> --unit mas-b100-<name>.service`. The supervisor restarts after an unexpected exit and retains task identities, deadlines and original records. For disjoint method resource pools, configure a separate `shared_lease_dir` per method and respect each host's actual GPU and memory capacity. A supervisor may finish the cohort only when every manifest task has a terminal outcome; unexpected worker exit cannot silently drop a task. Source versions and protocol changes remain attached to individual attempts; earlier completed attempts are not silently reclassified as new-code experiments.

Each run retains video, images, available model messages and returned reasoning summaries, tool traces, skill snapshots, versions, host/interpreter/GPU/PID provenance, and failure records. Replays do not invent hidden reasoning or unrecorded arm motion. Raw experiment data, credentials, and assets are excluded from Git.

- [Observation and tool protocol](docs/rgb_protocol.md)
- [Asynchronous four-camera design](docs/async_observation.md)
- [2026-09-30 validation](docs/four_camera_validation_20260930.md): 60 CPU checks, a real four-camera MCP probe, and a successful radio instance 301 loop; [report and replay](http://10.76.5.241:8765/surround_observation.html) require the lab network or VPN.
- [Earlier three-camera experiments](docs/history_before_four_camera.md): historical results do not validate a different camera configuration.

## Current executor validation

The v8 candidate enforces the selected placement surface, carries rigid supported contents such as food on plates, anchors the base during sampling physics, and uses consistent navigation segment checks. Camera calibration annotators initialize eagerly; RGB, private depth, and intrinsics pass a bounded render-only readiness check. No synthetic intrinsics or robot movement bypass initialization failures.

The recorded v8 validation includes 134 CPU tests and a real four-camera startup retest on `preparing_lunch_box`, instance 301. Component probes and complete model episodes have separate records. Passing code tests or initialization does not establish task success or a perfect executor. The [repair report](http://10.76.5.241:8765/executor_v8_20261001/index.html) preserves failed attempts and recordings. The [comparison with lvzhang and wenbo](http://10.76.5.241:8765/executor_v8_20261001/comparison.html) distinguishes program policies, navigation harnesses, and this project's RGB manipulation loop using pinned source snapshots.

## Results and replay

The [current system guide](http://10.76.5.241:8765/manipulation_system.html) explains the complete RGB loop and the Original / Official implementations with pinned source references. Its maintained source is [web/system_guide.html](web/system_guide.html); publish it with `python scripts/publish_system_guide.py --output-dir <report-root>`.

The [current comparison](http://10.76.5.241:8765/compare100_20261002/index.html) shows the Original and Official 100-task queues. The [historical 32-task replay portal](http://10.76.5.241:8765/retest32_v7_20261001/replays.html) retains only that completed cohort, synchronized camera views, and the model timeline. The complete task table is collapsed by default. Refreshes preserve playback. Retried tasks remain in the original batch without increasing its task count; previous attempts and bookmarked URLs retain their original execution versions. Bookmarked replays load independently of live progress metadata.

The portal source is [web/replays.html](web/replays.html). It reads `behavior100/progress.json` from the report directory and embeds existing replays without changing recordings, model outputs, or scores. `scripts/publish_results_page.py --output-dir <report-directory>` publishes the replay portal and analysis page.

The [historical 32-task comparison](http://10.76.5.241:8765/retest32_v7_20261001/index.html) retains that cohort and the earlier 2/32 baseline. [Scripted component regressions](http://10.76.5.241:8765/executor_retest_20261001/components.html) are displayed separately and do not count as model task successes.

The [earlier results and failure analysis](http://10.76.5.241:8765/retest32_gt_20260930/results.html) contains task status, old/new Q scores, failure evidence, timing, four-view observations, and model output. The detailed replay edition retains 1x simulation actions and reading intervals for available model text. The front view is largest; the other three views are smaller, and the spectator view is replay-only. Wall-time, earlier compact, and original video editions remain selectable. Returned reasoning summaries and assistant messages are displayed separately; missing history is not reconstructed. See the [replay review](http://10.76.5.241:8765/replay_readable_20261001/index.html).

[web/results.html](web/results.html) requires no build step or external CDN. It reads the batch's `behavior100/progress.json`, `behavior100/walltime_index.json`, `failure_review/review.json`, and per-task `replay.json`. Progress refreshes every 30 seconds. Manual failure analyses use explicitly frozen snapshots; their conclusions do not automatically extend to new runs. Audit scripts, input hashes, publication records, and browser checks remain in the corresponding operations batch.

- [Replay and model trace recording](docs/replay_trace.md) and a [recorded model episode](http://10.76.5.241:8765/replay_trace_20261001/manipulation_runs/mas_trace_000_turning_on_radio_i301_s0_r1/replay.html#step=9)
- [Startup and placement repair scope](docs/executor_repairs_20261001.md) and [repair attempts](http://10.76.5.241:8765/executor_fixes_20261001/index.html)
