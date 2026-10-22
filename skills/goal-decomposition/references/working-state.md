# Task notes format

Write the complete replacement JSON string with `task_notes(operation="write", content=..., expected_version=...)`. Start at version 0; each reply supplies the next version. After missing context or a version conflict, read with `operation="read", content=null, expected_version=null`. Maximum content length: 12000 characters.

Use this structure:

```json
{
  "format": "goal-tree-v2",
  "root_id": "g0",
  "requirements": [{"id": "r1", "condition": "Required result or constraint", "basis": "explicit or assumed"}],
  "goals": [{
    "id": "g0", "parent_id": null, "goal": "Complete end state in your own words",
    "covers": ["r1"], "depends_on": [], "status": "active",
    "check": "Evidence establishing the complete result", "evidence": []
  }, {
    "id": "g1", "parent_id": "g0", "goal": "Result contributing to its parent", "covers": ["r1"],
    "depends_on": [], "status": "active",
    "check": "Evidence needed to establish this result", "evidence": []
  }],
  "active": {"goal_id": "g1", "steps": ["Immediate next action"], "next_check": "Expected result"},
  "assumptions": [],
  "uncertainties": [],
  "change": "Reason for this revision"
}
```

This is a minimal shape, not a depth or branching limit. Add any number of goals with `parent_id` pointing to an existing goal: for example, siblings under `g0`, children under `g1`, and further descendants where useful. IDs are unique; only the root has a null parent. Parent links form one tree. Dependencies reference goal IDs and may cross branches; they do not set parentage. Keep both relations acyclic.

Statuses are `pending`, `active`, `done`, `blocked`, `retired`. Any executable goal can be active. With none ready, set `active.goal_id` to null and `steps` to an empty list.

Keep a compact member ledger in `evidence`, using visual descriptions, last locations, completion states and actual returned evidence IDs. Update entries rather than appending an action transcript. A spilled item reopens its goal; placing it temporarily on a bench leaves its storage requirement pending.

Before finish, use `change` to reconcile each criterion and `uncertainties` for remaining gaps. An evidence ID alone, a successful notes write or a `done` label does not verify a result. Preserve original requirements and valid progress when replacing the state.
