---
name: oracle-place
description: "Transport a retained object locally into a container and verify relation, release, retreat and consecutive settling."
---

# place

Scope: oracle_waypoints; action=place; revision=2.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

## Preconditions and transport

Use for a locally retained free object with a reachable container. Choose a station covering source and destination before grasping. Unreachable IK with several saturated joints calls for withdrawing and changing the empty robot's station; a deeper crouch alone may not solve reach.

Verify actual retention and lift before transport. Author native arm/torso paths with gripper=hold and refresh the hand/object geometry after torso motion. Do not use held-object navigation as native transport in this protocol.

Plan clearance for the carried object, fingers and wrist housing. Lift above the rim before translating over the opening, then lower using the measured hand-to-object offset. Inspect the mouth and handle in current wrist/surround RGB and link geometry; the outer AABB does not certify an opening.

Supply placement and retreat paths to place. Read relation_satisfied, released, release_evidence and retreat tracking independently. EEF position inside a container or an open hand alone does not establish successful placement. If a path fails, the object may remain retained at an intermediate pose; inspect before another command.

## Settle and verify

Allow transient settling before the requested stable interval. This executor reports all requested samples in stability.samples, but stable and position_drift_m inspect only the last 10. An interval of 60 steps therefore needs independent consecutive target-position trace rows, disengagement and consistent final geometry.

The original can placement requested 90 settling steps and the first frozen-skill repeat requested 120. Both passed independent checks over their final 60 consecutive target-pose rows with zero position drift, reached retreat, final native Inside and release. This does not certify every-step Inside or whole-arm collision freedom. An extra hold can use one wait(steps=60) if needed; do not split it into 60 model calls merely to create samples when passive per-step logging already exists.

Only one can per trial was placed. The official six-can task remained incomplete; other containers and complete repeated collection are untested extensions. Evidence uses native assisted retention and official IK. Recompute all targets and offsets from the current episode.

A second can repeat independently passed Inside/release/retreat after 90 settling steps. Its last 60 placement-settling position samples had maximum drift 7.05e-6 m, so stability need not mean exactly zero drift. A subsequent single wait(60) returned consistent final geometry with both hands disengaged. The provider-interrupted continuation is recorded separately from active execution time.

## Recorded evidence

- oracle_transfer_can_placement_20261029_201056_dca3d1: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_placement_skill_r1_20261029_223245_958875: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
- oracle_transfer_can_placement_skill_r2_20261029_234018_11de7b: task=collecting_aluminum_cans, instance=301, seed=0; official whole-task success=False; engineering success=True; claim=engineering_success; grasping=assisted; fixed-floor repairs=1.
