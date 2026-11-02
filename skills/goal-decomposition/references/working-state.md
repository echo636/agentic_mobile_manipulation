# Task notes format

Write the complete replacement JSON string with `task_notes(operation="write", content=..., expected_version=...)`. Start at version 0; each reply supplies the next version. After missing context or a version conflict, read with `operation="read", content=null, expected_version=null`. Maximum content length: 12000 characters. Keep completed branches concise: retain their IDs, relationships, result and evidence, but remove obsolete action plans and repeated prose. Keep each item subtotal in one member entry and refer to it from goals instead of duplicating the ledger.

Tree shape follows the task. This example has a root, two intermediate layers on one branch, and leaves at different depths. The current action plan is stored separately in `active` (labels illustrate structure, not task content):

```text
g0 Task end state (root)
├─ g1 Contributing result A (intermediate)
│  ├─ g11 Finer result A1 (intermediate)
│  │  ├─ g111 Directly pursuable result A1a (leaf)
│  │  └─ g112 Directly pursuable result A1b (leaf)
│  └─ g12 Directly pursuable result A2 (leaf)
├─ g2 Directly pursuable result B (leaf)
└─ g3 Directly pursuable result C (leaf)
```

Here `g111.parent_id` is `g11`, and `g11.parent_id` is `g1`. If A2 requires B, set `g12.depends_on` to `["g2"]` while keeping `g12.parent_id` as `g1`. Do not copy the example's depth or counts; expand any leaf if it later needs decomposition.

Use the following fields. Only two goal records are shown for brevity; put **every node**, including intermediate parents and deeper descendants, in the flat `goals` list:

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
  "active": {"goal_id": "g1", "method_id": "chosen-method", "serves": ["g1"], "steps": ["Immediate next action"], "next_check": "Expected result"},
  "members": [{"label": "local-a", "description": "Recognizable visual features", "location": "Last observed location", "state": "Current task-relevant state", "remaining": "Unfinished work", "count": 1, "evidence": []}],
  "assumptions": [],
  "uncertainties": [],
  "change": "Reason for this revision"
}
```

IDs are unique; only the root has a null parent. Every other `parent_id` references an existing goal; parent links form one tree. Dependencies reference goal IDs and may cross branches. Keep both relations acyclic. Parentage alone does not require sibling execution order; children do not wait for their parent to be done.

Statuses are `pending`, `active`, `done`, `blocked`, `retired`. `active.goal_id` identifies the focal work goal; `active.method_id` identifies the chosen method; `active.serves` lists all goals advanced by it. Their ancestors can also have status `active`. With none ready, set `active.goal_id` to null and `steps` to an empty list. In `steps`, describe the immediate work and its next check; refer to `members` for any inventory. Keep this short, not a complete future action transcript.

Use `members` when item coverage matters. Labels are your own, not simulator IDs. An entry represents one item or a distinguishable group in the same state; split a mixed-state entry into pending inputs and finished outputs, with their current locations and count units. Update state and remaining work from RGB and feedback. A spill reopens the affected goal; temporary placement does not complete storage.

For repeated transfers, save each subtotal with the last feedback event it includes in `evidence`. Before changing areas or subtasks, reconcile completed processing and deliveries with `members`; replace stale counts, including any conflicting summary. On resuming, read `action_feedback_since_write` from the returned notes and reconcile those public receipts with the checkpoint before acting or replacing notes. If `omitted` is nonzero or item correspondence is unavailable, mark the affected count uncertain. Keep uninspected supply, work in progress and confirmed delivery distinct. Replace stale entries rather than appending a transcript.

Before finish, use `change` to reconcile each criterion and `uncertainties` for remaining gaps. An evidence ID alone, a successful notes write or a `done` label does not verify a result. Preserve original requirements and valid progress when replacing the state.

## Costed methods on the same goal tree

Add `methods` to executable goals, not a separate list of transport goals. One method is a short, checkable unit of work that may need several primitives. Its `id` is unique across the tree. Its owner goal and optional `also` goals are the predicted results, all of whose prerequisites must be ready before the shared method starts. Methods on an intermediate node perform its own final check after its children. Otherwise intermediate nodes are structural: they remain subject to your explicit evidence checks, not automatic completion.

```json
{
  "planning": {
    "location": "bench",
    "state": {"tool": "none", "hand": "empty"},
    "travel": [
      {"source": "bench", "destination": "shelf", "seconds": 15},
      {"source": "shelf", "destination": "bench", "seconds": 15}
    ],
    "switches": [
      {"key": "tool", "from": "none", "to": "brush", "at": "bench", "seconds": 6},
      {"key": "tool", "from": "brush", "to": "none", "at": "bench", "seconds": 6}
    ]
  }
}
```

For example, a goal's candidate methods could be:

```json
"methods": [
  {"id": "clean-a", "route": ["bench"], "seconds": 12,
   "setup": {"tool": "brush"}},
  {"id": "clean-pair", "also": ["clean-b"], "route": ["bench"], "seconds": 19,
   "setup": {"tool": "brush"}, "requires": {"hand": "empty"},
   "effects": {"work_surface": "wet"}, "recovery_seconds": 2}
]
```

These are format examples, not a task recipe. `clean-b` must be another executable goal in the tree; describe its independent method there if feasible. Use the same representation for inspecting related items together, processing compatible inputs, joining components or moving a loaded tray. Alternatives belong under a result goal; do not duplicate that result as a new requirement. Retire obsolete methods when their assumptions stop holding.

Fields:
- `route`: ordered observed areas visited by the method, including its work location and destination. A local operation has one entry. Travel from the current location and along the route is added using the supplied directed area graph. Missing edges are not free; connected known paths may be combined. This is a task-level travel estimate, not a replacement for the executor's physical navigation.
- `seconds`: estimated local work and handling, including any internal actions not represented separately. Do not include travel or setup costs again. Use comparable estimates based on observations; record uncertainty. Counts alone cannot predict wall time.
- `requires`: state that must already hold before the method, such as available capacity or an empty hand. Unknown state does not satisfy a requirement. Values are short strings chosen by you, not simulator IDs.
- `setup`: reusable resource configurations needed at the first route location. An observed local change may use a matching `switches` entry; its cost is added and the resulting configuration is tracked across methods. Include physical retrieval/staging in that estimate. If a change involves nonlocal work or its own dependency, model it as a goal/method instead.
- `effects`: predicted state after the method, including resource, device or container changes. These affect candidate plans only. Confirm actual effects from RGB/receipts in the next notes update.
- `also`: other executable goals achieved together, without repeating completed work. Compatibility/capacity must be supported by your observations and included in the method's assumptions; the scheduler cannot discover them from hidden state.
- `recovery_seconds`: optional expected additional work based on observed difficulties, default zero.

The cost is work + travel + setup + expected recovery seconds. The scheduler searches method choices and order with a bounded beam, tracks predicted resource state, and reports up to three surviving different first choices. It does not prove a global minimum. It serializes robot work; passive process overlap is not timed automatically. Represent starting, other independent work and checking completion separately, and revise remaining wait estimates from actual observations.

`planning.coverage` includes every unfinished executable goal, including those without methods. A partial result names unplanned goals and missing routes/resource transitions. Explore or refine those gaps; do not present the partial cost as whole-task cost. `verification_goals` lists structural parents still needing evidence reconciliation. Keep known future branches in the tree even when the active method serves only one.

Choose a returned method with `active.method_id`, list all served goals in `active.serves`, and keep only its immediate steps in `active.steps`. On completion or changed evidence, update affected statuses, member counts, current location and resource state together. Every successful notes write recomputes the plan; reads/initialize preserve any pending receipt reconciliation. After act/look, the old next recommendation is withheld until reconciliation. A failure may have changed the scene: reopen affected results and ancestors from evidence, not merely by replaying the same method.
