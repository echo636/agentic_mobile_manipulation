# RAX controller experience

This is the outer-session workflow for reusing `main(ctx)` code with our Harness.
Keep the installed simulator/environment. Do not confuse it with the older
`oracle_compiled` `execute_skill` API.

New reusable procedures use a named `Manip(ctx, recipe=..., targets=...,
parameters=..., constraints=..., revision=...)` function inside the controller.
The recipe declares typed inputs and local-motion permissions; `main(ctx)` remains
the pipeline entry and calls `ctx.call("manip", recipe=..., ...)` once after empty
navigation. The host binds only the current read-only bundle's implementation
and spec; never resolve this call against a mutable workspace. The real parent
call is recorded, with expandable move_joints/grasp children in replay.
The maintained source proposal for `countertop_pick` is
`program_skills/rax/grasp-countertop-bottle/` in the Harness repository. It is a
draft atomic-call refactor, not a newly verified runtime. Historical accepted
bundles called the Python implementation directly; do not relabel those records
as public Manip calls. Follow the Oracle skill's
`references/manip-recipes.md`; preserve old bundles and published-library edits.
If the library has uncommitted edits, keep changes in a source proposal until
the library worktree is clean; do not stage or overwrite unrelated edits.

New pipeline prepare seeds current Manip source proposals directly into private
workspace code projects. Discovery and execution use list_manip_recipes,
read_manip_recipe and manip after empty navigation. The agent patches only its
own recipe controller after inspecting failures. Old sessions/snapshots are not
upgraded automatically. Ordinary Oracle episodes expose the same tools against
frozen trusted recipes; recipe editing belongs to exploration.

New prepare defaults require two independently successful unattended rehearsals
of the identical bundle/runner, with distinct instances. The operator can choose
same-instance repeatability and a pinned independent engineering evaluator for a
subtask; this does not qualify cross-instance verified status or official full-task
success. Historical sessions without qualification retain their original clean-run
admission rule; never present that compatibility rule as independent success.

Find current entries in:

    $MAS_PROGRAM_LIBRARY_ROOT/oracle_waypoints/RAX_INDEX.md

Read the selected `rax_skills/<name>/SKILL.md`, `spec.json` and compact
`evidence.json`. Check the task/subgoal, observations, code/runner hash, failed
attempts and current status. For the single-countertop-bottle task, the entry is
`grasp-countertop-bottle`. It requires native assisted retention and permits only
empty-handed ideal base navigation. It does not solve complete bottle/can/paper
sorting.

After the ordinary pipeline `prepare` creates a new session, but before its first
episode, stage starting code without making any robot or model call:

    python scripts/stage_rax_skill.py \
      --library "$MAS_PROGRAM_LIBRARY_ROOT" \
      --session /absolute/path/to/the/new/prepared/session \
      --name grasp-countertop-bottle --project bottle

Add `--allow-draft` only when deliberately testing a draft. The helper requires a
clean library Git, checks published hashes, refuses active/previously used
sessions and existing projects, and records the source commit privately. Only
the controller and its instructions enter the agent's workspace; raw evidence,
old coordinates and formal schedules are not copied into its inputs.

The agent can then read its staged code, recompute current geometry and decide
whether to reuse or iterate. Register and rehearse with the new frozen session
runner even if the Python file is unchanged. Previous verification is evidence
about the recorded code/runner, not certification of a changed runtime.

After recorded trials, score the declared engineering goal from the raw per-step
trace and inspect actual images/video. Keep the original full-task score
separate. `clean` or controller `ok` alone is not success. A code revision needs
two independent unattended successes with the identical bundle/runner before
verified status; keep failures in the evidence index. Store one current skill
directory with source, instructions and compact hashed evidence; local Git
commits/tags preserve history, and raw media stays outside Git on NAS.

The bottle experiment's curator command is recorded in its `spec.json` as
`publisher`. That command enforces expected versions, the library lock, clean
Git and same-bundle independent engineering evidence. It does not call the old
ProgramLibrary verification CLI or claim that it certifies RAX controllers.
