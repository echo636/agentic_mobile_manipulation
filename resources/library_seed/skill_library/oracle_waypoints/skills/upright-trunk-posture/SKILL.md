---
name: upright-trunk-posture
description: Derive R1Pro's third trunk pitch and verify upright posture with at most one move, skipping an already-satisfied target.
status: draft
robot: R1Pro
entry: code/run.py
version: 1
uses: []
---

# Upright trunk posture — draft, no simulator validation

This is a narrow R1Pro hardware-posture draft, motivated by a sign error in manual trunk planning. It is not a floor-grasp skill, collision planner, transport controller or whole-task solution. CPU tests verify code control flow only. No simulator run of this code has been performed. A later reviewed publication must remain draft and final replay must explicitly set `allow_draft=true`.

## Hardware contract

The pinned R1Pro asset orders trunk joints as `[torso_joint1, torso_joint2, torso_joint3, torso_joint4]`, with axes `+Y,+Y,-Y,+Z`. Near upright, `q3=q1+q2`; the third value can be positive or negative. The caller chooses `q1_rad`, `q2_rad` and `yaw_rad` from current geometry. The code computes q3 and never accepts an independently chosen q3.

Only the calibrated R1Pro asset and native assisted grasping are supported by this draft. The observation does not expose an independently verifiable robot-model ID or joint-axis list. Code checks the four observed joint-limit values against the calibrated R1Pro signature and the `torso_link4` +Z measurement convention; these are compatibility checks, not proof of hardware identity. The caller must use R1Pro. Changed robot definitions require a new review.

## Prepare before invocation

Both hands must be empty. Code checks current `held_object` and native assisted `object_id` for both arms, and rejects nonempty or missing retention evidence. Physical grasp mode is excluded because the available native-held fields cannot establish an empty physical grasp.

The caller must put both arms into a suitable compact posture and inspect current surround/wrist RGB, arm/body geometry, obstacles and the entire intended trunk/arm sweep. Explicitly pass `prepared_for_trunk_motion=true` only after doing so. The backend reports current finger contacts and conservative body AABBs, not an independently reliable two-arm swept-clearance metric. Code rejects observed finger contact when contact data is available; unavailable contact data leaves the visual preparation requirement in force. Neither absence of finger contact nor the caller acknowledgement certifies collision safety. Use a level base for world-vertical uprightness; the code checks actual world tilt after motion.

## Parameters and execution

`main(ctx, q1_rad, q2_rad, yaw_rad, prepared_for_trunk_motion, expected_height_interval_m)` returns `{ok, measured, why}` through the existing frozen runtime. All five parameters are required in `spec.json`; use `expected_height_interval_m=null` when no height constraint is intended. A non-null pair is a caller-selected inclusive interval for the actual `upper_body_height_above_base_m` field. It is neither an EEF height nor a scene/world coordinate.

1. Read the current serialized observation without an extra render. Require finite current joints, limits and actual posture measurements. Derive q3 and check all four targets against the current lower/upper limits with margin.
2. If every current joint is within 0.01 rad of its target, current measured tilt is within 2° and the optional height interval already holds, return `measured.already_satisfied=true` without any physical call or extra observation/render.
3. Otherwise call native `move_joints(group='trunk', positions_rad=[q1,q2,q1+q2,yaw])` once. The runtime supplies the current revision. On a successful response, read the returned current observation and verify empty hands, available finger contacts, actual joint error within 0.03 rad, world tilt within 2° and the optional height interval. Return `already_satisfied=false`. A close joint target alone cannot bypass a failed measured tilt or height check.

Any failed precondition or native motion stops immediately. There is no automatic retry, extra wait, reset, navigation, release, gripper command, arm preparation or restoration of an earlier posture. Failures may leave the trunk at a partially reached pose; inspect the returned observation and evidence before deciding a new action. Runtime evidence preserves the attempted native call and its returned state.

## Calibration and deliberate draft choices

`code/upright_trunk_posture/params.py` makes these engineering choices explicit:

- A **0.06 rad margin** from every current joint limit. Historical R2's near-limit `[1.8,-2.75,-0.95]` had only 0.0326/0.0425 rad margin on its first two joints; its first joint reached 1.74325 and tilt was 3.242°. The well-tracked `[1.5,-2.6,-1.1]` retains at least 0.1925 rad. The margin excludes the former candidate; it is not a measured safety envelope.
- A **0.03 rad maximum actual joint error**, stricter than the native tool's 0.10 rad reached threshold. This rejects R2's 0.05675 rad near-limit discrepancy.
- A **2° maximum measured world tilt**. R2's upright examples measured under 0.01°; this draft threshold permits tracking variation and still rejects its 3.242° example. No real execution has yet validated this threshold for this code.
- A **0.01 rad no-op joint-error threshold**, solely to avoid nearly zero moves when the measured posture and optional height already satisfy the request. This is an engineering threshold, not a collision or stability guarantee; it does not relax the post-move checks.

Known single-run hardware posture evidence includes `[q1,q2,q3]=[1.5,-2.6,-1.1]` (0.00950°, height 0.60663 m) and `[-0.9,1.8,+0.9]` (0.00924°, height 0.87742 m). These are candidate hardware configurations, not recommended paths from every starting state. Historical yaw and all can/bucket/EEF coordinates are deliberately absent from this skill. Trunk-enabled IK later in a task can change the posture, so any prior upright result must be rechecked afterward.

## Evidence and review status

Calibration: `archive://v02/runtime/operations/can_multiagent_20261030/torso_calibration.md` and `archive://v02/runtime/operations/can_multiagent_20261030/torso_calibration.json`, including pinned URDF/frozen-source hashes and historical R2 event IDs 00041, 00045, 00049, 00067, 00075, 00083 and 00087. These are offline maintainer evidence, not required model-time reading dependencies. The historical trial motivates the mathematics and candidate criteria; it is not an unattended code success.

Synthetic CPU tests and their records remain at `archive://v02/runtime/operations/can_multiagent_20261030/proposals/upright-trunk-posture/tests/` and the adjacent `validation/`, using the frozen `SkillContext` loader. They are offline development evidence, not runtime dependencies. Any publication must include a maintainer-reviewed real demonstration image and validation evidence. Do not create or substitute synthetic robot evidence. Promotion to verified requires the existing independent validation workflow on this exact code and runtime.
