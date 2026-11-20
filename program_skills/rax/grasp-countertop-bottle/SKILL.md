---
name: grasp-countertop-bottle
description: Execute or adapt the countertop_pick Manip recipe for an upright R1Pro countertop-bottle grasp, lift and stable hold, with explicit motion permissions and typed parameters.
---

# Countertop pick through Manip

This is a **draft atomic-call refactor** of the frozen controller accepted twice
on instance 308 and once on 309. Read spec.json and evidence.json. No robot or
model was run for this revision; those videos do not qualify changed code. The scope
is an upright countertop bottle with Oracle 3D, stock six cameras, official
OmniGibson IK and native assisted retention. It does not solve complete sorting.

## Call

Oracle now exposes this function through the `manip` MCP tool. Discover its
contract with list_manip_recipes/read_manip_recipe. Supply EntityRef JSON in
targets, the current revision and allow_draft=true for this explicitly requested
development experiment. Tool calls default to local manipulation after separate
empty-handed ideal navigation. Exploration edits its own seeded controller;
ordinary episodes execute an immutable startup snapshot.

The outer task controller navigates while empty, then makes one public call:

```python
result = ctx.call(
    "manip",
    recipe="countertop_pick",
    targets={"object": {"task_name": "wine_bottle.n.01_1"}},
    parameters={"arm": "right", "ready_posture": "countertop_ready",
                "lift_m": 0.10, "hold_steps": 60},
    constraints={"allow_base_motion": False, "allow_trunk_motion": True},
    revision=current_revision,
    allow_draft=True,
)
```

EntityRef is exported by controller.py; JSON {"task_name": "..."} is also accepted.
`targets.support` optionally binds another countertop; its default is
`countertop.n.01_1`. Resolve bindings from current geometry, never old runtime IDs.
main(ctx) runs the explicit empty approach and then ctx.call("manip"). The runner
binds this tool to the same read-only bundle's Manip implementation and spec.
The Manip call keeps base motion disabled. One recorded parent contains the
move_joints/grasp/wait children; replay can expand these actual internal calls.
Physical actions execute sequentially, with no rollback or hidden retry.

## Parameter contract

| Parameter | Type and default | Meaning |
| --- | --- | --- |
| recipe | string, required | Currently only countertop_pick is implemented |
| targets.object | EntityRef, required | Upright bottle on the bound countertop |
| parameters.arm | auto / left / right; auto | Auto chooses the nearer prepared shoulder; a forced arm is not silently switched |
| parameters.ready_posture | countertop_ready | The inherited, reach-dependent empirical joint-pose family |
| parameters.lift_m | finite number in [0.05, 0.15]; 0.10 | Commanded vertical lift displacement, metres |
| parameters.hold_steps | integer >=60; 60 | Physical control steps, split only to satisfy the native 150-step call limit |
| constraints.allow_base_motion | boolean; false | Explicit opt-in to the old ideal checked empty navigation |
| constraints.allow_trunk_motion | boolean; true | Permit empirical upright trunk preparation |
| revision | nonnegative integer or None | Expected current revision; None refreshes observation |

Unknown fields, invalid numbers and permissions are rejected before motion.
Only the inherited 0.10 m / 60-step / automatic-arm behavior has historical
source evidence. Other parameter combinations remain untested.

## Execution and postconditions

Start with both hands empty and the bottle upright. Acquire current object,
counter, arm/finger, torso and obstacle geometry. If the station needs approach
and base motion is disabled, return approach_required before any joint movement.
If the computed trunk preparation is forbidden, stop before the arm preparation.
No constraint silently enables another backend or collision planner.

The internal phases retain the recorded sequence: optional empty approach;
checked empirical arm/trunk preparation; refreshed geometry; finger-offset-derived
EEF poses; exactly one native grasp with pregrasp/approach/closure/lift; endpoint
verification and batched hold. Low-level tools remain in the normal ledger.
The empirical joint interpolation and IK paths are not collision-planned. The
recipe adds neither cuRobo nor an automatic grasp sampler.

Return ok, recipe/target bindings, effective parameters and constraints, phase on
failure, compact metrics and capture references. Require one intended assisted
base_link retention, actual lift >=0.05 m, torso tilt <=5 degrees, standing height
>=initial-0.08 m and endpoint EEF-relative drift <=0.03 m. The commanded lift_m
is a path displacement; acceptance uses measured object height. Runtime endpoint
checks report continuous_hold_validated=False. An independent saved per-step
audit must establish the continuous final 60-step condition. Official complete
sorting success is separate. Stop on failure without retry, release or held
navigation; preserve the actual final grip and evidence.

Baseline notes are episode-local and target-bound. Do not reuse another bottle's
initial height. Historical bottle_initial_v1 notes are accepted only for the
original wine_bottle.n.01_1 / countertop.n.01_1 binding.

## Evidence and release

The immutable source and replay remain at the paths recorded in evidence.json.
Its earlier development failures and the previous library candidate's failures
are retained there. New hashes/parameter combinations require new registration
and physical qualification. Cross-instance acceptance cannot be inferred from
same-instance repetition or CPU checks. Keep this source proposal outside the
published library while its worktree has uncommitted edits. Publish a new draft
through the managed curator only after those edits are resolved; do not relabel
historical hashes, agent authorship, failures or videos.
