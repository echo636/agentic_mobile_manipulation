# Manip recipe contract

Use a named recipe plus typed arguments for a reusable sequence of joint moves,
end-effector paths, contact/gripper commands and verification. Preserve the
ordinary Harness tool ledger; the recipe executes through `ctx.call` only.

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

`ctx` is the supplied isolated-runner context. Keep `main(ctx)` as a thin task
entrypoint: empty navigate, then this one Manip call. The runner binds the tool to
the project's own frozen `Manip(ctx, ...)` implementation and `spec.json`.
Its internal move_joints/grasp/wait calls stay in the raw ledger and appear as
expandable children in replay. This is one public procedure invocation; its
physical effects are sequential and are not rolled back on failure.
The source proposal is `program_skills/rax/grasp-countertop-bottle/` in the
Harness repository. Read its SKILL.md and spec.json. The atomic-call interface
is a new draft, adapted from the controller accepted twice on 308 and once on 309;
those historical trials called the Python implementation directly and do not
physically qualify this changed runtime. Never rewrite them as historical tool calls.

## Inputs and execution

Oracle agents discover recipes through `initialize.manip_recipes` or
`list_manip_recipes({})`, inspect `read_manip_recipe(recipe=...)`, then select
`manip(recipe=..., targets={"object": {"task_name": "wine_bottle.n.01_1"}},
parameters={...}, constraints={"allow_base_motion": False}, revision=...,
allow_draft=True)`. Defaults fill omitted parameter/constraint fields. All current
source proposals are drafts, so draft use needs the explicit flag. The RGB tool
catalog remains separate. The agent chooses a recipe from its applicability;
the Harness does not silently replace native grasp calls.

The manipulation sequence begins after the agent navigates while empty and
checks actual reach/clearance in current geometry and wrist RGB. Navigation is
ideal/kinematic. A recipe failure preserves the scene. Inspect its phase and
actual retention, change a concrete hypothesis, then edit the project returned
by read_manip_recipe using read_workspace_file/patch_workspace_file. Do not
repeat an unchanged failing call or reset to hide failure. Recipe-local baseline
notes are separate from the agent's task_notes in direct manip tool executions.

In ordinary Oracle episodes, recipe code is frozen at episode startup; the agent
cannot edit shared code there. Use the exploration pipeline to optimize recipes.
After development works, register a full navigate+Manip main(ctx) with the recipe
implementation inside that project. Rehearsal/formal ctx.call("manip") executes
only that exact frozen project. It cannot access the mutable workspace recipe.
New sessions require two independently accepted unattended rehearsals of the same
bundle/runner before submit. Code edits start a new bundle's acceptance count at
zero. The operator fixes the metric: official full-task success, or a separately
hashed engineering evaluator for an explicitly scoped subtask. status reports
counts. Development, clean exit and controller ok never count by themselves.
Same-instance repeats establish repeatability only, not cross-instance verification.

- `recipe`: a registered procedure identifier. Declare a separate parameter
  schema per recipe; reject unknown names/fields rather than silently guessing.
- `targets`: named roles mapped to `EntityRef(task_name=...)` or JSON
  `{"task_name": ...}`. Resolve exact object/link identities in the current scene.
  A place recipe can add a destination; an articulated recipe can add a joint.
- `parameters`: recipe-specific finite numbers, enums and checked integers.
  Document defaults, units and calibration scope. Joint posture presets are
  empirical robot-specific preparation, not collision-planned trajectories.
- `constraints`: execution permissions enforced before dispatch. Local
  manipulation defaults to no base motion. If empty-hand ideal navigation is
  supported, expose an explicit opt-in and report it. Held navigation remains
  forbidden by the native_only Harness policy. Disabled trunk motion also applies
  to preparation; do not move other joints first when trunk preparation is
  already known to be forbidden.
- `revision`: expected current observation version. Reject stale versions before
  motion. With no explicit version, reacquire current observations. Recompute
  geometry after base/trunk motion, and stop on ambiguous retention or failure.

EEF paths specify metres, quaternion xyzw and their reference frame. Derive them
from fresh geometry. IK tracks those paths; a recipe wrapper alone adds no
collision planner, grasp sampler or cuRobo capability. Keep contact/closure,
lift and retention checks distinct, even when one API call contains them all.

## Outputs and evidence

Return `ok`, recipe identity, effective parameters/constraints, current phase or
failed phase, compact measured metrics and capture references. Stop at the first
failed action without hidden retry/release. Endpoint checks and continuous
per-step independent acceptance are different claims; whole-task official scoring
is separate again. Preserve previous code/runner hashes, failed runs and actual
authorship. A format or behavior change needs new registration and independent
physical qualification before its status can be upgraded.
