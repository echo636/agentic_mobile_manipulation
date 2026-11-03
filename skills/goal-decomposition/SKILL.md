---
name: goal-decomposition
description: Preserve task outcomes in a goal tree and optimize explicit operation DAGs for efficient RGB execution.
---
# Plan outcomes, optimize operations, verify results

Keep two linked representations in one task note. The **goal tree** records what must be true; the **operation DAG** records the work that can make it true. Read [references/working-state.md](references/working-state.md) before the initial write.

| Goal-tree layer | Meaning |
|---|---|
| Root | Describe the requested final state in your own words. |
| Intermediate layers | Group contributing results; repeat these layers where useful. |
| Leaves | Independently verifiable results, with quantity, destination, state and evidence requirements. |

Depth and child counts follow the task. Preserve requirements and meaningful intermediate parents. Goal status is your belief, not evaluator truth. A completed operation does not automatically complete a goal.

Build the operation DAG across known unfinished branches: discovery/access, tool preparation, processing, assembly, device use, delivery, arrangement and final checks. A node is a concrete local operation with a location, prerequisites, resource conditions, predicted effects and cost. Split work whose locations, resource use or ordering can differ; do not hide a whole branch behind “prepare everything” or “deliver all supplies”. Link producing operations to goal IDs with `achieves`. Keep unknown results explicit and add discovery work; refine the graph as observations resolve them.

Edges express necessary prerequisites, not a preferred chronology. Do not force independent branches into global search/processing/delivery phases. Reuse one node for shared preparation. When separate branches describe the same one-time preparation, give those equivalent nodes a common `share` label so the optimizer can consider merging them. Never share labels across different items, repeated consumption, or checks at different times.

Propose feasible alternative subgraphs where execution could differ: shared versus separate preparation, workstation or tool choices, compatible processing batches, staged versus direct assembly, carrier versus individual delivery. Expand each alternative into its actual operations, including loading, unloading, setup and cleanup required by that approach. Preserve the same results and dependencies. Support capacity, compatibility, access and processing feasibility from observations and available tools; a cheaper estimate cannot justify an unsupported action. Transport batches and processing batches need not match.

The program compares graph replacements, shared work and operation order, charging local work, movement and process waiting. Distinguish workstations even within one room. Use estimates consistent with observed tool time; keep movement separate from local work. The aim is low total execution cost, not fewest nodes. Physical/navigation uncertainty remains possible.

Write/update, read coverage, missing work, graph changes, costs and alternatives, then select a `plan_id` and `operation_id`. Selection saves that operation graph and identifies the local work to execute using current RGB. Replies retain the selected operation and measured tool/simulation time. Complete its supported local steps; when it finishes, fails or new information changes the plan, update affected operations, goals and resource beliefs together, then compare again. Do not mark predicted effects as observed facts.

For unattended processing, separate starting from checking its result. Describe process duration and independent work's simulation progress so the optimizer can compare productive work against waiting. Model/network waiting does not advance simulation. An elapsed estimate permits a check, not a completion claim.

Track identifiable items or spatially distinct groups with their current location, state, remaining work and evidence. Counts alone cannot distinguish unprocessed items from processed ones. If handling mixes or spills them, reopen affected results and restore correspondence before an irreversible operation. Items already found do not establish the total; retain unexplored locations and uncertain identities.

Verify results bottom up and revisit results disturbed by later handling. Before finish, reconcile every requirement and member with RGB and public receipts. Preserve valid progress and report unresolved work rather than treating successful tool calls or a complete predicted graph as task success.
