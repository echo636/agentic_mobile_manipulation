# Working state and revision contract

Use `task_notes(operation="write", content=<JSON string>, expected_version=<current version>)`. The initial version is 0. The reply returns the stored text and new version. Read with `operation="read", content=null, expected_version=null` after missing context or a conflict. Replace the complete state while preserving valid progress; the store accepts at most 12000 characters. The same general-purpose tool is available in the control condition.

Required structure:

- `L1`: original `instruction`, interpreted `goal`, `criteria` (stable `id`, `condition`, `basis`), and `assumptions`.
- `L2`: children with stable `id`, result-level `goal`, `covers` criterion IDs, necessary `depends_on` child IDs, `status`, a falsifiable `check`, and `evidence`.
- `active`: current `subgoal_id`, immediate `steps`, and `next_check`. This is the disposable L3 plan, not another tree level. When no child is executable, use null for subgoal_id and an empty steps list.
- `uncertainties`: missing information or unresolved results, including group scope when relevant.
- `change`: why this version differs; in the last version, a criterion-by-criterion reconciliation.

Statuses: pending, active, done, blocked, retired. A child with missing check or incomplete member coverage is not ready to be marked done. All criteria need a final-result child, not just a discovery child. Use ordinary visual descriptions, not hidden object IDs.

```json
{
  "format": "goal-tree-v1",
  "L1": {
    "instruction": "Put the tools away",
    "goal": "The relevant loose tools are in suitable tool storage",
    "criteria": [{"id": "r1", "condition": "All relevant loose tools are stored in the identified tool container", "basis": "instruction; container choice is an interpretation"}],
    "assumptions": ["The open compartmented case looks like tool storage; its role still needs confirmation"]
  },
  "L2": [
    {"id": "g1", "goal": "Relevant tools and suitable storage are identified", "covers": ["r1"], "depends_on": [], "status": "active", "check": "Distinguish each discovered tool and confirm storage role; record remaining search scope", "evidence": ["Initial front RGB: red-handled pliers on the bench; blue screwdriver by the case; the far work area is occluded"]},
    {"id": "g2", "goal": "Relevant tools are stored and none remain in the task area", "covers": ["r1"], "depends_on": ["g1"], "status": "pending", "check": "Each discovered member has supported placement inside suitable storage, and likely tool locations have been inspected", "evidence": ["red pliers on bench: pending", "blue screwdriver by case: pending"]}
  ],
  "active": {"subgoal_id": "g1", "steps": ["Inspect current views for the case interior and loose tools", "If needed, approach a visible position that reveals the occluded work area"], "next_check": "Storage role and tool scope are sufficiently established to begin transfers"},
  "uncertainties": ["Case role unconfirmed", "More tools may be in the occluded work area"],
  "change": "Initial plan; two candidates found, not a confirmed total"
}
```

This illustrates the representation, not a task recipe or required number of children. Replace its descriptions with the actual task. For repeated work, use compact evidence entries as a member ledger. Update each entry rather than accumulating a full action transcript. A valid completion entry might read: `red pliers: inside case, evidence_id returned by placement, still supported after the final nearby move`. Use actual returned IDs; never invent IDs or observations.

A revision example: after confirming storage, mark g1 done with its evidence, make g2 active, and plan only the next transfer. If another tool is revealed, add it to the ledger and keep/reopen g2. If placing it inside fails, staging it on the bench leaves that member pending; g2 is not done. A different local approach may address access or orientation without changing r1. If carrying the case later spills a tool, reopen g2 and update that member's location.

Before finish, `change` should reconcile each criterion rather than just say "all done": e.g., `r1 supported for red pliers and blue screwdriver; far work area not yet inspected, so full scope remains uncertain`. In that state, achieved is premature. Conversely, when available observations cover the relevant scope and every member's placement is supported, do not add arbitrary extra searches or unrelated cleanup.

The replay displays the L1/L2 hierarchy, dependency edges, active L3 and each version's recorded evidence. These are model-authored claims, separate from the private task evaluator.
