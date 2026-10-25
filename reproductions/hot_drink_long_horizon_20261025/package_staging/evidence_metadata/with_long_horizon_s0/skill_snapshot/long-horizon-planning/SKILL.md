---
name: long-horizon-planning
description: Plan interdependent object or room changes, track individual items and completion evidence, and revise routes or staging after feedback.
---
# Long-horizon planning

The instruction defines the goal. RGB and bounded tool feedback are evidence; the plan is a revisable hypothesis. Choose every action and pixel yourself.

1. Before the first motor action, build a compact goal ledger and dependency DAG. Each node needs a result, actual prerequisites and completion evidence. Separate individual known items from category summaries. For unknown container contents, keep an inspection/clearance obligation open; a successful operation on one item does not establish the count. Newly seen items extend the ledger.
2. Make dependencies local to what is actually needed. One available transport unit may be processed while other units are still elsewhere; completing all transport is not automatically a prerequisite for processing the first unit. A summary node can track the final aggregate without blocking ready work. Choose among ready nodes using observed access, current hand state, required door operations, staging and return trips.
3. Compare the next useful sequence before a long trip: destination preparation, transport, temporary placement, retrieval and return. Reconnaissance while empty-handed can be necessary for unknown access or unsafe staging, but it also costs a return trip. If a carrier can be safely staged at the destination, consider making the first visit with it. Choose from current evidence; no universal room order or fixed route is prescribed.
4. Before every tool call, supply the required `decision`: `state` (observed facts and uncertainty), `active_subgoal`, `reason` (immediate purpose) and `expected` (confirming feedback). This is a concise public decision, not private chain-of-thought. For initialize, the scene is not yet observed.
5. After each result, revise beliefs about the hand, doors, visible items, locations and completed placements. Advance an item only after successful action feedback and consistent RGB; leave failed or obscured work pending. Reuse a successful approach or staging landmark while it stays applicable. Read pick-and-place/references/staging-and-doors.md before a carrier move or hand-freeing placement. Keep a prepared destination open for its remaining batch when safe; close it once that batch and its uncertainties are resolved.
6. Replan after a changed prerequisite, a newly exposed item, or a recovery that recreates the same obstruction. Before another move, ask which missing view, reach or task effect it obtains. If all four current views already supply it, act directly from the relevant image. Use `remember` for reusable visual landmarks and unresolved counts; store only observed or explicitly uncertain facts.
7. Before finish, check each individual obligation and the final hand, door and appliance states against available evidence. An additional view is useful when it resolves a specific uncertainty; otherwise finish promptly. A hard limit or unsupported recovery warrants an honest incomplete result, not an invented completed goal.

## Plan tool contract

`update_plan` replaces the whole DAG. Include top-level `reason`, `subgoals` and `decision`; `decision.reason` does not supply the separate top-level reason. Each node has `id`, `description`, `dependencies`, `status` (`pending`, `active`, `done`, `blocked`) and `evidence`. Reference existing node IDs and keep dependencies acyclic.

Before replacing the DAG, reconcile it with the previous plan and item ledger. Carry each unresolved obligation forward, or explain its resolution using public evidence. Keep a carrier's unresolved contents inspection separate from category-level storage progress. Renaming a node or completing one item of a category does not clear that carrier. Preserve each carrier's identity and remaining uncertainty through the final reconciliation.

For pending/active work, evidence may be null. Mark done only when its dependencies are done and supply **one exact successful act/look response `evidence_id`** in evidence. A comma-separated list, an image_ref, or a tool-call event ID is not that field's value. Keep multiple supporting results in the item ledger or notes and choose one relevant response ID for the node; this bookkeeping does not prove the whole goal. If one aggregate would obscure several items' status, split it into item-level nodes. After a rejected update, correct the schema or evidence before relying on the proposed plan as stored.

If BDDL is the task input, read `references/bddl-goals.md` before the first motor action and translate its formal conditions into this ledger. Infer dependencies from observed affordances; a room hint is not proof of visibility. Keep any accompanying human instruction intact.
