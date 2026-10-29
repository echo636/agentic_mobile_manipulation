# Executor v7 candidate — validation in progress

Historical iteration notes below. Current target grounding uses direct depth
backprojection without the former mesh/depth agreement gate; navigation skips
object lookup, while manipulation uses a separate first-hit visual ray for object
identity. See the [current RGB protocol](rgb_protocol.md).

This iteration is a research motor protocol behind the unchanged RGB-only model boundary. Candidate implementation and CPU tests do not establish simulator stability or benchmark success. The previous 32-task result (2/32) remains frozen; operations/executor_retest_20261001 records each subsequent environment probe, component attempt and matched retest.

## Candidate changes

- Restore only absent fixed floor meshes from the same supplied full scene template into the derived partial scene. Garage task partial templates contain two floors versus twelve in the full scene; task bindings and all existing objects remain untouched. This is an explicit scene-geometry repair with a new configured-scene hash, not an identical-environment ablation.

- Private same-pixel depth and CPU visual-mesh triangle intersection route the selected visible object. Local rigid mesh acceleration structures are cached; live transforms follow moving links. This avoids Replicator segmentation annotators, whose offscreen and viewer variants crashed during these probes. It is not a general native-crash fix. Collision-proxy disagreement is recorded separately, rather than rejecting a visually valid pixel or choosing a nearby object. No segmentation, object ID, world pose, target catalogue or goal value is given to the model.
- GT navigation filters approach candidates for visibility and proximity to the selected target. Manipulation may spend part of its existing bounded motor budget approaching that same target. This does not implement a complete dynamic obstacle/held-object planner.
- Controlled grasp lifts the selected object at its own XY and maintains its relative pose with no robot/object FixedJoint. Gravity and ordinary collisions remain enabled; the motor projects the held pose before and after control ticks. Contained rigid objects follow. This is ideal pose actuation, not physical grasp control. Particle/fluid carry and arbitrary articulated payload stability require separate real-simulator validation.
- Official Open.set_value(..., fully=True) replaces random partial opening. Open/close/toggle preserve the object's root and the robot base during settling, then verify the official state. Existing task goal formulas remain unchanged.
- Placement rollback restores private carry ownership as well as simulator state. A selected-surface place_on_top may request a relative world-up yaw rotation. A conservative horizontal envelope is used during cuboid sampling. Successful motor support is still distinct from official task predicates.
- act now includes nullable placement_yaw_degrees (only place_on_top) and wait_seconds (only wait, 0.1–20 simulation seconds; default 5). Archived three-field act requests remain accepted. Cross-primitive options fail before movement.
- Recording captures every second control step by default, uses explicit previous-frame holds for intervening CFR frames, and refreshes at observation boundaries. Each ledger row records its source capture step and wall timestamp. Raw frames are written to ffmpeg through an ordered, bounded eight-frame queue; there is no motion interpolation. MAS_VIDEO_RENDER_STRIDE=1 retains full capture frequency. Fresh model RGB is always rendered with the full synchronization barrier.
- Private component timing measures physics/metrics, render, sensor readback, spectator and video submission; nested timings are not additive. Current timing does not isolate planner time from all simulator overhead without a paired live comparison.
- A read-only MCP initialize/tools-list handshake verifies the actual stdio transport and exact frozen tool schemas before starting the model budget. It performs no simulator tool calls and does not add unseen observations to the model trace.
- Skills require an item/count/state verification pass using fresh RGB before claiming completion, and explain placement recovery and process waits. These instructions can reduce mistakes but cannot guarantee model compliance.

## Validation and resource policy

Writable project-local copies of the pinned runtime and selected assets were prepared on V03 and S115 because S134 GPUs are occupied. Native failures remain unresolved on V03 and on supported-driver S134/S115; its 570.124.04 driver is older than Isaac Sim 5.1's documented 580.65.06 validation baseline. Component tests use S115 and S134 with driver 580.173.02; supported drivers alone did not eliminate native failures. Upstream source and assets remain unmodified. Executed Python, host, GPU UUID, PID/unit, source hash and task/asset hashes must accompany each run. Cgroup preflight estimates reclaimable clean file cache and slab (full memory.stat is recorded), excludes shared/dirty/writeback pages, and requires 28 GiB available working-set headroom; simulator units are capped at 28 GiB. No unrelated processes are stopped.

Tests must include navigation timing with consistent capture policy, kitchen grasp, lower-shelf/container placement, fully opened doors and tool discovery. Scripted probes never count as autonomous task success. After these gates, rerun the same 32 tasks with the same instruction, instance 301, seed 0, model and action/model budgets, publishing every attempt and independent score. The changed motor protocol and any startup-budget/environment differences are explicit comparison limitations.

Shared GPU use must be explicit and must pass a >=24 GiB free-memory check before every episode; the default still requires an idle GPU. Capacity is a point-in-time check, not an exclusive reservation. Native crashes and resource failures remain visible in the result denominator. Replicator asynchronous rendering toggling is disabled and DLSS uses Quality mode per [Isaac Sim 5.1 known issues](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/overview/known_issues.html). This did not resolve V03's native crash and is not presented as a proven crash fix.

## Verified component results (2026-10-01)

The production core is frozen at `318de33` for the matched cohort. Project operations retain every unsuccessful initialization / native-crash experiment, not just these completed probes.

| Check | Result and limit |
| --- | --- |
| CPU contracts, pinned BDDL differential checks and real geometry dependencies | 111 tests passed, no skips in the complete V03 environment; CPU-only, no benchmark score |
| MCP stdio | Nine exact schemas, four actual RGB image payloads and legacy act arguments validated with a fixture; no physical-world success claim |
| Radio selected-pixel motor sequence | Four actions passed; native-debugger inferior exited normally. Debugger's final `bt` returned exit1 because the process had already exited; preserved separately |
| Kitchen sequence | Nine actions passed, including full opening, grasp, transport, official Inside placement and the food-processor grasp that previously produced NaN |
| Kitchen sustained carry / rollback | Repeated nine actions, 120 additional held-object steps, injected placement exception restoring carry ownership and pose, and 30 more finite-pose steps passed |
| Shoe lower-shelf placement | Five actions passed; real contact and upward support, shelf-height agreement, release and zero final speed verified. Official OnTop remains false because the rack also extends above the shoe. The task's unchanged predicates require rack contact, no floor contact and pairwise proximity |
| Garage loading-the-car initialization | Repaired floor geometry loads and four RGB cameras capture successfully; no navigation/task-completion claim from this startup-only probe |

These scripted probes use private target reprojection and do not contribute to autonomous success rate. The 32-task model retest runs separately with immutable records and independent final task scoring. The repaired configuration is not an official challenge submission. Native simulator reliability beyond these completed checks remains an empirical question; the earlier crashes were not silently removed or represented as globally solved.
