# Astra direct motor experiment

Branch: `explore/astra-primitive-composition`. Protocol: `rgb_physical_joint_program_v1`.

This opt-in experiment lets Astra write bounded Python programs that directly command official OmniGibson joint-position and holonomic-base velocity controllers. The existing semantic action profiles remain available with their original defaults. This is a new control condition, not a claim of improved benchmark performance.

## Interface

Launch `manipulation_agent.vision_cli --agent-profile motor` and use the same profile in `scripts/run_codex_controller.py --model gpt-6-astra`. The MCP catalog contains `describe_controls`, `execute_code`, `observe`, the three asynchronous observation tools, and `finish`. It excludes `act`, ideal `look`, GT navigation and semantic manipulation skills.

`describe_controls` returns static joint names/order/limits, units, and function contracts. It does not return current joint positions, robot pose, scene objects, depth or evaluator state. The model still receives front/back/left/right RGB from four fixed cameras; no wrist camera is added.

Inside `execute_code(program, revision)`:

| Function | Meaning |
| --- | --- |
| `base_velocity(x=0, y=0, yaw=0, steps=30)` | Body-frame forward/left linear velocity in m/s and yaw rate in rad/s. Maximum planar speed 0.25 m/s and yaw magnitude 0.5 rad/s. Adds one counted zero-command braking step. |
| `joint_delta(group, delta, steps=30)` | Move `arm_left`, `arm_right` or `trunk` relative to measured joints at entry. Seven arm joints or four trunk joints, in the returned order. Interpolate a fixed joint target; at most 0.35 rad per joint and 0.5 rad/s commanded ramp. |
| `gripper(hand, opening, steps=30)` | Ramp left/right finger joints to an opening fraction: 0 closed, 1 open. Physical contacts only; this does not prove a grasp. |
| `hold(steps=15)` | Hold previous arm/trunk/gripper targets while commanding zero base velocity. |
| `observe()` | Record a fresh four-camera capture and return its public metadata. The model sees the returned RGB after the MCP call; end the program when a new visual judgment is needed. |

Each motor primitive allows 1–90 control steps. A program has a maximum of 240 physical steps, 16 motor attempts, eight internal observations, 10,000 interpreter operations, and 16 nested function calls. Episode budgets apply as well. Failed primitive attempts consume action budget. Commands that exceed a remaining step budget are rejected before they move the robot.

Example of a short program (illustrative, not a grasp policy):

```python
def run():
    base_velocity(x=0.1, steps=15)
    gripper(hand="right", opening=1, steps=15)
    return {"status": "partial", "reason": "Inspect fresh RGB before selecting the next motion"}
```

`run()` must return `status` (`completed`, `partial`, or `blocked`) and a nonempty `reason`. Program completion never closes the episode or establishes task success. `finish` triggers the independent, private BEHAVIOR evaluator.

## Execution boundary

The program is parsed and interpreted as a restricted Python subset. It supports plain functions, local variables, numeric arithmetic, JSON collections/indexing, conditionals and bounded loops. It does not execute arbitrary host Python; imports, attributes, filesystem/network access, classes, comprehensions and indirect calls are unavailable. This is a language boundary, not a general-purpose OS sandbox. Only JSON values and explicit primitives cross it.

All simulation access stays on the existing owner thread. Each physical action calls `env.step` and advances official metrics and the video timeline. No object grounding, semantic state setter, attachment, pose teleport, ideal carry, GT path planner, CuRobo planner or IK planner executes on this path. The robot uses `grasping_mode=physical`. The existing private camera calibration/depth artifacts remain offline and are not callable from the program.

`joint_delta` uses measured robot joints privately to construct a relative command, just as a motor driver would. Those measurements do not enter the model's observations. Unselected joints retain their last commanded targets. Joint limits are checked, but collision avoidance and task-level recovery remain the model's responsibility. Stopping the base command does not guarantee instantaneous physical braking.

This first implementation deliberately exposes joint motion. It does not reproduce all eleven interfaces in the [lvzhang audit](lvzhang_primitive_audit_20261001.md), notably pose planning, contact geometry and RGB-D measurements. It also does not reproduce HomeBody's higher-level pick/place motor skills. Results across these interfaces must be labeled separately.

## Records and replay

The normal episode and controller recorders are retained. `events.jsonl` includes generated programs, primitive arguments, results and errors. `motor_steps.jsonl` records every actual action vector, robot joint response and termination flag for offline audit. Public RGB and offline state are kept separate. Video retains all physical steps and its frame sampling metadata. Replay treats `execute_code` as a motor action and retains the verbatim model output and provider-returned reasoning summaries.

Source/asset/task hashes, host, interpreter, GPU UUID, PID/unit, and output paths are recorded. A scripted component probe is not a model rollout; a mock test is not simulator validation; successful execution is not task success.

```bash
# CPU contract and regression tests
PYTHONPATH=src python -m unittest discover -s tests -v

# In an already prepared OmniGibson runtime, with a fresh output directory:
PYTHONPATH=src python scripts/probe_motor.py --output /path/to/new_probe
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --agent-profile motor --task turning_on_radio --instance 301 \
  --port 29983 --output /path/to/new_episode
```

Current experiment records live outside Git at `operations/astra_primitive_exploration_20261001/`. Consult their structured statuses for the actual validation level; this document does not certify task performance.

Model integration follows [OpenAI's Astra model reference](https://developers.openai.com/api/docs/models/gpt-6-astra) and [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), retaining the existing login and JSON event recorder. No credentials are copied into this repository.

## First validation and task attempt — 2026-10-01

Frozen source: `d3611a0e98a98e266247072c192d4e70bf21f521`. Simulator: OmniGibson 3.9.2 / Isaac Sim 5.1, S134 (`ZJU3DV-3090`), GPU UUID `GPU-99a7efe6-7e2d-a5b7-f781-c9856a61e8b4`. Task and asset hashes are in the run record.

| Validation | Result |
| --- | --- |
| CPU suite | 143 passed, seven dependency-dependent skips; 150 total |
| Scripted physical components | 13 checks passed over 56 physical steps |
| Physical base movement | 0.02773 m measured displacement |
| Physical arm movement | Requested 0.05 rad; measured 0.04983 rad |
| Physical gripper movement | Opening and closing both measured |
| Astra autonomous task | `turning_on_radio`, public instance 301, seed 0: **failed**, Q = 0 |
| Model/controller lifecycle | Completed normally; explicit `finish(blocked)` |
| Trace/observation alignment | Passed; matching source, tools, returned results and actual RGB bytes |

The autonomous attempt used 17 MCP calls, 36 motor primitives and 1,139 physical steps. The model/controller interval was about 341 seconds; simulation time was 37.97 seconds. All physical steps were recorded, with explicit frame holds between fresh renders. The archive contains 12 provider-returned reasoning summaries, verbatim public output, all generated programs, and per-step control vectors. It does not expose hidden internal reasoning.

The model approached the table, moved the right arm, attempted contacts around the radio's controls, and changed its view. It reported that it could not reliably identify or confirm power activation from RGB. Independent evaluation also found the radio goal unsatisfied. No motor tool error occurred in this attempt. These observations establish a functioning RGB → code → physical action → RGB loop; they do not establish reliable switch manipulation or an improved success rate. A single attempt is not a comparative benchmark.

An earlier scripted probe failed before motion when the launcher's `TasksMax=256` prevented Isaac Sim from creating threads. The separate retry raised the limit to 2,048 and passed. Both attempts are preserved; the failed startup is not counted as a model task attempt.

[Lab experiment page](http://10.76.5.241:8765/astra_primitive_exploration_20261001/) · [Autonomous task replay](http://10.76.5.241:8765/astra_primitive_exploration_20261001/runs/mas_astra_motor_radio_20261001/replay.html).
