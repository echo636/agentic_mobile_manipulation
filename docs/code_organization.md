# Code organization

This map describes the long-horizon worktree. Experiment records identify the
frozen source actually used by each run; the latest checkout does not describe
every historical replay.

## Runtime path

```text
vision_cli.py (manip-agent)
  -> VisionHarness + ToolContext + tools/
     -> RGBBackend (executors/omnigibson_rgb.py)
        -> local_rgb.py / local_manipulation.py: target-locked approach
        -> carry.py / placement.py / material_*.py: object operations
        -> navigation/: mapping, route search, collision checks, base motion
        -> OmniGibsonBackend: simulator setup, stepping, task evaluation
           -> compat/: explicitly installed simulator compatibility fixes

Codex client -> FastMCP -> bridge -> VisionHarness
Native Responses loop -----------> VisionHarness
```

The model sees RGB and public tool feedback. Private geometry and simulator
handles belong to the executor; task evaluator state is kept out of model inputs.
Simulator construction and access stay inside the adapter and its owner thread.

## Where to change a behavior

Paths below are relative to `src/manipulation_agent/` unless stated otherwise.

| Concern | Main files | Responsibility |
|---|---|---|
| Episode entry and lifecycle | `vision_cli.py`, `vision_harness.py`, `closure.py`, `deadline.py` | CLI configuration, revision, budgets, closure and evaluation orchestration |
| Public tools | `tools/`, `tool_context.py`, `contracts.py` | Schemas, profile selection and request dispatch |
| Client transport | `mcp_server.py`, `bridge.py`, `clients/` | FastMCP, owner-thread RPC and client preparation/event parsing |
| Model requests and context | `vision_policy.py`, `agent_loop.py`, `image_history.py`, `provider_gateway.py`, `image_pruning.py` | Prompt, native loop and opt-in outbound image pruning |
| Skills and working notes | `skill_runtime.py`, `task_notes.py`, repository `skills/` | Readable guidance and episode-local model-authored goal/member state |
| Observation and grounding | `observations/`, `surround.py`, `executors/visual_target.py`, `executors/visual_mesh_grounding.py` | Four-camera capture and private pixel-to-target grounding |
| Action dispatch and local approach | `executors/omnigibson_rgb.py`, `executors/local_rgb.py`, `executors/local_manipulation.py`, `executors/local_stances.py` | Resolve the selected target and execute from a feasible local stance |
| Grasp and placement | `executors/carry.py`, `executors/placement.py`, `executors/placement_*.py`, `executors/inside_sampling.py` | Native grasp ownership, sampled placement and support/contact geometry |
| Material and articulation operations | `executors/material_*.py`, `executors/particle_*.py`, `executors/articulation_*.py` | Material transfer, payload bookkeeping and articulated-object operations |
| Navigation | `executors/navigation/` | GT route planning, collision geometry and base motion; retained online-mapping experiment |
| Simulator initialization and compatibility | `omnigibson_backend.py`, `executors/scene_lifecycle.py`, `executors/compat/` | Environment setup and version-specific upstream repairs |
| Task specifications and construction | `custom_tasks.py`, `catalog_tasks.py`, `task_bindings.py`, `executors/custom_task.py`, `executors/catalog_task.py`, repository `task_designs/` | Load/build frozen task instances and their public/scoring contracts |
| Scripted reference workflows | `reference_tasks.py`, `branching_reference.py`, `diverse_reference.py`, `full_workflow_reference.py`, `reference_evidence.py`, `custom_readiness.py` | Construction/readiness evidence; separate from model autonomy |
| Records and presentation | `records.py`, `model_trace.py`, `tool_trace.py`, `replay.py`, `goal_replay.py`, `*_video.py`, `replay_assets/`, `report_assets/` | Provenance, replay, videos and report assets |
| Batch support | `batch_lifecycle.py`, `batch_storage.py`, `memory_admission.py`, `startup_*.py` | Admission, supervision, storage and startup diagnostics |

`cli.py`, `harness.py`, `policies.py` and `fake_backend.py` retain legacy/symbolic
paths. The active RGB entry is `vision_cli.py`; similarly named legacy files are
not a second implementation to update automatically.

## Executor package boundaries

`navigation/` contains the default GT path (`gt_navigation`, `live_gt_map`,
`forward_navigation`, `heading_collision`, `navigation_refinement`,
`point_route_projection`, `base_motion`) and the retained sensor-built mapping
experiment (`online_backend`, `online_navigation`, `online_scene`,
`cartographer_mapping`, `depth_scan`, `self_depth_filter`). Being in this package
does not make the online experiment part of the default executor.

`compat/` contains the existing version-specific fixes for articulation sleep,
cloth import, contact capacity, heat-source links, Inside sampling, macro-particle
pose/size, particle emission/quaternions and temperature indices. Importing the
package does not install patches. Callers install them explicitly; ordinary
action logic remains outside this package.

These modules moved from `manipulation_agent.executors.<module>` to
`manipulation_agent.executors.navigation.<module>` or
`manipulation_agent.executors.compat.<module>`. In-tree imports and probe scripts
use the new paths. External scripts importing these private modules must update
their imports; frozen experiment copies retain their original layout.

## Script entry points

| Purpose | Repository paths |
|---|---|
| Single episode / controller | `manip-agent`, `manip-mcp`, `scripts/run_codex_controller.py`, `scripts/run_model_controller.py` |
| Batch preparation and execution | `scripts/prepare_behavior100.py`, `scripts/start_behavior100_service.py`, `scripts/run_behavior100.py`, `scripts/behavior100_remote.py`, `scripts/local_worker_transport.py` |
| Freeze runtime source | `scripts/stage_batch_runtime.py` |
| Task construction and readiness | `scripts/build_custom_task.py`, `scripts/prepare_custom_evaluation.py`, `scripts/validate_custom_readiness.py`, `scripts/validate_custom_task_physics.py` |
| Replay and reporting | `scripts/build_review.py`, `scripts/render_batch_replays.py`, `scripts/publish_replay_ui.py`, `scripts/publish_*`, `web/` |
| Diagnostics and historical probes | `scripts/probe_*`, `scripts/validate_*`, `scripts/audit_*` (read each script's scope before use) |

Script paths remain stable because batch configurations and existing services
call them directly. Generated runs, assets, caches, frozen sources and operational
journals belong outside Git in the shared workspace. A probe, reference workflow
or CPU test is not evidence that a model completed a task.

## Remaining structural work

The root package still mixes task/reference and media modules; `scripts/` still
mixes launchers, probes and publishers. `omnigibson_rgb.py`, `placement.py` and
`run_behavior100.py` remain large coordination modules. Future extraction should
follow these responsibilities and preserve entry points, with each extraction
reviewed separately from behavioral fixes.

See [architecture](architecture.md) for protocol/lifecycle details,
[local manipulation](local_manipulation.md) for approach behavior, and
[GT navigation](gt_navigation.md) for route planning.
