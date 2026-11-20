---
name: oracle-open-door
description: "Recover articulated-handle contact, author hinge arcs, release and review a continuous post-release opening interval."
---

# open-door

Scope: oracle_waypoints; action=open-door; revision=2.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

## Geometry and approach

Read the movable link, ancestor joint, axis origin/direction, current angle and limits. A link AABB is not an exact handle region. The tested microwave lacked a separate handle region, so current wrist RGB, camera calibration and finger offsets refined contact clear of the fixed panel.

Prepare a bent arm in clear space and refresh geometry after station changes. Use a short safe pre-contact call before refining the handle approach. In both successful repeats the robot eventually used the left arm; repeated stalled right-arm approaches in one repeat motivate trying another station/hand when appropriate, not prescribing one hand for every door.

## Retention and recovery

Use grasp_kind=articulated_handle with a small joint-consistent verification path; do not lift an anchored handle vertically. The tested schema still required min_lift_m >= 0.01 although unused for that subtype. A valid field does not imply a vertical lift.

Require native_grasp.target_link for the intended movable link, not merely the object ID or two contacts. Native assisted retention has contact/raycast/time-window conditions. Failed grasp calls can leave a closed hand beside the handle; inspect current contacts, wrist view and retention before reopening. In the original transfer and repeat 2, a clearance-aware adjustment while closed subsequently established a native SphericalJoint. This is conditional recovery, not a direction or offset to replay.

## Hinge motion

Probe a short arc and verify measured joint progress, intended-link retention and parent drift. Author all points from the observed hinge: p_new = hinge + R * (p_old - hinge), with a matching EEF orientation rotation and normalized xyzw quaternion in the current base frame. Use rotate with the observed joint ID and signed relative angle. Recompute after base/torso changes, permit torso IK only with clearance, and revise after tracking/contact failure. Never navigate while retaining the handle.

Repeat 2 verified seven relative arc segments of 0.06–0.12 rad once retention was established. Those increments illustrate small-step feedback; current geometry and limits still determine the path. Open beyond the requested threshold with margin because the peak can relax after release.

## Release and continuous hold

Release, retreat, then measure the post-release angle and free hand. In repeat 2 the angle relaxed from about 0.666 to 0.632 rad. Use one wait(steps=60) for a required hold when passive per-control-step joint logging is enabled; internal samples do not require 60 model calls. The outer reviewer checks the final consecutive interval, every value available and above threshold, both hands released and native motion. The robot uses returned observations and should qualify continuous evidence until independently reviewed.

Two frozen-skill repeats passed strict final-60-step checks at approximately 0.635 and 0.632 rad. The historical transfer only recorded equal 60-step endpoints at 0.653 rad and remains endpoint-only evidence. Missing values or trace gaps cannot certify continuous hold. These checks cover recorded control steps, not every physics substep or whole-arm collision freedom. Cooking, other door types and drawer translation remain unverified; all sources used assisted retention and official full-task scores stayed false.

## Recorded evidence

- oracle_transfer_microwave_door_20261029_194648_19c826: task=make_microwave_popcorn, instance=301, seed=0; official whole-task success=False; engineering success=None; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=11.
- oracle_transfer_microwave_door_skill_r1_20261029_223245_61f9a8: task=make_microwave_popcorn, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=11.
- oracle_transfer_microwave_door_skill_r2_20261029_232922_cf704f: task=make_microwave_popcorn, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=11.
