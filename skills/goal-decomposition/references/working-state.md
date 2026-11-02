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
  "active": {"goal_id": "g1", "serves": ["g1"], "steps": ["Immediate next action"], "next_check": "Expected result"},
  "members": [{"label": "local-a", "description": "Recognizable visual features", "location": "Last observed location", "state": "Current task-relevant state", "remaining": "Unfinished work", "count": 1, "evidence": []}],
  "assumptions": [],
  "uncertainties": [],
  "change": "Reason for this revision"
}
```

IDs are unique; only the root has a null parent. Every other `parent_id` references an existing goal; parent links form one tree. Dependencies reference goal IDs and may cross branches. Keep both relations acyclic. Parentage alone does not require sibling execution order; children do not wait for their parent to be done.

Statuses are `pending`, `active`, `done`, `blocked`, `retired`. `active.goal_id` identifies the focal leaf; `active.serves` lists all ready leaves advanced by the current batch or shared trip. Their ancestors can also have status `active`. With none ready, set `active.goal_id` to null and `steps` to an empty list. In `steps`, name the batch's source, carrier, destination and next useful work at that destination; use `members` for its inventory. Keep this short, not a complete future action transcript.

Use `members` when item coverage matters. Labels are your own, not simulator IDs. An entry represents one item or a distinguishable group in the same state; split a mixed-state entry into pending inputs and finished outputs, with their current locations and count units. Update state and remaining work from RGB and feedback. A spill reopens the affected goal; temporary placement does not complete storage.

For repeated transfers, save each subtotal with the last feedback event it includes in `evidence`. Before changing areas or subtasks, reconcile completed processing and deliveries with `members`; replace stale counts, including any conflicting summary. On resuming, read `action_feedback_since_write` from the returned notes and reconcile those public receipts with the checkpoint before acting or replacing notes. If `omitted` is nonzero or item correspondence is unavailable, mark the affected count uncertain. Keep uninspected supply, work in progress and confirmed delivery distinct. Replace stale entries rather than appending a transcript.

Before finish, use `change` to reconcile each criterion and `uncertainties` for remaining gaps. An evidence ID alone, a successful notes write or a `done` label does not verify a result. Preserve original requirements and valid progress when replacing the state.

Before repeated deliveries, add `transport` for a small horizon of known work, including unfinished preparation. Each item references a delivery leaf in `goals`; link its preparation goals through `depends_on`. Put each unfinished prerequisite in `preparations` with its work location and estimated local processing/handling time. Model operations start and end with a free hand; include necessary staging and tool handling in the estimate. Exploration with unknown locations can stay outside this horizon. Preserve the task's end-state requirements: these dependencies describe the chosen method, not extra task requirements.

The reply's `transport_advice.plan.steps` orders preparation and delivery operations, including shared carrier batches. It compares finishing preparation before departure with delivering currently ready items, and tracks the carrier's location after each trip. Copy the chosen first operation to `active.steps` and its goals to `active.serves`; use current RGB for actual targets. A `needs_revision` plan identifies missing prerequisites/routes; correct the beliefs or inspect what is unknown. The estimate is advisory and never marks goals done. Clear `transport` when its work is finished.

```json
"transport": {
  "robot_location": "kitchen",
  "items": [
    {"goal_id": "deliver-cup", "source": "kitchen", "destination": "table", "units": 1, "compatibility": "clean-tableware"},
    {"goal_id": "deliver-plate", "source": "kitchen", "destination": "table", "units": 1, "compatibility": "clean-tableware"}
  ],
  "preparations": [{"goal_id": "prepare-plate", "location": "kitchen", "seconds": 15}],
  "carriers": [{"label": "tray", "location": "kitchen", "capacity": 2, "compatible_groups": ["clean-tableware"], "return_required": false}],
  "travel": [{"source": "kitchen", "destination": "table", "seconds": 20}, {"source": "table", "destination": "kitchen", "seconds": 20}],
  "handling_seconds": {"direct_item": 10, "load_item": 10, "unload_item": 10, "carrier_trip": 20, "carrier_return": 10}
}
```

In this example, add a pending `prepare-plate` goal and set `deliver-plate.depends_on` to `["prepare-plate"]`; the cup is already ready. Preparation IDs and delivery IDs are distinct. Do not list already completed preparations. These labels, capacities and costs are illustrative; estimate from the episode, and omit unknown routes/carriers. `units` and `capacity` use the same space scale. Routes are directional and include returns where needed. `carrier_trip` covers grasping/placing the carrier; item costs include grasp/place and freeing the hand. Add `recovery_seconds_per_trip` for observed carrier failures. Unknown compatibility stays null and is not grouped. The planner searches this supplied horizon with a bounded beam and heuristic capacity packing; it does not prove a globally optimal schedule. Include nearby pending preparation before comparing trips, rather than supplying only the single item you have just finished.
