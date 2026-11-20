---
name: behavior-make-microwave-popcorn
description: "Open, release and retreat from the microwave door with reviewed continuous hold evidence; cooking remains unverified."
---

# make microwave popcorn

Scope: oracle_waypoints; task=make_microwave_popcorn; revision=2.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

## Demonstrated scope

This experience covers opening the microwave door, releasing and retreating; popcorn cooking has no demonstrated recipe here. The original transfer supplied endpoint-only hold evidence. Two frozen-skill repeats now pass independent final-60-control-step angle checks at roughly 0.635 and 0.632 rad relative to initial, above the 0.5 rad goal. Their complete official cooking scores remain false.

## Approach and contact

Read the movable door link, hinge axis/origin, joint limits and current finger offsets. The tested microwave has no separate exact handle region; use the active wrist image to refine contact and clearance from the fixed control panel. Prepare the bent arm in clear space. Unfolding next to the counter previously made contact; retreat/reposition while empty if needed, then refresh geometry.

Both successful repeats eventually used the left arm. In repeat 2, right-arm approaches stalled before the robot changed strategy; this is a useful station/hand alternative for similar geometry, not a rule that the right arm cannot work. Retain freedom to choose the hand from the current reachable workspace.

Use articulated_handle grasp with a short joint-consistent verification path, not a free-object vertical lift. The schema still required min_lift_m >= 0.01 even though unused for this grasp subtype. Two finger contacts or a reached EEF pose are insufficient: require native retention of the intended movable link and measured motion. A failed grasp may leave the hand closed near contact. Clearance-aware adjustment while closed subsequently established a native assisted SphericalJoint in the original transfer and repeat 2; reopening by reflex would discard that state. Derive any correction from fresh observations.

## Open and certify

Author short signed hinge arcs and matching EEF orientation from current geometry. Probe a small arc, read actual joint progress and retention, then continue beyond the threshold with margin. Repeat 2 used verified relative segments of 0.06–0.12 rad after contact was established. Stop and replan after tracking failure; never navigate with the retained handle. Read current geometry again after allowing torso motion.

Release, withdraw and inspect the free hand. The peak opening can relax after release: repeat 2 went from about 0.666 rad to 0.632 rad. Judge the post-release angle, not the peak. When passive joint logging is enabled, use one wait(steps=60) and inspect its observation; the independent reviewer can check all 60 recorded native control steps. Repeat 1 used 60 wait(1) calls; repeat 2 used one wait(60), with no isolated timing comparison because prompts and action histories differed.

Require the final continuous interval to remain at least 0.5 rad open with neither hand retaining the door. Endpoint equality alone did not certify the historical run; missing joint values or trace gaps cannot certify a continuous hold. This checks recorded control steps, not every physics substep or whole-arm collision freedom. Eleven fixed floors were repaired in the tested scene with task assets/bindings unchanged. See oracle-open-door for general hinge geometry.

## Recorded evidence

- oracle_transfer_microwave_door_20261029_194648_19c826: task=make_microwave_popcorn, instance=301, seed=0; official whole-task success=False; engineering success=None; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=11.
- oracle_transfer_microwave_door_skill_r1_20261029_223245_61f9a8: task=make_microwave_popcorn, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=11.
- oracle_transfer_microwave_door_skill_r2_20261029_232922_cf704f: task=make_microwave_popcorn, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=11.
