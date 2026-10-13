# Compact working state

The same general-purpose task_notes tool is available to agents with and without this skill. This format is a convention taught by the skill, not a simulator state representation.

Write a JSON object as the `content` string of task_notes, with `operation="write"` and the current `expected_version` (initially 0). The reply returns the stored notes and new version. Use `operation="read", content=null, expected_version=null` to recover the current state after missing context or a version conflict. Replace the complete working state, preserving valid progress. Transport retries with the same request ID do not create a new version.

```json
{
  "format": "goal-tree-v1",
  "L1": {
    "instruction": "Put the books away",
    "goal": "The relevant books are in suitable final storage",
    "criteria": [{"id": "r1", "condition": "All task-relevant books are stored together in the identified book storage", "basis": "instruction plus stated interpretation"}],
    "assumptions": ["The visible shelf is book storage; verify its role and scope from RGB"]
  },
  "L2": [
    {"id": "g1", "goal": "Relevant books and a suitable storage location are identified", "covers": ["r1"], "depends_on": [], "status": "active", "check": "Visible books and storage are distinguishable; any uninspected area remains recorded", "evidence": []},
    {"id": "g2", "goal": "The identified books are stored and no relevant book is left out", "covers": ["r1"], "depends_on": ["g1"], "status": "pending", "check": "Account for each identified book using placement feedback and scene coverage", "evidence": []}
  ],
  "active": {"subgoal_id": "g1", "steps": ["Inspect the four initial views for books and storage", "If scope is still unclear, move to a visible observation position and inspect returned RGB"], "next_check": "Book scope and suitable destination are identified"},
  "uncertainties": ["Whether more books are outside the initial views"],
  "change": "Initial interpretation; scope still needs observation"
}
```

This is an illustration, not a book-task recipe or a required child count. Use the actual task and evidence. Keep text compact; the store accepts at most 12000 characters. `L1` and `L2` form the tree; `active` is the disposable L3 plan for one child. Do not nest children inside L2.

When g1 is supported, change it to done, add evidence such as `event-00017: returned left RGB shows the red and blue books and accessible shelf`, make g2 active, and replace `active` with its immediate carrying steps. Do not invent evidence IDs; tool replies supply evidence_id. If a later view reveals another book, preserve the already stored books, expand scope, reopen or continue g2, and record that discovery in change. Keep counts or distinguishing landmarks in goal/evidence strings as needed; no hidden identities are required.

A completion claim is still a model claim. The replay preserves earlier versions and shows changes alongside their action-step positions; the official evaluator stays private.
