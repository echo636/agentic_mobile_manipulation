# Executor v7 candidate — validation in progress

This iteration is a research motor protocol behind the unchanged RGB-only model boundary. Candidate implementation and CPU tests do not establish simulator stability or benchmark success. The previous 32-task result (2/32) remains frozen; operations/executor_retest_20261001 records each subsequent environment probe, component attempt and matched retest.

## Candidate changes

- Restore only absent fixed floor meshes from the same supplied full scene template into the derived partial scene. Garage task partial templates contain two floors versus twelve in the full scene; task bindings and all existing objects remain untouched. This is an explicit scene-geometry repair with a new configured-scene hash, not an identical-environment ablation.

- Private same-pixel render-instance identity plus depth routes the selected visible object. Collision-proxy disagreement is recorded separately, rather than rejecting a visually valid pixel or choosing a nearby object. No segmentation, object ID, world pose, target catalogue or goal value is given to the model.
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

A writable project-local copy of the pinned environment is being prepared on V03 because S134 GPUs are occupied by unrelated tasks. Upstream source and assets remain unmodified. Executed Python, host, GPU UUID, PID/unit, source hash and task/asset hashes must accompany each run. Cgroup preflight accounts for inactive file cache that the kernel can reclaim, and requires 28 GiB available working-set headroom; simulator units are capped at 28 GiB. No unrelated processes are stopped.

Tests must include navigation timing with consistent capture policy, kitchen grasp, lower-shelf/container placement, fully opened doors and tool discovery. Scripted probes never count as autonomous task success. After these gates, rerun the same 32 tasks with the same instruction, instance 301, seed 0, model and action/model budgets, publishing every attempt and independent score. The changed motor protocol and any startup-budget/environment differences are explicit comparison limitations.
