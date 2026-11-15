---
name: resource-dependency-planner
description: Compare executable processing, delivery, and resource-return plans for the declared two-station symbolic task with one or two raw items, a tool, and a capacity-limited box. Use when choosing processing sites, transport methods, or interleaved resource returns in this model.
---

Plan from the public task using the bundled R4 method library. This experimental
skill is a planning component; native object geometry and robot execution remain
outside its model.

Read [the public model](references/public-model.md) before encoding an instance.
Confirm the initial state, actions, capacities and terminal goals match that
contract. A task with additional locations, occupied surfaces, uncertain
retention or intermediate milestones needs a different model; report the
mismatch instead of dropping those conditions.

Compare complete alternatives. Keep material prerequisites and each resource's
remaining uses. A resource return follows its own last use; independent loading,
delivery and processing may interleave. Retain direct transport and unused-box
alternatives, and charge staging, handling and resource restoration.

Write a request containing `public_model` and `proposal` to the experiment output
directory. Include an existing action list as `proposal`, or `[]` when none exists.
Run this skill's `scripts/plan.py` with `--input` and `--output` paths. Use the
actual result: `best_actions`, `best_cost`, `best_origin`, and, when present,
`best_macro_graph` and `best_macro_order`. An unsupported input is not a plan.

The program preserves a better or tied complete proposal. It combines frozen
transport methods with schedules of small macro dependency graphs, then checks
every emitted action against the public transition model. It searches only its
declared method families, so call the result the best compared plan. Its costs
are symbolic; physical execution still needs a separately validated mapping.

The bundled program has been evaluated through a controller-operated CLI.
This packaged skill's autonomous selection and its text alone are not established
by those experiments. Read [the method boundary](references/method-boundary.md)
when interpreting coverage or attributing a result.
