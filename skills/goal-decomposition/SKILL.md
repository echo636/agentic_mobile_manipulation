---
name: goal-decomposition
description: Preserve task outcomes in a goal tree and search grounded operations for efficient RGB execution.
---
# Understand results, search operations, execute and reconcile

Keep a result tree and an operation plan in one task note. Read [references/working-state.md](references/working-state.md) for the format before the initial write.

| Result-tree layer | Meaning |
|---|---|
| Root | Describe the requested final state in your own words. |
| Intermediate layers | Group contributing results; repeat layers where useful. |
| Leaves | Required final results with quantity, destination, state and evidence requirements. |

Depth and child counts follow the task. Preserve explicit requirements; separate assumptions. Goal status is your belief, not evaluator truth.

Describe current observations in `planning.state` and each result's terminal `condition`. Temporary access, staging and preparation states belong in operator preconditions/effects, not competing final goals. Describe available local work in `execution.operators`, including where it occurs, its preconditions, effects and cost. Keep operators for known unfinished work across all branches through their terminal results, not only the next phase. The program selects, combines, omits or repeats these operators and generates the operation DAG; you need not author complete alternative plans.

An operator is concrete work with one location and resource use. Split work when location, resources or ordering can differ. Include supported capabilities that offer meaningful choices, not just the actions of one preselected sequence. Resource acquisition, staging, access and cleanup have effects and cost; completing retrieval once does not permanently keep a tool in hand. Describe effects that can disturb earlier results too. Distinguish workstations within a room and account for movement separately from local work.

Only model physically plausible operations supported by RGB and the available tools. Respect support, tool access, contents and capacity. Containers useful for transport may be unsuitable for cutting or processing. Preserve identifiable members and required quantities when describing batch work; do not assume a carrier or processing device can handle an arbitrary collection.

Unknown work remains explicit. Add necessary discovery results and concrete access/inspection operators, then refine the operation pool as observations resolve identities, locations and capabilities. An inspection changes what is known, not the physical ingredient state. Do not invent a producer for an unlocated item merely to make the graph complete.

Write/update, read the generated graph, coverage, missing conditions, costs and search limits. `comparison.missing_producers` identifies terminal states absent from both current beliefs and operator effects: add omitted known work, or retain the uncertainty and plan discovery. The program already selects the first operation of its lowest estimated cost plan; execute its supported local work from current RGB without a separate selection call. Use `select` only to override that choice. At completion, failure or changed information, update observed states, affected goals and available operators together, then search again. Predicted effects never count as observations. Partial coverage is not whole-task optimization, and an empty predicted plan calls for verification, not automatic success.

For unattended processes, separate starting from checking. Estimate independent work's `simulation_seconds` from public action receipts; omission means zero, so the search cannot credit that work against a pending wait. Keep this simulation progress separate from wall-time work costs: model/network waiting does not advance simulation. An elapsed estimate permits a check, not a claim that cooking or another process succeeded.

Track recognizable items/groups with source, location, state, remaining work, count and evidence. A transfer receipt confirms movement, not the identities of all requested contents; keep unverified members unresolved. Counts alone cannot distinguish unprocessed from processed items. After mixing or spilling, restore correspondence before irreversible work. Keep unexplored locations and uncertain identities visible.

Verify results bottom up, including results disturbed by later handling. Before finish, reconcile every requested result and member with RGB and public receipts. Neither a complete predicted DAG nor successful tool calls establish official task success.
