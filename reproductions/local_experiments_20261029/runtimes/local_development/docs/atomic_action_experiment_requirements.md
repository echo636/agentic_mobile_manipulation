# Atomic-action model experiment requirements

This is the acceptance contract for the added RGB atomic actions. Read it before
starting, auditing, or publishing an atomic-action experiment. The implementation
and current evidence are described in [atomic_actions.md](atomic_actions.md).

## Scope and controller

- Use only tasks and instances in the pinned public BEHAVIOR 100-task set. The
  current single-task comparisons use public instance `301` and seed `0`; record
  any change to either value on the result page.
- Start from the task instruction and the initial front/back/left/right RGB
  images. The model must choose the next primitive and current-image pixel
  through the real MCP `act` interface, inspect returned RGB and feedback, and
  call `finish` itself. Do not supply object IDs, depth, evaluator state,
  hand-selected action sequences, or target pixels to the controller.
- Use `gpt-6-astra` with reasoning effort `low` for **new** experiments. Retain
  already completed `high` attempts as historical evidence, label their actual
  effort on every result and history link, and do not rerun them solely to change
  effort. Never silently mix efforts in a reported cohort.
- Test each of the eight added `act` primitives: `wipe`, `cut`, `soak`,
  `sweep`, `spray`, `spread`, `hang`, and `vacuum`. Existing `attach` provides
  the pattern for model-selected grounding and executor verification; `hang`
  applies the same checked attachment state to a compatible support.

## Acceptance and review

An individual run passes only when all of these are recorded and true:

1. The actual model controller terminates with a formal `finish`.
2. Its ordered MCP calls match the simulator's recorded calls and arguments.
3. The model selects the target primitive in `act`, and the simulator reports
   that primitive executed successfully.
4. The independent official task evaluator reports success for the **whole**
   task. An `act` success or model claim alone is insufficient.
5. The continuous five-view episode video passes validation, and
   `scripts/build_review.py` produces the review page from that run's recorded
   evidence.

Preserve every attempt, including failures and partial evidence, in its own
directory. The audit must show the task, instance, seed, model, reasoning effort,
RGB configuration, ordered model `act` calls, official result, controller
closure, alignment, video status, and source/configuration provenance. Keep the
credential outside run files and logs. Scripted executor probes and manual pixel
selections are diagnostic evidence only; label them separately from autonomous
model trials.

Publish audited runs on the default internal visualization page:
[atomic-action model review](http://10.130.140.115:8769/autonomous_model_index.html).
Each attempt needs a link to its `build_review` five-view page, continuous video,
and machine-readable audit. Show failed attempts alongside successes and label
the model, effort, and task for **each** attempt, including historical `high`
attempts. A page or table is a view of the immutable run evidence, not a
replacement for it.

## Public-task feasibility

The pinned 100-task set has official target tasks for six added actions:
`wipe` → `clean_a_keyboard`, `cut` → `chopping_wood`, `sweep` →
`sweeping_garage`, `spray` → `spraying_for_bugs` or `spraying_fruit_trees`,
`hang` → `hanging_pictures`, and `vacuum` → `vacuuming_floors`.

The same pinned set has no positive `Saturated` goal for `soak`; its positive
`Covered` goals are spraying tasks supplied with sprayers, not a `spread` goal.
No public instance of `staining_wood_furniture` is present in the pinned
manifest. Therefore `soak` and `spread` can be exposed and tested through MCP,
but cannot be claimed as completed task-level successes within the requested
public-100-only scope. Report that limitation explicitly rather than substituting
a private task or treating action execution as task success.
