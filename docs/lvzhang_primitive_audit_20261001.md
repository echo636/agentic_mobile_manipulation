# lvzhang primitive and program interface audit

Status: **static source inspection passed; no new simulator experiment performed**.

This inspection follows the request to examine lvzhang's code-composition interface before implementing the Astra experiment. It does not establish task success, physical reliability, or a benchmark comparison.

## Source and scope

- Repository: <https://github.com/llz3724/agentic_mobile_manipulation>
- Inspected branch: `my_mm`
- Inspected commit: `c00938f8ccdd59974ecaddd28cfd7ce86fa6ba0f`
- Commit time: 2026-10-01 21:31:27 +0800
- Separate reference checkout: `repos/lvzhang_20261001_c00938f8` in the workspace.
- Earlier reference checkout remains unchanged at `619605e362cecf0e54eedd4dbe34636671d5cf15`.
- Authoritative public declaration: `mobile_manipulation/agent/mcp/catalog.py`.

The current catalog has **42 public components**, including functions, a type, exceptions, and a constant. It has **11 motion/planning interfaces**, one of which only plans. These counts must not be interpreted as 42 physical actions or 11 complete semantic skills. Older files in the repository are not necessarily part of the current public contract.

## Outer MCP tools

`mobile_manipulation/agent/mcp/server.py` exports three tools:

| Tool | Contract |
| --- | --- |
| `execute_code(program: str)` | Execute ordinary Python defining zero-argument `run()`. The program receives the declared component functions and a mutable `progress` dictionary. |
| `observe(observation_id=None)` | Read the current or historical observation without advancing physics. |
| `view_image(image_path=None, observation_id=None, view='head')` | Display a saved image or an observation's RGB image. |

Thus the model can submit a program that calls many primitives, evaluates feedback, and takes branches within a single MCP call. It need not issue a separate model request for each controller step.

## Current motion and planning interfaces

The source locations below refer to the pinned checkout's `mobile_manipulation/agent/mcp/actions.py`.

| Interface | Input and behavior | Return and limits | Line |
| --- | --- | --- | --- |
| `plan_motion(*, hand, pose)` | Plan free-space joint motion to an end-effector pose; no physical stepping. | `MotionPlan`: `planned` or `rejected`, observation, hand, bound goal, read-only trajectory, reason. | 132 |
| `execute_motion(motion_plan)` | Execute the selected plan without replanning or automatic retry. | `MotionResult`: `reached`, `incomplete`, or `rejected`, measured endpoint and errors. Any physical step after planning invalidates the plan. | 163 |
| `approach(*, hand, pose)` | Local end-effector motion within 0.15 m and 30 degrees. | `MotionResult`; bounded feedback, speed and commanded-acceleration limits. Arrival tolerance is 1 mm / 1.5 degrees. | 346 |
| `lift(*, hand, distance_m=0.05)` | Raise the end effector along estimated +Z while preserving its initial XY and orientation. | `MotionResult`; 0–0.30 m, segments at most 0.05 m, no collision planner. Arrival does not establish target retention. | 360 |
| `reset_posture(*, raised_hands='none')` | Physically restore compact torso/arm joint posture; options `none`, `left`, `right`, `both`. Preserve gripper commands. | `PostureResult`: `reached` or `incomplete`, measured joint errors. No collision planner; not a scene reset or base self-righting action. | 97 |
| `open_gripper(*, hand)` | Request opening for 90 control intervals, or 3 seconds of simulation time. | `Observation`; does not guarantee complete opening or successful placement. | 281 |
| `close_gripper(*, hand)` | Request closing for 90 control intervals. | `Observation`; gripper HOLD alone does not establish target identity or task success. | 294 |
| `forward(goal)` | Translate to goal XY, preserving heading; ignore goal height and orientation. | `MotionResult`; local controller, maximum 0.5 m/s, no obstacle avoidance or automatic retry. | 404 |
| `turn(goal)` | Turn to goal yaw through the shortest angle, preserving XY. | `MotionResult`; maximum 0.5 rad/s; stop on arrival, stalled progress, or a finite attempt bound. | 416 |
| `step(command)` | Execute one end-effector or base velocity command for 1/30 second. | `Observation`; expects a `Velocity` command, not an arbitrary raw environment action tensor. | 86 |
| `servo(controller, max_steps=300)` | Repeatedly evaluate a Python callback on the latest observation, then execute its velocity command. Callback returning `None` stops the loop. | `Observation` with execution summary; the default is 300 intervals. `None` disables this count limit and requires the program's own finite boundary. | 201 |

`Pose` uses position in metres and an xyzw quaternion. Its default `estimated` frame is initialized from the starting base frame and maintained through odometry; `robot` means a base-relative frame bound at action entry. This is not a guarantee of drift-free global localization.

The public catalog does **not** expose semantic `grasp`, `place_on_top`, `place_inside`, `open`, `close`, `push`, or `navigate_to` functions. The old combined `move` is also no longer public. Compared with the earlier checkout, it was replaced by separate `plan_motion` and `execute_motion`, and `lift` and `reset_posture` were added: 39 declared components became 42.

## Supporting components

The generated program also has access to components for:

- Observation and geometry acquisition: `observe`, `segment`, `in_view`, `depth_point`, `locate`, `search`, `measure_with_local_views`, `track_object`, `update_object`.
- Geometry and contact construction: `fit_plane`, `fit_obb`, `wrist_facing_pose`, `fingertip_pose`, `contact_goal`, `transform`, `pose_error`, `pose_reached`, `base_reached`, `bounded`, and `Pose`.
- Command construction: `ee_velocity`, `ee_pose_velocity`, `base_velocity`. These construct commands; they do not themselves advance physics.
- Tracing and failures: `stage`, `ActionRejected`, `SlipDetected`, `ObjectLost`, `ObjectAmbiguous`, `ObjectMeasurementUnavailable`, `LocalObservationExhausted`, and `MEASUREMENT_ERRORS`.

Program observations include RGB, depth, camera calibration, estimated base/end-effector poses, gripper state, and proprioception. Access by the generated program is broader than our existing RGB-only model interface. A default RGB-D CuRobo V2 backend performs free-space planning; an explicit `GAP_CUROBO_BACKEND=oracle` alternative also exists. These configurations must be distinguished in any comparison.

## What code composition means

`program.py` checks Python syntax and the `run()` entry, compiles and executes the submitted program, and calls `run()` on the simulator's owning thread. Ordinary Python branches, loops, helpers, and feedback controllers determine what happens next. `@stage` records calls and exits; it does not route a graph or retry an action automatically.

A conceptual pick procedure is:

```text
observe / segment / locate
    -> compute task-specific contact and pre-grasp poses
    -> plan_motion -> inspect plan -> execute_motion -> inspect endpoint
    -> approach -> inspect endpoint
    -> close_gripper -> inspect grasp evidence
    -> lift -> verify the intended object remains held
```

This diagram is not a tested task program. The generated program must supply grounded geometry, choose a hand, handle failed measurements and motions, and verify effects. A place procedure additionally selects a release pose, moves/lowers the object, opens the gripper, and checks the result. Drawer opening requires handle acquisition and a suitable pull/contact controller; it is not an official symbolic `OPEN` call.

The program returns `status` (`completed`, `partial`, `blocked`, or `failed`) and a nonempty `reason`. The server adds execution and observation IDs. Python local variables do not persist between calls, although the simulation state does. A program's `completed` result is not an independent BEHAVIOR success score.

## Implication for our exploration branch

Our existing interface gives the model `act` with a semantic action and a visual target. The executor supplies most navigation, grasp, and placement details. The inspected lvzhang interface places more of those decisions in generated Python, while retaining motion planning and feedback controllers below that program. It is similar to code-as-policy at this architectural level; it does not mean the LLM generates every motor command directly or that all skills are reliable.

[HomeBody's public project description](https://tml.stanford.edu/homebody/) presents a VLM orchestrating motor skills such as picking, placing, drawer interaction, and navigation. Its [linked repository](https://github.com/Stanford-TML/homebody) displayed “Code coming soon.” when inspected. We have not obtained or executed a public HomeBody runtime.

Two experimental interfaces therefore need distinct names and records:

1. Direct official semantic actions: expose OmniGibson's action primitives explicitly, retaining their built-in execution behavior.
2. Physical primitive composition: execute model-authored code that composes pose planning, local motion, gripper control, and feedback loops, as in the inspected lvzhang interface.

The second interface is lower level than the first and changes what the model must solve. Neither is automatically a raw joint-command experiment. The current branch, `explore/astra-primitive-composition`, records this distinction before implementation. No new executor, model rollout, or success-rate result is claimed by this audit.

## Evidence and validation

Workspace artifacts:

- `operations/astra_primitive_exploration_20261001/lvzhang_audit.json`: source revision, source hashes, component inventory, extracted contracts, and audit process provenance.
- `operations/astra_primitive_exploration_20261001/validation.json`: five passing static catalog checks.
- `operations/astra_primitive_exploration_20261001/journal.md`: chronological operations record.
- `reports/astra_primitive_exploration_20261001/index.html`: detailed source-audit report.

The checks verify the pinned source, 42 unique declarations, implementations of all 11 motion/planning interfaces, removal of public `move`, and absence of the listed semantic actions from the active catalog. They do not validate physical feasibility. No source snapshot or existing evaluation process was modified.
