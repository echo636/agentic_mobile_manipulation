---
name: behavior-turning-on-radio
description: "Reuse the observed contact-press strategy for turning on a radio in an Oracle R1Pro scene."
---

# turning on radio

Scope: oracle_waypoints; task=turning_on_radio; revision=1.
Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.

Applies to the tested R1Pro, native assisted retention and official OmniGibson IK. Tool schemas and current observations take precedence. The notes are experience for planning, not executable macros or a statistical success guarantee.

## Preconditions and strategy

Inspect the current radio state and toggle region. If it is already on, preserve it. Prepare the arm away from obstacles, navigate while empty as needed, then refresh base-frame geometry. Use the measured finger offset to place an eligible finger against the current button region, with a short approach and sufficient contact dwell.

## Checks and recovery

Read current local state and contact after every press, even a failed call. The successful historical attempt set the device on although the overall approach reported reached=false; the independent whole-task evaluator passed. Do not repeat an already effective press and toggle the radio off. Earlier near-region approaches and fleeting contacts did not turn it on: revise contact geometry and clearance rather than repeat the same target.

## Demonstrated scope

One official whole-task success, instance 301 seed 0, four surround cameras plus Oracle geometry. Use available wrist images in newer episodes; this run did not establish a wrist benefit. Related action skill: oracle-press.

## Recorded evidence

- oracle_official_ik_20261029_081435_71e43b: task=turning_on_radio, instance=301, seed=0; official whole-task success=True; engineering success=None; claim=whole_task_success; grasping=assisted; fixed-floor repairs=0.
