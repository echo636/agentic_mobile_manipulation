# Direct official symbolic executor

Development branch: `explore/official-symbolic`. Historical simulator evidence below belongs to frozen mixed-branch commit `c91a054`; splitting branches does not rerun those experiments.

Profile: `official`. Current protocol: `rgb_official_symbolic_episode_budget_v3`.
The [2026-10-04 alignment analysis](official_alignment_20261004.md) documents the
current frame and budget repairs. The initial v1 experiment remains recorded below.

This opt-in mode calls the pinned OmniGibson `SymbolicSemanticActionPrimitives.apply_ref` directly with the model-selected object and `attempts=1`. It retains four-camera RGB observation, revision checks, bounded execution, private evaluation, and replay. It is a symbolic execution experiment, not a physical-control challenge submission.

## What changes

| Aspect | Existing `skills` executor | New `official` executor |
| --- | --- | --- |
| Target | RGB pixel mapped privately to an object and surface point | Same grounding; passes only the selected object to upstream |
| Navigation | Project's Jinkai-style GT grid planner and incremental pose following | Native `NAVIGATE_TO`, initialized CuRobo and holonomic-frame compatibility |
| Grasp / carry | Project controlled pose carry and payload following | Official forced assisted grasp / fixed joint |
| Placement | Selected-surface checks, volume sampling, project recovery | Original relation-based sampler and placement; no surface/yaw constraint or project rollback |
| Open / toggle | Project state-operation wrapper with anchoring and approach | Original official preconditions, state setter and settling |
| Reach | Project distance checks / automatic approach | No added distance check or approach; upstream semantics apply |
| Actions | Nine shared semantic names plus project `wait` | All 14 official enum members; no `wait` or `look` |

The separate, paused motor branch is a third condition: model-written Python commands physical base, joint and gripper controllers. It does not call these symbolic primitives. All current MCP transports in this branch use the official SDK FastMCP server; executor selection is independent of the transport.

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
- The upstream Symbolic constructor skips CuRobo. This adapter explicitly initializes its required DEFAULT/ARM planners and adapts the inherited endpoint signature. A measured world/virtual-anchor mismatch is corrected for candidate joints and endpoint world height. Sampling, collision and IK remain native; there is no project GT navigation fallback.
- Tool completion is an upstream operation outcome, not task success. Failures can leave an object released or a state changed. No project scene rollback runs.
- All 14 names are exposed; exposure and CPU dispatch checks do not establish that all 14 work physically or complete their associated tasks.
- Official primitives use the episode execution deadline and remaining environment steps. The former 120-second/700-step caps and separate internal physics cap were removed because they truncated legitimate native settling. MCP transport waits allow the episode deadline plus response/cleanup time. A process supervisor still handles native calls that cannot return.
- One upstream attempt differs from the upstream default of three. This prevents hidden retry multiplication and is explicitly recorded.
- Camera tracking is disabled to preserve the fixed RGB rig. Upstream files stay unchanged. Required planner initialization, instance-scoped signature/frame compatibility and recorded execution budgets are explicit in provenance.

`official_primitives.jsonl` privately records each upstream call, target binding, error and step count. `official_control_steps.jsonl` records every yielded action passed to the environment. Internal sampler ticks are counted separately from official environment-step timing. Source-file hashes and protocol flags are included in run provenance. Public feedback sanitizes simulator object/state details.

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --agent-profile official --task turning_on_radio --instance 301 \
  --output /path/to/fresh/run --port 29984
# Use --agent-profile official on scripts/run_codex_controller.py as well.
```

Experiment records: initial `operations/official_symbolic_20261002/` and current `operations/harness_upgrade_20261004/` outside Git. A scripted component probe is recorded separately from a fresh Astra episode. Existing batch workers continue on their frozen sources.

[Official symbolic source](https://github.com/StanfordVL/BEHAVIOR-1K/blob/b1979916ec1549b10a4e65e630bc6504a9af1b00/OmniGibson/omnigibson/action_primitives/symbolic_semantic_action_primitives.py).

## First validation — 2026-10-02

This is historical v1 evidence. The initial component probe accepted the expected
navigation failure as a diagnostic check; the current probe requires navigation
to actually return successfully. Do not read the old component total as a passing
navigation capability.

Frozen implementation: `c91a054fd25092971c5a1e1237436db5d64bef00`. Simulator host: S134 (`ZJU3DV-3090`), GPU UUID `GPU-99a7efe6-7e2d-a5b7-f781-c9856a61e8b4`. The original evaluation workers were not changed.

| Check | Result |
| --- | --- |
| CPU suite | 152 passed, seven dependency-dependent skips; 159 total |
| Scripted component probe | Ten checks passed across 200 environment steps; private scripted target identity, no model and no task-success claim |
| Native navigation | **Failed** with the expected absent-planner `AttributeError`; this is a reproduced upstream defect, not a passing capability |
| Native toggle on/off | Both completed; actual official state changes verified |
| Fresh Astra task | `turning_on_radio`, public instance 301, seed 0: **success**, official Q = 1 |
| Policy calls | `observe` → `act(toggle_on)` → `finish(achieved)` |
| Timing | Model/controller interval 49.20 seconds; one action, 100 environment steps (3.33 simulated seconds) |
| Evidence | All audit checks passed: matching clean source, exact tool/result traces, RGB byte hashes and private scoring |

Astra selected the radio in the left RGB view, invoked the original toggle primitive, and observed the green indicator in fresh RGB. It did not request navigation or perform physical button contact. Thus this result verifies the RGB-selection-to-official-symbolic-action loop; it does not establish navigation, physical manipulation or general reliability of all 14 actions. No success-rate improvement is inferred from one task or compared across different executor conditions.

The archive retains actual model output and the single provider-returned reasoning summary, with no reconstruction of hidden reasoning. The first component startup spent several minutes in camera initialization; the fresh policy startup completed in about 1.5 minutes. Startup time is separate from the 49.20-second policy interval.

[Experiment page](http://10.76.5.241:8765/official_symbolic_20261002/) · [Astra task replay](http://10.76.5.241:8765/official_symbolic_20261002/runs/mas_official_symbolic_radio_20261002/replay.html).
