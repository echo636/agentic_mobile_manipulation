# Custom long-horizon pilot

This pilot uses BEHAVIOR assets in joint scenes with new goals. It is not a
BEHAVIOR100 result or an official challenge submission. The user authorized
this extension beyond the historical public100-only experiment scope.

The runtime recipes in `task_designs/runtime/` are construction inputs. A recipe
is not an executable benchmark instance until a frozen scene and its SHA-256
are attached and the checks below pass. Generated scenes and experiment records
live outside Git.

## Tasks and dependencies

| Task | Coupled work | Independent work |
| --- | --- | --- |
| Workshop | Pack all five tools at storage → transport the closed loaded box → unpack on the workbench → return the empty closed box | Store two books and discard three cans |
| Lunch | Retrieve and cook two hotdogs → pack four food items → deliver lunch and tea | Set four dining items, discard three cans, store two leftovers and the board, restore appliances |
| Movie night | Clear and dust the table before serving; cook popcorn → switch microwave off before removal → serve popcorn | Store toys and trash; arrange six dining items; return brush and restore microwave |

The instruction is a short household request. Both controllers receive the same
public household context describing the menu, storage conventions, completion
criteria, and any historical constraints. This resolves ambiguity without giving
the controller object IDs, private predicates, the evaluator DAG, or a reference
action sequence. Context must be evaluated as part of the input, rather than
claiming the controller inferred an undisclosed preference from a short phrase.

## Independent evaluation

`CustomTaskBackend` reads actual OmniGibson object states.
`TemporalGoalEvaluator` combines simultaneous terminal conditions with committed
historical milestones and public transport constraints. Model notes are never
used to score the task. A milestone may be consumed later, such as unpacking a
previously delivered box. A later correct final pose cannot erase a transport
constraint violation. Partial credit is the fraction of terminal, historical,
and constraint conditions satisfied; it is explicitly **not official Q**.

Hypothetical placement candidates do not award milestones. An executor's
transactional rollback discards staged motion violations only when both the
tracked poses and predicates actually return to their previous values. Required
entities destroyed during execution make the task fail, instead of silently
omitting their goals.

## Validation and experiment protocol

1. Construct a joint scene from pinned asset definitions and verify initial
   assertions. Validate particle-system names against the pinned assets before
   simulator initialization; an inactive valid system differs from a misspelled
   system. Record asset, dependency, source, and instance hashes.
2. Execute a full reference trajectory through the same checked motor executor.
   Privileged target selection is permitted only for this feasibility diagnostic.
   Exercise different legal branch orders and evaluator negative cases. Preserve
   every failed attempt. A setter success or process exit does not prove task
   feasibility.
3. Restore the frozen scene in a fresh simulator. After camera creation, refresh
   contact data and state caches, then recheck the actual policy-ready conditions.
4. Freeze source, scene, and public input. Launch matched autonomous trials with
   `gpt-6.1-sol`, reasoning `low`, 200 act/look attempts and no episode wall
   deadline. Failed act/look calls count. This is a pilot budget, not evidence
   that a task requires 200 useful actions.
5. Both arms share tools, generic `task_notes`, executor, other skills, scene,
   public context, and budget. Only the hierarchical goal decomposition skill
   and its read instruction differ. Both enable the harness's mandatory-notes
   gate (`require_task_notes=true` in the actual pilot protocol): the first
   act/look requires a nonempty note in either arm. The control can write freeform
   notes and has no prescribed goal tree. Report this shared intervention when
   interpreting the ablation; it is not an entirely unprompted planning baseline.
   Do not merge this pilot with BEHAVIOR100 or
   the six-identity I/O repair cohort.
6. Audit controller closure, ordered MCP calls, independent score, observation
   alignment and continuous video. Replay shows model-authored goal-tree versions
   separately from offline actual-state evidence. The latter never enters policy
   observations. Report failed attempts and unknown scores explicitly.

Report measured useful reference/model actions, navigation, recovery attempts,
and dependency structure. Task length must not be inferred from the configured
step cap or padded with redundant motion.
