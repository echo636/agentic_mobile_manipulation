# Task notes format

Write the complete replacement JSON string with `task_notes(operation="write", content=..., expected_version=...)`. Start at version 0; each reply supplies the next version. After missing context or a version conflict, read with `operation="read", content=null, expected_version=null`. Maximum content length: 12000 characters.

Use this structure:

```json
{
  "format": "goal-tree-v1",
  "L1": {
    "instruction": "Original instruction, unchanged",
    "goal": "Interpreted final result",
    "criteria": [{"id": "r1", "condition": "Required observable result", "basis": "explicit or interpreted"}],
    "assumptions": []
  },
  "L2": [{
    "id": "g1", "goal": "Result this child establishes", "covers": ["r1"],
    "depends_on": [], "status": "active",
    "check": "Evidence needed to establish this result", "evidence": []
  }],
  "active": {"subgoal_id": "g1", "steps": ["Immediate next action"], "next_check": "Expected result"},
  "uncertainties": [],
  "change": "Reason for this revision"
}
```

Add children as the task requires. Statuses are `pending`, `active`, `done`, `blocked`, `retired`. When no child is executable, set `active.subgoal_id` to null and `steps` to an empty list.

Keep a compact member ledger in `evidence`, using visual descriptions, last locations, completion states and actual returned evidence IDs. Update entries rather than appending an action transcript. A spilled item reopens its goal; placing it temporarily on a bench leaves its storage requirement pending.

Before finish, use `change` to reconcile each criterion and `uncertainties` for remaining gaps. An evidence ID alone, a successful notes write or a `done` label does not verify a result. Preserve original requirements and valid progress when replacing the state.
