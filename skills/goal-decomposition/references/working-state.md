# Working state and automatically composed operation DAG

Store one `goal-tree-v2` document through `task_notes`. `goals` describes results, `planning.state` records observed beliefs, and `execution.operators` describes available work. The program generates `execution.operations` and selects its first operation on write/update. All labels come from your RGB understanding, never simulator IDs.

Write before the first act/look with `operation="write", expected_version=0`. Read after missing context. Use `update` with the current version to reconcile changed fields. Each input can contain up to 12000 characters; merged notes and generated DAGs can be larger. Keep working notes concise; do not append a transcript.

```json
{
  "format":"goal-tree-v2","root_id":"root",
  "requirements":[{"id":"r1","condition":"Both items processed and arranged","basis":"explicit"}],
  "goals":[
    {"id":"root","parent_id":null,"goal":"Both requested items processed and arranged","status":"active","covers":["r1"],"check":"Both final results inspected","evidence":[]},
    {"id":"a","parent_id":"root","goal":"Item A processed and arranged","status":"pending","condition":{"a":"finished"},"check":"A has required form and placement","evidence":[]},
    {"id":"b","parent_id":"root","goal":"Item B processed and arranged","status":"pending","condition":{"b":"finished"},"check":"B has required form and placement","evidence":[]}
  ],
  "planning":{
    "location":"bench","state":{"hand":"empty","tool":"bench","a":"raw","b":"raw"}
  },
  "execution":{
    "operators":[
      {"id":"get-tool","description":"Pick up the visible tool","action":"grasp","at":"bench","seconds":3,"requires":{"hand":"empty","tool":"bench"},"effects":{"hand":"tool","tool":"held"},"check":"Correct tool held"},
      {"id":"process-a","description":"Process A on its support","at":"bench","seconds":8,"requires":{"hand":"tool","a":"raw"},"effects":{"a":"processed"},"check":"A has required form"},
      {"id":"process-b","description":"Process B on its support","at":"bench","seconds":8,"requires":{"hand":"tool","b":"raw"},"effects":{"b":"processed"},"check":"B has required form"},
      {"id":"put-tool","description":"Set the tool down on the bench","at":"bench","seconds":3,"requires":{"hand":"tool"},"effects":{"hand":"empty","tool":"bench"},"check":"Tool supported, hand empty"},
      {"id":"arrange-a","description":"Arrange processed A at its final bench position","at":"bench","seconds":3,"requires":{"hand":"empty","a":"processed"},"effects":{"a":"finished"},"check":"A has required form and placement"},
      {"id":"arrange-b","description":"Arrange processed B at its final bench position","at":"bench","seconds":3,"requires":{"hand":"empty","b":"processed"},"effects":{"b":"finished"},"check":"B has required form and placement"}
    ],
    "operations":[]
  },
  "active":null,"members":[],"assumptions":[],"uncertainties":[],"change":"Initial grounded model"
}
```

This illustrates a complete declared workflow, not a task recipe: intermediate `processed` states enable final arrangement. The program can compare doing each item end to end against sharing the tool across both. Include the actual task's resource, access, support and processing conditions. A short operator must not hide decisions about resources, workstations or order.

## Results and observed beliefs

Each goal has `id`, `parent_id`, `goal`, `status`, `check` and `evidence`. Only the root has a null parent. Intermediate layers may repeat. `covers` references requirement IDs. Statuses are `pending`, `active`, `done`, `blocked`, `retired`.

Give required leaves a nonempty `condition` dictionary using the same state keys as operator effects. Values are strings. All required conditions must hold together at the end. Temporary preparation and tool occupancy belong in operator states, not extra final requirements. Preserve completed result conditions so later work cannot silently undo them. Intermediate groups need no condition unless they have an additional independent terminal requirement.

`planning.state` holds the states currently supported by RGB and public receipts. Missing is unknown, not false or freely achievable. Use distinguishable states for whole, half-cut, diced, transferred and cooked items where relevant. Moving an object changes its recorded location too. A discovery result can record a location inspected or identity resolved; keep unfinished physical results separate.

`members` preserves recognizable identity/group, location, state, remaining work, count and evidence. Revise stale inventory instead of appending history.

## Available operators and costs

Each operator has:

- `id`, `description`, optional `action`: concrete local work, with an optional primitive name.
- `at`: observed work location; distinct workstations remain distinct even in one room.
- `requires`, `effects`: named states required before and predicted after the operation. Include hand/tool occupancy, access, object location, compatibility and any prior results it can disturb. Unmentioned states are predicted unchanged.
- `seconds`: estimated local work cost, excluding travel; optional `recovery_seconds` describes extra work supported by observed difficulty.
- `check`: observable evidence needed to accept the actual result.

Operators are reusable capabilities, not occurrences: give them no `status` or `depends_on`. A repeated use is possible only when its preconditions hold. Update capabilities when an object moves or changes form; retrieval from an old location is no longer available. The program cannot infer an omitted physical condition or invent a missing capability.

`planning.travel` contains directed source/destination edges with estimated seconds. The program uses shortest paths over these edges; absent routes are gaps, not zero cost. Physical navigation belongs to the executor. Costs compare work + travel + expected recovery + remaining process waiting, not graph node count.

For independent processing, put `starts:{"process":60}` on its start and `awaits:["process"]` on its check. Include the appropriate device/input preconditions and effects. `simulation_seconds` estimates simulation progress during an operation; omitted means zero. Use public `runtime.observed_simulation_seconds` from comparable completed work to estimate it, not `seconds` or model/network time. `planning.processes` holds remaining simulation seconds for already active processes; `planning.wait_cost_per_sim_second` prices explicit waiting. Update clocks from public capture timestamps and receipts. Elapsed time does not prove the transformation succeeded.

## Comparison, selection and reconciliation

The program searches operator combinations under state, resource, location and process conditions. It returns `operation_graph`, ordered `steps`, `coverage`, `unplanned_results`, `alternatives` and `comparison`. `comparison.missing_producers` lists goal conditions that are neither currently true in your notes nor produced by any declared operator; it identifies missing modeled work, not physical impossibility. Read these and resource/route gaps before treating a plan as complete. Search is bounded; `comparison.search_truncated` and `optimal_in_declared_model` describe the search within your declared model, not actual physical optimality.

For incomplete models, search maximizes satisfied terminal conditions, then minimizes cost. `coverage.conditions_scheduled` counts these predicted conditions separately from fully covered goals; useful work within an unfinished group is still scheduled. Neither count is observed task success.

Write/update stores the generated graph and selects its first operation, using its authored description and check. Inspect `selected_operation` and execute its supported local work; no separate `select` call is needed. To override the suggestion, use `operation="select"` with a returned `plan_id`, `operation_id`, short RGB-grounded `steps` and `next_check`. Generated operation IDs differ from reusable operator IDs. Selection does not perform a robot action. Edges preserve producing conditions and conflicting resource changes; independent work may branch. The ordered steps specify the chosen single-robot schedule.

Action replies retain the selected operation and measured tool/simulation time. After its local work finishes, fails or reveals new information, update actual state and result evidence. For example:

```json
{"planning":{"location":"bench","state":{"hand":"tool","tool":"held","a":"raw","b":"raw"}},"change":"Tool grasp confirmed"}
```

`update` merges goals and operators by ID. `execution.remove_operators` removes obsolete capabilities. Supplied `planning.state`, `processes` and lists replace their previous values; omitted fields persist. Omitted `active` ends the previous interval and lets the program select from the reconciled state. Do not mark an operator done: update observed state so remaining work is searched afresh. The generated graph and selection are preserved exactly in the replay.

`previous_execution` retains predicted costs and measured time for the last selected interval. A partial attempt or recovery is not a completed-operation cost sample. Keep travel separate from local work when revising estimates.

Before finish, verify the result tree and member inventory from evidence. Task success is established by the independent evaluator, not by graph completion or tool receipts.
