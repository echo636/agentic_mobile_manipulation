---
name: oracle-upright-trunk-posture
description: "Derive R1Pro upright trunk pitch correctly and verify actual posture; optional empty-handed draft code skips already-satisfied moves."
---

# upright-trunk-posture

Scope: oracle_waypoints; action=upright-trunk-posture; revision=1.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

## R1Pro calibration and conditions
For the calibrated R1Pro trunk, joint order is torso_joint1..4 and axes are +Y,+Y,-Y,+Z. On a level base the near-zero-pitch upright constraint is q3=q1+q2. Derive q3; its sign can be positive or negative. Check all four current joint limits without independently clipping q3. Yaw is chosen from current clearance, not copied from an old scene.

The hardware candidates [q1,q2,q3]=[1.5,-2.6,-1.1] and [-0.9,1.8,+0.9] measured approximately 0.00950 and 0.00924 degrees of world tilt in historical R2, with torso-link4 heights above the base of 0.60663 and 0.87742 m. These are hardware configurations, not collision-safe paths or EEF heights. Near-limit [1.8,-2.75,-.95] missed the first joint and measured 3.242 degrees despite tool reached=true. Prefer a reasoned height/clearance choice with margin over repeated saturated joint attempts.

## Prepare and measure
Trunk movement sweeps both arms and cameras. Inspect current RGB, fingers and surrounding geometry and compact the arms as appropriate before moving. Verify the actual body_posture.upper_body_tilt_deg and joint positions afterward; command algebra and native reached alone do not prove uprightness. Any later allow_trunk=true IK can change the posture. Avoid a new near-zero joint command when the actual posture already meets the intended condition.

## Optional executable experience
In oracle_compiled, inspect list_programs/read_program for upright-trunk-posture. Its v1 is draft and needs explicit allow_draft=true. It accepts model-selected q1/q2/yaw, derives q3, uses current limits with a 0.06 rad margin and checks actual error <=0.03 rad and world tilt <=2 degrees. Both assisted hands must be empty and the caller must first inspect/prepare the swept path. An already-satisfied pose (joint error <=0.01 rad plus tilt/optional height checks) returns without physical motion; otherwise it executes at most one move_joints. No automatic retry, release, arm preparation or collision-safety guarantee is provided. Normal oracle_waypoints can apply the same hardware relation manually; code availability is profile-specific.

## Evidence limits
The cited R2 run passed its one-can engineering placement validation; the six-can official task was incomplete. This posture calibration is supported by its measured states and the pinned URDF, including an offline 22-observation forward-kinematics cross-check. The new posture program has 19 CPU checks and zero real simulator invocations so far. Neither those checks nor historical manual motion verifies this code or establishes faster performance, whole-task success, collision freedom or real-hardware suitability.

## Recorded evidence

- oracle_transfer_can_placement_skill_r2_20261029_234018_11de7b: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
