# Executable experience

Use the existing V02 environment. Curated code sources are in
`program_skills/skills/<slug>/`; persistent published versions live at
`$MAS_PROGRAM_LIBRARY_ROOT/oracle_waypoints`.
`MAS_PROGRAM_LIBRARY_ROOT` can override the parent library directory; otherwise
it is inferred alongside `MAS_EXPERIENCE_ROOT`.

The parent skill_library is our own local Git repository, with no configured remote.
It is separate from both the Harness project and the RAX reference checkout. Each
publish, evidence import or status change makes a local commit; source releases have
tags oracle_waypoints/<slug>/v000001. Only current sources live in the worktree.
Prior versions stay in Git rather than duplicate revision directories. Images and
videos stay on NAS; evidence indexes contain paths and hashes, not copied media.

```bash
python scripts/manage_program_skills.py init
python scripts/manage_program_skills.py history --name place-held-object --limit 10
```

Init imports existing pre-Git records before flattening directories; pre_git_versions.json
maps earlier revisions to the import commit and paths. Inspect an older release with
git show; recover by publishing its reviewed source as a new draft. Do not reset
the shared library HEAD. Never add a RAX remote or merge/push to the reference repo.

Read the library INDEX.md and the applicable SKILL.md before changing a program.
Each source has SKILL.md, spec.json, code/run.py, and a uniquely named package
with params.py. The entry is `main(ctx, **params) -> {ok, measured, why}`.
Use only the supplied context's current observations and native tools. Keep
calibration values in params.py, not historical scene coordinates. Declare called
skills in `uses` and compose them through `ctx.run` rather than importing a path.
Dependencies are pinned at publication and cycle-checked.

Publish a draft against an independently reviewed model demonstration and one of
its recorded images. This records provenance and an image path/hash, without
copying media or rerunning the demonstration:

```bash
python scripts/manage_program_skills.py list
python scripts/manage_program_skills.py publish \
  --source program_skills/skills/<slug> --expected-version 0 \
  --demonstration-run /absolute/path/run --image /absolute/path/run/env/frames/image.jpg
```

The CLI checks expected-version, commits version history, and always publishes
new code as draft. Uncommitted changes in the published worktree block writes and
new snapshots; preserve them and edit a source proposal instead of overwriting them.
Use the `oracle_compiled` profile only for explicitly scoped
compiled-code experiments, with official OmniGibson IK and native assisted grasping.
The robot selects a program and current parameters through execute_skill; it cannot
submit arbitrary code. Draft execution requires allow_draft=true. Each native child
call remains in the normal event ledger with origin=compiled_skill. The same
simulator owner thread executes the whole subroutine; failed checks stop it and
return the latest observation without an automatic retry or forced release.

After a finished real run, inspect `env/program_runs/<invocation_id>.json`, child
tool evidence, actual frames and passive physics traces. Review the invocation's
start/end conditions, not just the final episode. A tool ok or whole-task result
alone cannot certify the subroutine. Save a separate `program_validation.json` in
the run root with this structure (copy the exact hashes from the actual record):

```json
{"invocations": [{"invocation_id": "run-...", "scope": "program_invocation",
  "code_sha256": "...", "runtime_sha256": "...",
  "invocation_sha256": "SHA256 of env/program_runs/run-....json",
  "passed": false, "checks": {}, "notes": "Record actual review results here"}]}
```

Do not prefill passed=true. For the door, independently check the continuous
60-control-step angle trace after release/retreat. For placement, inspect the
60-step position trace; native stable alone covers only the last 10 samples.
Record failed and unreviewed runs too. Review additions append new ledger entries;
old evidence is not rewritten:

```bash
python scripts/manage_program_skills.py record-run --run /absolute/path/run --invocation-id run-...
python scripts/manage_program_skills.py verify --name <slug> --expected-version 1
python scripts/manage_program_skills.py deprecate --name <slug> --expected-version 1 --reason 'Observed failure; replacement and conditions'
```

Verification requires two distinct runs with independently passed invocation
reviews, exact code/runtime/dependency versions, no intervention inside the
subroutine, matching recorded images, and independent engineering or official
success. Demonstrations and CPU fixtures never count. New behavior or changed
Harness Python requires a new draft and new evidence; changes during a run refuse
execution. Status transitions and failures are retained. Future episodes receive
the new frozen version; active episodes retain their snapshot.
Each snapshot/invocation records library_commit. Evidence imports cross-check the
invocation's code/version/runtime against that Git publication.

RAX format reference: commit db744f5950582c7c301f249192c0ed3c76517dd8,
`skills/continual-learning/references/library-format.md`. Only the format and
iteration principles are adapted. No RAX simulator or controller was installed.
