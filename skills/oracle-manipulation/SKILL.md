---
name: oracle-manipulation
description: Plan native R1Pro manipulation in Robot Harness using six-view RGB, Oracle geometry and model-authored waypoints. Use for contact pressing, free-object grasp and placement, or hinged-door manipulation; verify actual contact, retention and motion after failures.
---

# Native Oracle manipulation

This is privileged simulation experience. The robot model chooses targets and all
poses; IK tracks them, and the simulator handles contact, native assisted retention
and object-state updates. Use the existing installation and navigation interface.

Initialize and read current geometry and camera metadata. If initialize.experience
offers a task_skill, read it for that exact task and check its demonstrated scope.
Prefer the relevant listed learned action skill (oracle-grasp, oracle-place,
oracle-open-door or oracle-press); read one of the following references when no
matching learned action skill is available:

- [Contact pressing](references/press.md): toggle regions and finger contact.
- [Elevated grasp](references/grasp.md): free objects and an upright finish.
- [Native placement](references/place.md): local transport, release and settling.
- [Hinged articulation](references/articulation.md): handle retention and hinge arcs.
- [Evidence and limits](references/evidence.md): demonstrated scope and sources.

When preparing a run, use the `oracle_waypoints` profile. Its `list_skills` /
`read_skill` tools serve frozen documents from `skill_snapshot` and record the
bundle hash. Verify actual reads before claiming skill use. The updated placement
and articulation references were written after the transfer trials described in
the evidence; those models read the earlier entrypoint and grasp reference.
There is no comparison establishing a skill-induced success-rate improvement.

## Reusable manipulation recipes

When exploring or refactoring an executable procedure, expose it through
one `ctx.call("manip", recipe=..., targets=..., parameters=..., constraints=...,
revision=...)` invocation. Keep `Manip(ctx, ...)` as its internal implementation;
the isolated runner binds only the exact frozen project's implementation and spec.
Replay shows the parent call with expandable internal move_joints/grasp steps.
Read [the recipe contract](references/manip-recipes.md) for typed inputs, explicit
navigation permissions and the current countertop-pick source example. Keep the
existing low-level tool records and independent evidence. Oracle exposes
list_manip_recipes, read_manip_recipe and manip. After empty navigation, inspect
current RGB/geometry, choose a matching recipe and call manip with local-motion
constraints. Exploration can patch its workspace recipe; ordinary Oracle episodes
use frozen trusted sources. Runtime ok is not independent acceptance.

## Decisions that transfer

- Re-read geometry after navigation or torso changes, including after failures.
  Navigation changes the base frame; discard old base-frame points. Torso motion
  changes hand/camera poses and clearance even if the base stays still.
- Bring a nearly straight arm into its reported ready posture before Cartesian IK.
  Prepare in clear space: the ready posture itself is not collision-aware. If a
  nearby counter blocks unfolding, recover compactly and reposition while empty.
- EEF origin and finger contact center differ. Use measured finger offsets and
  orientation to convert contact targets to EEF-link poses. Units are metres,
  quaternion xyzw in the CURRENT robot base frame.
- Reach safe pre-contact in a short call, inspect the active wrist, then refine the
  contact path. Internal phases do not call the VLM between control steps.
- A reached pose or two finger contacts alone do not verify retention. Compare
  `held_object`, `native_grasp.target_link`, contacts and actual object/joint motion.
  A failed call can leave contacts, a closed gripper, or a changed world; inspect
  the returned revision before deciding whether to retreat, reopen or refine.
- Change station, orientation or posture after unreachable motion. Do not repeat
  the same failed target. Follow the latest tool schema, including required fields
  that are not physically meaningful for the selected operation subtype.
- Do not navigate while retaining an object or handle when claiming native
  transport: the existing navigation executor includes ideal carried-object
  projection. Choose a station that permits the intended local transfer first.
- Keep whole-task official scoring, operation verification and engineering criteria
  separate. Report native assisted retention as assisted and preserve failures.
