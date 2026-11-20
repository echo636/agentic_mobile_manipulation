---
name: open-retained-door
description: Follow measured positive hinge arcs from retained door contact, then release, retreat and wait.
status: draft
robot: R1Pro
entry: code/run.py
version: 3
uses: []
---

# Open a retained hinged door

## When to use

Use after the chosen hand has native assisted retention on the movable door link. Reviewed model demonstrations cover the BEHAVIOR microwave, instance 301, seed 0, including left-hand SphericalJoint retention. Contact acquisition, navigation and popcorn cooking are outside this subroutine. At initial v1 publication the compiled implementation had no unattended simulator verification; consult the active status and appended run evidence for later validation.

## Preconditions

Code checks the current exact retained object/link, a directly observed revolute joint, positive opening, current limits and at most 0.80 rad additional travel. Caller supplies the absolute joint goal, the minimum released angle in the same joint convention and a calibrated retreat direction in the current base frame. Original target angles near zero are evidence, not a universal closed-door reference.

## Procedure

Re-observe; generate short signed arcs around the current hinge axis/origin and rotate EEF orientation consistently. Re-read measured joint progress and retained link after every native rotate. At the goal, release, retreat along the caller's clear direction, then use one wait(60). No model request occurs between these internal operations. No navigation, state setters or object projection is used.

## Code

Entry is `main(ctx, arm, target_object_id, target_link, joint_id, goal_position_rad, minimum_released_position_rad, retreat_direction_base, allow_trunk=False)`. See `spec.json` for the exact parameter schema and `code/open_retained_door/params.py` for constants and calibration. Ordinary calls share the supplied context; failure reports `{ok: false, measured, why}` and current frame refs. The runtime records every child call, revision and result in `program_runs/` and the normal event ledger.

## Checks

Each rotation must verify retained link and measured progress. Release/retreat must succeed. Both wait endpoints must meet the released-angle threshold and 60 control steps must elapse with neither hand retaining the target. These endpoint checks do not certify intermediate angles: independent review must inspect the passive per-control-step joint trace before claiming a continuous hold. Peak opening is insufficient because the door can relax after release.

### Perception checkpoints

| Step | Frames | Question | Pass if | On fail |
| --- | --- | --- | --- | --- |
| Before invocation | Active wrist plus surround | Is the intended movable link retained, and are the arc and retreat corridors clear? | Caller confirms current geometry/clearance and exact native link retention | Recover contact or change station/hand before invoking |
| Return/failure | Latest returned frame refs | Is the released door open and the hand withdrawn? | Images agree with measured opening and release | Inspect state and recover without dropping or blind retry |

## Failure modes

No retention, wrong joint/link or out-of-domain goals refuse motion. A tracking failure, lost retention, insufficient progress or relaxed post-release angle stops and returns control. There are no automatic retries, continued arcs after failure, or forced releases on error. Positive microwave-style opening only; negative opening, other doors, drawers and cooking remain unverified.

## Evidence

The published version stores model demonstration provenance and a NAS reference-image path/hash; raw media stays outside Git. Historical model success is not evidence that this code ran unattended. Draft becomes verified only after at least two distinct independently reviewed unattended runs with frames of the exact code. Changed behavior needs a new draft version and new evidence; Git retains prior versions.
