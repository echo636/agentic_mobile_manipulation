---
name: goal-tree
description: Maintain a hierarchical result plan and item progress, with the model choosing the next work.
---
# Plan down, verify up

Organize the task in a result tree:

| Layer | Meaning |
|---|---|
| Root | The requested final state, written in your own words. |
| Intermediate goals | Contributing results; subdivide again when useful. |
| Leaves | Directly pursuable results with observable completion checks. A leaf may need several actions. |
| Current local plan | A short sequence for the selected leaf, stored in `active.steps` outside the tree. |

Depth and child counts follow the task. Preserve required quantities, destinations, states and constraints. Refine the branch you pursue; other branches can remain coarse. Choose the next feasible unfinished leaf yourself, accounting for prerequisites and independent work.

After initial inspection, write the tree through `task_notes`. Update it when a result completes or blocks, the focus changes, or observations invalidate a belief. Check completed children against their parent's own result before marking it done. Reopen results disturbed by later handling. Notes are beliefs supported by RGB and public receipts, not evaluator truth.

Use `members` to distinguish recognizable items or groups by location, current state, remaining work and count. Separate observed facts from uncertainty; completed action counts alone do not identify what remains. Before finishing, reconcile every requirement and member with the root's result.

Store one JSON document using this format; add intermediate nodes and leaves as needed:

```json
{
  "format":"goal-tree-v2","root_id":"g0",
  "requirements":[{"id":"r1","condition":"Required final result","basis":"explicit"}],
  "goals":[
    {"id":"g0","parent_id":null,"goal":"Complete final state","covers":["r1"],"status":"active","check":"Evidence for the whole result","evidence":[]},
    {"id":"g1","parent_id":"g0","goal":"Contributing result","covers":["r1"],"status":"pending","check":"Observable result","evidence":[]}
  ],
  "active":{"goal_id":"g1","steps":["Next local action"],"next_check":"Expected result"},
  "members":[],"assumptions":[],"uncertainties":[],"change":"Initial plan"
}
```

Every non-root node references its parent; keep stable IDs. Statuses are `pending`, `active`, `done`, `blocked`, `retired`. A member entry may contain `label`, `description`, `location`, `state`, `remaining`, `count`, `evidence`; labels come from your observations.

Call `task_notes(operation="write", content=<JSON string>, expected_version=0)` initially. Each reply supplies the new version. `update` merges changed goals by ID; include the new `active` plan, and replace `members` when its inventory changes. After missing context, `read` with null content/version restores the tree and recent public action receipts. Reconcile these receipts before revising stale progress. Keep notes concise rather than appending a transcript.

This profile stores the result tree and local plan only. It does not use operation graphs, operator libraries, costs or program-selected actions. No note write is required by the executor to permit an action.
