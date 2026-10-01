# Direct official symbolic executor

Profile: `official`. Protocol: `rgb_official_symbolic_direct_v1`.

This opt-in mode calls the pinned OmniGibson `SymbolicSemanticActionPrimitives.apply_ref` directly with the model-selected object and `attempts=1`. It retains four-camera RGB observation, revision checks, bounded execution, private evaluation, and replay. It is a symbolic execution experiment, not a physical-control challenge submission.

## What changes

| Aspect | Existing `skills` executor | New `official` executor |
| --- | --- | --- |
| Target | RGB pixel mapped privately to an object and surface point | Same grounding; passes only the selected object to upstream |
| Navigation | Project's Jinkai-style GT grid planner and incremental pose following | Original `NAVIGATE_TO`, including upstream defects |
| Grasp / carry | Project controlled pose carry and payload following | Official forced assisted grasp / fixed joint |
| Placement | Selected-surface checks, volume sampling, project recovery | Original relation-based sampler and placement; no surface/yaw constraint or project rollback |
| Open / toggle | Project state-operation wrapper with anchoring and approach | Original official preconditions, state setter and settling |
| Reach | Project distance checks / automatic approach | No added distance check or approach; upstream semantics apply |
| Actions | Nine shared semantic names plus project `wait` | All 14 official enum members; no `wait` or `look` |

The current `motor` profile is a third condition: model-written Python commands physical base, joint and gripper controllers. It does not call these symbolic primitives. FastMCP is unrelated to which executor runs; all profiles keep the existing official MCP SDK server.

## Model-facing contract

Six MCP tools: `observe`, `start_observation`, `get_observation`, `cancel_observation`, `act`, and `finish`. The simplified action signature is:

```python
act(primitive="toggle_on",
    target={"image_ref": "latest RGB reference", "point": [0.5, 0.5]},
    revision=0)
```

This is illustrative, not a tested pixel. `release` alone takes `target=null`. No simulator object ID, current pose, state truth, depth or evaluator feedback enters the model interface. On placement, the selected pixel identifies the destination object; it does not constrain placement to that exact pixel or shelf. `placement_yaw_degrees` and `wait_seconds` are not accepted.

Available enum names, written in the tool's lower-case form:

`grasp`, `place_on_top`, `place_inside`, `open`, `close`, `toggle_on`, `toggle_off`, `soak_under`, `soak_inside`, `wipe`, `cut`, `place_near_heating_element`, `navigate_to`, `release`.

The initial profile omits workflow-skill documents because the existing documents describe custom navigation and placement semantics. There is no plan or memory tool. Normal conversation history remains available.

## Upstream limits and evidence

Pinned OmniGibson source: `b1979916ec1549b10a4e65e630bc6504a9af1b00` (3.9.2).

- Symbolic actions can directly change states and poses. In particular, original grasp/toggle code does not enforce the old adapter's distance checks. A successful radio toggle may therefore require no approach. This cannot establish navigation or contact-control ability.
- Symbolic initialization skips CuRobo, but inherited `NAVIGATE_TO` accesses that absent planner. The pinned implementation also has a navigation method signature mismatch farther down that path. This mode preserves errors and does not silently fall back to project navigation.
- Tool completion is an upstream operation outcome, not task success. Failures can leave an object released or a state changed. No project scene rollback runs.
- All 14 names are exposed; exposure and CPU dispatch checks do not establish that all 14 work physically or complete their associated tasks.
- Primitive attempts use a 120-second cooperative time check, bounded yielded environment steps and a separate internal physics-tick cap (four times the action step budget). These checks cannot interrupt a native call that never returns; the process supervisor supplies the outer deadline.
- One upstream attempt differs from the upstream default of three. This prevents hidden retry multiplication and is explicitly recorded.
- Camera tracking is disabled to preserve the fixed RGB rig. Official primitive source files are not patched. Wrapper budgets and tracing are the only additions around primitive execution.

`official_primitives.jsonl` privately records each upstream call, target binding, error and step count. `official_control_steps.jsonl` records every yielded action passed to the environment. Internal sampler ticks are counted separately from official environment-step timing. Source-file hashes and protocol flags are included in run provenance. Public feedback sanitizes simulator object/state details.

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --agent-profile official --task turning_on_radio --instance 301 \
  --output /path/to/fresh/run --port 29984
# Use --agent-profile official on scripts/run_codex_controller.py as well.
```

Experiment records: `operations/official_symbolic_20261002/` outside Git. A scripted component probe is recorded separately from a fresh Astra episode. Existing batch workers continue on their frozen sources.

[Official symbolic source](https://github.com/StanfordVL/BEHAVIOR-1K/blob/b1979916ec1549b10a4e65e630bc6504a9af1b00/OmniGibson/omnigibson/action_primitives/symbolic_semantic_action_primitives.py).
