---
name: place-held-object
description: Place a retained object in a calibrated container using current geometry and verified native release and retreat.
status: draft
robot: R1Pro
entry: code/run.py
version: 3
uses: []
---

# Place a held object

## When to use

Use after native assisted grasp/lift is already verified, from a station covering both source and container. Model demonstrations cover one soda can and the BEHAVIOR collecting_aluminum_cans bucket at instance 301, seed 0. At initial v1 publication the compiled code had no unattended simulator verification; consult the active status and appended run evidence for later validation. The caller identifies the opening in current RGB and supplies the intended object-origin release point in the destination object's frame.

## Preconditions

Code checks current object IDs, a movable source, assisted retention and a local transfer no longer than 0.60 m. A container's AABB is used for height clearance, not to certify an opening. Calibrate the release point and inspect rim/handle/wrist clearance before invocation. The other arm should already be compact.

## Procedure

Observe; compute the current EEF-to-object position offset; lift the entire carried object above the destination bounds; translate and lower without changing EEF orientation; call native place with a retreat and 90 settling steps. Never navigate while holding. Stop at the first failed native result and return the current observation, retaining any object still held.

## Code

Entry is `main(ctx, arm, target_object_id, destination_object_id, release_position_in_destination_m, allow_trunk=False)` returning `{ok, measured, why}`. The model tool additionally requires the current revision and explicit draft opt-in. Parameters and limits are listed in `spec.json`; tunable motion constants and calibration notes are in `code/place_held_object/params.py`. The supplied context is shared; the code never opens another simulator/client.

The runtime appends each invocation and its tool evidence IDs to the episode's `program_runs/`. Library review records are append-only `evidence/runs.jsonl`. Frame references identify the latest recorded six-view observation.

## Checks

Require native Inside, released, reached retreat and stable separately. Stable currently covers the last 10 settling samples; a strict 60-step claim needs independent passive-trace review. Object translation uses current geometry rather than old coordinates. IK reach does not establish whole-arm collision freedom.

### Perception checkpoints

| Step | Frames | Question | Pass if | On fail |
| --- | --- | --- | --- | --- |
| Before invocation | Current wrist and surround | Are the retained object and intended mouth correctly identified, and is the corridor clear? | Caller supplies a calibrated mouth point and verified retained object | Reposition while empty or correct perception; do not invoke blindly |
| Return/failure | Returned six-view frame refs | Is the object inside and the hand clear? | Images agree with the measured relation/release | Caller inspects and recovers; no automatic retry/drop |

## Failure modes

Wrong held object, absent geometry or excessive transfer fails before motion. Native path/relation/release failure stops immediately; there is no automatic reopen or retry. The hold may remain active. Different containers, orientations and poses are unverified. This is privileged Oracle code, not RGB-only manipulation.

## Evidence

The published version stores reviewed demonstration provenance and the NAS reference-image path/hash under `evidence/`; raw media stays outside Git. Model demonstrations motivate this procedure but do not count as unattended code successes. Status remains draft until two distinct independently reviewed unattended runs of this exact code pass. A behavior change publishes a new draft version; Git history retains old versions and failures.
