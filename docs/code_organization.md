# Code organization

This map describes the current long-horizon worktree. Each experiment records its
own frozen source; archived replays do not inherit changes from this checkout.

## Package layout

```text
src/manipulation_agent/
  agent/          model policies, context, skill loading and working notes
  runtime/        episode state, clocks, closure and tool dispatch
  transport/      owner-thread RPC and MCP preflight
  clients/        Codex/OpenCode/Kimi adapters and event parsing
  tools/          public tool schemas and handlers
  observations/   RGB capture, model boundary and asynchronous capture jobs
  executors/      simulator adapter, object operations and local approach
    navigation/   current GT navigation and target visibility
    compat/       explicitly installed simulator compatibility fixes
  tasks/          task contracts, bindings, grounding and private scoring
  references/     scripted construction/readiness workflows
  batch/          resource admission, startup, lifecycle and storage
  reporting/      trace interpretation and video export
  replay_assets/  offline replay HTML/CSS/JS
  report_assets/  report theme assets
```

The root keeps ten Python files: six command entry points (`vision_cli.py`,
`mcp_server.py`, `provider_gateway.py`, `cli.py`, `replay.py`, `report_server.py`),
shared `contracts.py` and `records.py`, the small `replay_shell.py` presentation
loader, and package `__init__.py`. `tasks.json` stays beside the CLI entry points.
Existing command names, packaged resources and script paths remain stable.
There are no import-alias wrappers or a second copy of the implementations.

## Runtime path

```text
vision_cli.py (manip-agent)
  -> runtime/vision_harness.py + runtime/tool_context.py + tools/
     -> executors/omnigibson_rgb.py
        -> local_rgb.py / local_manipulation.py: target-locked approach
        -> carry.py / placement.py / material_*.py: object operations
        -> navigation/: GT routes, collision geometry and self-occlusion
        -> executors/omnigibson_backend.py: simulator setup and evaluation
           -> compat/: explicitly installed version-specific fixes

clients/ -> mcp_server.py -> transport/bridge.py -> runtime/VisionHarness
agent/agent_loop.py ----------------------------> runtime/VisionHarness
```

The model receives RGB and public tool feedback. Simulator handles and geometry
belong to the executor; private evaluator state is excluded from model inputs.
Only the simulator owner thread constructs, renders, steps or changes the scene.

## Where to edit

All paths below are relative to `src/manipulation_agent/`.

| Change | Main files |
|---|---|
| Prompt, model requests or image context | `agent/vision_policy.py`, `agent/agent_loop.py`, `agent/image_history.py`, `agent/image_pruning.py`; provider queue entry `provider_gateway.py` |
| Skill documents and goal/member notes | repository `skills/`, `agent/skill_runtime.py`, `agent/task_notes.py` |
| Tool contract and episode lifecycle | `tools/`, `contracts.py`, `runtime/vision_harness.py`, `runtime/tool_context.py`, `runtime/closure.py`, `runtime/deadline.py` |
| MCP/client integration | `mcp_server.py`, `transport/bridge.py`, `transport/mcp_preflight.py`, `clients/` |
| Observation and pixel grounding | `observations/`, `executors/visual_target.py`, `executors/visual_mesh_grounding.py` |
| Target approach and manipulation | `executors/local_rgb.py`, `executors/local_manipulation.py`, `executors/local_stances.py`, `executors/carry.py`, `executors/placement.py`, `executors/material_*.py` |
| Simulator initialization | `executors/omnigibson_backend.py`, `executors/scene_lifecycle.py`, `executors/compat/` |
| Task definitions and construction | `tasks/custom_tasks.py`, `tasks/catalog_tasks.py`, `tasks/diverse_task_contract.py`, `executors/custom_task.py`, `executors/catalog_task.py`; repository `task_designs/` |
| Private task evaluation | `tasks/long_horizon.py`, `tasks/task_bindings.py`, `tasks/goal_grounding.py` |
| Scripted feasibility/readiness | `references/reference_tasks.py`, `references/*_reference.py`, `references/reference_evidence.py`, `references/custom_readiness.py` |
| Provenance and replay | `records.py`, `replay.py`, `replay_shell.py`, `reporting/audit.py`, `reporting/model_trace.py`, `reporting/tool_trace.py`, `reporting/transcript.py`, `reporting/goal_replay.py` |
| Video recording/export | `reporting/video.py`, `reporting/*_video.py` |
| Batch supervision | `batch/batch_lifecycle.py`, `batch/batch_storage.py`, `batch/memory_admission.py`, `batch/startup_*.py`, `batch/asset_preflight.py` |

`cli.py`, `runtime/harness.py`, `agent/policies.py` and
`executors/fake_backend.py` retain the legacy/symbolic path and shared helpers.
The normal RGB entry remains `vision_cli.py`. Scripted references and CPU mocks
are kept separate from autonomous model success evidence.

## Why navigation has several files

These are components of one current GT navigation path, not independent stacks:

| Module | Role |
|---|---|
| `gt_navigation.py` | Grid representation, target candidates and grid route search |
| `live_gt_map.py` | Build/update private floor-support and scene-obstacle geometry |
| `forward_navigation.py` | Check route connections and forward/reverse/holonomic alternatives |
| `heading_collision.py` | Collision checks at robot/payload positions and headings |
| `navigation_refinement.py` | Refine a local route or turn when coarse geometry is insufficient |
| `point_route_projection.py` | Project a selected point to a reachable route endpoint |
| `self_occlusion.py` | Test whether robot or payload geometry blocks the target from a camera |

The online-mapping experiment was removed on 2026-10-29: Cartographer integration
and native worker, depth-to-scan projection, online mapping/planning, its probes
and dedicated tests. The unused historical `base_motion.trajectory` helper was
also removed. The shared self-occlusion geometry remains unchanged; depth masking
was removed. [Archived online experiment evidence](online_navigation.md) and
frozen runs retain their historical versions.

`compat/` contains existing simulator-version repairs. Importing the package does
not install patches; the adapter explicitly installs the ones it uses.

## Script entry points and remaining work

- Episode/controller: `manip-agent`, `manip-mcp`, `scripts/run_codex_controller.py`, `scripts/run_model_controller.py`.
- Batch: `scripts/prepare_behavior100.py`, `scripts/start_behavior100_service.py`, `scripts/run_behavior100.py`, `scripts/behavior100_remote.py`.
- Source freezing: `scripts/stage_batch_runtime.py`.
- Task construction/readiness: `scripts/build_custom_task.py`, `scripts/prepare_custom_evaluation.py`, `scripts/validate_custom_readiness.py`, `scripts/validate_custom_task_physics.py`.
- Presentation: `scripts/build_review.py`, `scripts/render_batch_replays.py`, `scripts/publish_*`, `web/`.
- Diagnostics: remaining `scripts/probe_*`, `scripts/validate_*`, `scripts/audit_*`; read their scope before use.

Private module imports now use the packages above. In-tree consumers are updated;
external scripts targeting this checkout need the same import updates. Existing
command entry points and frozen experiment copies keep their established paths.
Generated runs, assets, caches and operational journals stay outside Git.

The large `executors/omnigibson_rgb.py`, `executors/placement.py` and
`scripts/run_behavior100.py` still combine several coordination responsibilities.
Their internal decomposition is separate work from this package reorganization.
See [architecture](architecture.md) for tool and lifecycle details.
