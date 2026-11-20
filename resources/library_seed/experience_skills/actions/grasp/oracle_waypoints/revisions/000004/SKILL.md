---
name: oracle-grasp
description: "Plan free-object grasp and recovery from current finger geometry, then verify native assisted retention and world lift."
---

# grasp

Scope: oracle_waypoints; action=grasp; revision=4.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

## Applicability

Free-object grasp planning for the tested R1Pro with official IK and native assisted retention, including a bottle and soda cans. Anchored handles instead need oracle-open-door and a joint-consistent probe. Category and AABB do not define a collision-free grasp pose.

## Contact and lift decisions

Choose an empty-robot station that permits the intended finish or local placement. Read current wrist calibration and measured EEF-to-finger offsets; convert the desired finger contact center to an EEF-link pose. Prepare a bent arm in clear space. Ready postures do not avoid collisions. After base/torso changes, refresh geometry and discard stale base-frame targets.

Use a short pregrasp call, inspect the active wrist, then refine approach and a small lift path. For a free object, require the intended held object/link, actual world lift and continued retention; reached IK, gripper closure and two contacts are separate observations. Native assisted retention must be described as assisted, not friction-only grasping.

On failure inspect the latest revision, current contacts, hand closure, object pose and held state before choosing reopen, retreat or a clearance-aware correction. The bottle and first can repeat both recovered from failed grasps. In the original can transfer, changing the empty robot's station resolved repeated unreachable approaches. A deeper crouch or replaying saturated joint targets is not a universal fix.

Verified lifts in the original can transfer and first repeat were about 0.112 m and 0.105 m. These are historical outcome checks, not future lift coordinates or a minimum imposed on other tasks. Preserve clearance for nearby container rims, handles and the wrist housing. Do not navigate while holding when claiming native transport; use local arm/torso paths.

## Evidence limits

Sources support engineering subgoals in the recorded instances, not all shapes, seeds or whole tasks. Read current tool feedback even after a previously successful recipe; failure recovery remains necessary. No controlled comparison establishes that skill use increased success or reduced wall time.

A second can repeat recovered from failed free-motion approaches, refined the pregrasp using the current wrist view, and then its first grasp operation verified assisted retention and about 0.115 m lift. The repeat was resumed in the same episode after a provider interruption; it does not supply an uninterrupted timing comparison.

## RAX bottle code: supported operation and failed boundaries

A frozen full-controller rehearsal completed a native assisted bottle grasp, local retraction, upright restoration and final continuous 60-step hold, without a live agent or failed motor calls. Initial exploratory recovery is separate from that code result. Two later formal executions failed, so the published RAX bottle code remains a draft with one successful independent run, not a generally verified grasp controller.

The first failure occurred AFTER a verified grasp and about 0.102 m initial world lift: fixed-trunk retraction hit the elbow lower limit, missed its EEF target by about 0.128 m and left the retained bottle too low with the torso still bent. Retention alone does not preserve the lift/upright goal after a failed move. Refresh current hand/object height, trunk pose and joint-limit slack before recovery. Another instance failed the fixed right-arm pregrasp by about 0.442 m before contact, with several joints near upper limits. The current draft's unconditional right-arm choice and fixed retraction are not broadly demonstrated.

A better empty-base station, another arm or a different checked retraction is a candidate for new exploration, not an already validated remedy. The successful and failed stations differed, but the trials do not establish a universal safe distance threshold. Code and compact evidence are at archive://v02/runtime/skill_library/oracle_waypoints/rax_skills/grasp-countertop-bottle; keep the ordinary Oracle action protocol separate from that RAX main(ctx) interface.

## Recorded evidence

- oracle_bottle_wrist_upright_20261029_113405_30f048: task=sorting_bottles_cans_and_paper, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=11.
- oracle_transfer_can_placement_20261029_201056_dca3d1: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_placement_skill_r1_20261029_223245_958875: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_placement_skill_r2_20261029_234018_11de7b: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- rax_bottle_exploration_301_20261031: task=sorting_bottles_cans_and_paper, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=11.
- rax_bottle_exploration_302_20261031: task=sorting_bottles_cans_and_paper, instance=302, seed=0; official whole-task success=False; engineering success=True; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=11.
- rax_bottle_formal_305_20261031: task=sorting_bottles_cans_and_paper, instance=305, seed=0; official whole-task success=False; engineering success=False; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=11.
- rax_bottle_formal_306_20261031: task=sorting_bottles_cans_and_paper, instance=306, seed=0; official whole-task success=False; engineering success=False; claim=partial_or_failure; grasping=assisted; fixed-floor repairs=11.
