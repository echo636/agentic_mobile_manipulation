---
name: behavior-experience
description: Retrieve and improve persistent BEHAVIOR task and manipulation-action experience in Robot Harness after recorded simulator trials. Use to prepare a repeat task, distill supported lessons, or revise and roll back experience skills.
---

# BEHAVIOR task and action experience

Use this from the outer development/experiment session. The in-episode robot model
uses frozen MCP documents and cannot modify the shared library.

Use the library configured by MAS_EXPERIENCE_ROOT. Reuse the user's installed
environment and repository; keep large records outside the source checkout.
The runtime and replay server addresses are operator settings.

## Retrieve before a repeated task

From the repository root, source runtime/env.sh. Inspect:

    python scripts/manage_experience_skills.py list
    python scripts/manage_experience_skills.py show --kind task --key collecting_aluminum_cans --protocol oracle_waypoints

If a task has no reviewed experience, proceed from its instruction and relevant
workflow/action skills. The index covers the current 100 public tasks; a missing
learned skill means no accumulated recipe, not a known failure or solution. The
store accepts other valid task IDs when that task is otherwise in authorized scope.

The Harness matches the current task ID and observation protocol, freezes the
selected task and available action documents, and recommends names in initialize.
Oracle experience must remain separate from rgb_only trials. Recompute objects,
coordinates and poses from fresh observations in every instance.

## Improve after a trial

Finalization automatically archives an offline candidate with source hashes,
ordered action names, failures and independent scores. A candidate is evidence,
not a learned procedure. Read the actual tool results, relevant images and any
independent validation before writing transferable lessons.

Read the current task/action SKILL.md before replacing it. Keep useful prior lessons,
revise contradicted advice, and distinguish observed conditions, procedure, result
checks, recovery and untested scope. Task skills hold task-specific sequencing;
action skills hold reusable motion/contact decisions. Keep historical coordinates
and IDs in evidence, not as instructions for the next scene.

Publish a complete replacement using a proposal JSON as specified in
[references/update.md](references/update.md). The expected revision prevents lost
updates; prior versions and evidence remain available. This is local library work,
not permission to launch extra trials, publish media, or commit/push the project
repository. The user's separate authorization to use our executable skill Git
library permits its managed local commits, as described below.

Use whole_task_success only when the independent official score is true.
Use engineering_success only with a passing independent engineering validation.
Partial progress, failed calls, sampled checks and hypotheses stay explicitly
qualified. A model finish claim is insufficient. Do not infer improved success rate
or runtime from having written or read the skill.

## Compile a stable subroutine

When the user asks to turn experience into executable actions, read
[references/executable.md](references/executable.md). Keep task sequencing in task
documents and compile only a subroutine with observable start/end conditions.
The opt-in oracle_compiled profile freezes reviewed local code; historical model
success motivates a draft but never verifies that code. The ordinary Oracle and
RGB profiles continue to use their own catalogs.
Published code uses our independent NAS Git repository: current files in the
worktree, old versions in commits/tags, media referenced by NAS path/hash. Never
merge or push to RAX. Use the management CLI so version checks and evidence rules
remain enforced.

For the RAX Explore → Compile → Rehearse workflow with `main(ctx)` controllers,
read [the RAX code experience reference](references/rax.md). Its skills live in a
separate namespace and are staged into a fresh prepared workspace; they are not
compatible with the older `execute_skill` entrypoint.
