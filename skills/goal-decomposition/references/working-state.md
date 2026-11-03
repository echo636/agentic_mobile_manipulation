# Goal tree and operation DAG

Use `task_notes` to store one `goal-tree-v2` document. `goals` is the result hierarchy; `execution.operations` is a separate directed acyclic graph of work. Their IDs are distinct concepts. `achieves` links work to results. All names are your RGB-based labels, never simulator IDs.

Write before the first act/look with `operation="write", expected_version=0`. Read after missing context; update using the current version. The 12000-character store is working state, not a transcript: keep completed work concise and remove obsolete operations after updating their dependents.

```json
{
  "format":"goal-tree-v2", "root_id":"root",
  "requirements":[{"id":"r1","condition":"Requested final result","basis":"explicit"}],
  "goals":[
    {"id":"root","parent_id":null,"goal":"Task end state","status":"active","covers":["r1"],"check":"Every requested result verified","evidence":[]},
    {"id":"result-a","parent_id":"root","goal":"One verifiable contributing result","status":"pending","covers":["r1"],"check":"Observable result","evidence":[]}
  ],
  "planning":{
    "location":"bench", "state":{"tool":"stored"},
    "travel":[{"source":"bench","destination":"shelf","seconds":8},{"source":"shelf","destination":"bench","seconds":8}]
  },
  "execution":{
    "operations":[
      {"id":"get-tool","description":"Retrieve the required visible tool","action":"grasp","at":"shelf","seconds":3,"requires":{"tool":"stored"},"effects":{"tool":"held"},"check":"Correct tool held"},
      {"id":"process-a","description":"Perform the required processing at the bench","at":"bench","seconds":8,"depends_on":["get-tool"],"requires":{"tool":"held"},"effects":{"item-a":"processed"},"achieves":["result-a"],"check":"Requested processing visible"}
    ],
    "rewrites":[]
  },
  "active":null,
  "members":[], "assumptions":[], "uncertainties":[], "change":"Initial result and operation plan"
}
```

This illustrates the format, not a task recipe. Include all known branches and resource changes relevant to the actual task.

## Result hierarchy

Each goal has a stable `id`, `parent_id`, `goal`, `status`, `check` and `evidence`. Only the root has a null parent. Repeat intermediate layers where needed. `covers` references requirement IDs. `depends_on` can express necessary cross-branch result prerequisites; represent their actual work dependencies in the operation DAG too. Leaves are independently verifiable results; no `methods` are stored on goals in this representation.

Goal statuses are `pending`, `active`, `done`, `blocked`, `retired`. Requested quantities, relations, destinations, processing states and final device states remain explicit. Extra assumptions are separate from requested outcomes. Completing children does not establish a parent's own check.

`members` records recognizable identity/group, last observed location, current state, remaining work, count and evidence. Keep pending input, work in progress and completed delivery distinct. Replace stale subtotals instead of appending history.

## Operation nodes

- `id`, `description`: stable identity and concrete work to perform. `action` optionally names the expected primitive. One local operation may require a short sequence, but must not hide choices of location, resources or ordering.
- `at`: observed work location. Movement from the previous operation is priced separately using `planning.travel`; do not lump distinct stations into a room label.
- `depends_on`: operation IDs that must finish first. Shared preparation is referenced by multiple consumers. Independent nodes remain unordered.
- `seconds`: estimated local tool/work cost, excluding travel. `recovery_seconds` optionally estimates extra work from observed difficulty.
- `requires`, `effects`: named resource/object states before and after the operation. Values are strings, e.g. `{"hand":"empty","carrier":"loaded"}`. Model tool retrieval, staging, loading and switching as explicit operations; resource changes are not free.
- `achieves`: goal IDs whose complete results this operation is expected to establish. This is coverage for planning, not an automatic goal-status update.
- `check`: RGB/public feedback needed before accepting the operation's result.
- `status`: `pending` by default, then `done` when reconciled. An operation that failed without its intended effect remains pending with a revised approach, or is replaced. Preserve useful partial effects in `planning.state`.
- `share`: optional label for duplicate descriptions of the **same one-time work**. The program can merge matching pending nodes, unioning their dependencies and achieved results. Do not label repeated processing, distinct objects or separate visits as shared work.

The scheduler tracks hand/tool/object states you supplied. It does not infer capacity, object identity or physical feasibility. Include relevant conditions rather than assuming any ordering is feasible.

## Alternative execution subgraphs

### Automatic composition from available operations

The opt-in `execution.operators` form lets the program compose a plan without
requiring you to propose complete replacement graphs. Each operator describes
one available local operation using `id`, `description`, `at`, `seconds`,
`requires`, `effects` and `check`; `action` and process timing fields are optional.
Operators have no occurrence dependencies or completion status. Their state
preconditions determine when they can be used, including repeated use when
necessary. Record all relevant effects, including results that an action undoes.

Give each required final-result leaf a `condition` dictionary using the same
named state keys as `planning.state` and operator effects. These are predicted
final-state conditions, not statements that the results have been observed.
Keep conditions for completed results too, so later work must preserve them.
An absent condition is reported as incomplete task coverage.

The program searches available state transitions, charging work, travel,
recovery and process waiting. It can choose, omit, combine or repeat operators;
then it generates operation occurrences and causal/resource dependencies in
`execution.operations`. No `rewrites` or `share` labels are required in this
mode. Selection uses the returned plan ID and operation ID as usual. Search is
bounded; `comparison` reports whether the bound was reached and whether the
lowest cost was established within the declared model. Physical feasibility
and actual task success still require RGB execution evidence.

Only declare operations justified by observations and available tools. If an
operation moves an object, include its location change; later retrieval must
use the new location. Tool/access/support conditions must describe a plausible
physical operation. A symbolic state assignment cannot create a missing tool
capability or make inaccessible cutting feasible.

Updates merge operators by ID; `execution.remove_operators` removes obsolete
capabilities. After observing an effect, update actual `planning.state`; do not
mark a reusable operator done. The generated DAG is replaced on selection, and
the exact selected graph is retained in the replay.

### Explicit replacements

`execution.rewrites` describes meaningful replacements of pending work:

```json
{
  "id":"shared-approach",
  "description":"Why these concrete operations can achieve the same results with different cost",
  "replaces":["old-operation-a","old-operation-b"],
  "operations":[
    {"id":"shared-setup","description":"Prepare the observed shared resource","at":"bench","seconds":5},
    {"id":"shared-work","description":"Perform compatible joint work","at":"bench","seconds":8,"depends_on":["shared-setup"],"achieves":["result-a","result-b"]}
  ]
}
```

New operation dependencies refer to other operations within the replacement. The program connects incoming prerequisites to its roots, and outgoing consumers to its terminal nodes. It preserves the declared achieved result set; you must also preserve required states and physical feasibility. Internal operations must expose meaningful handling/processing choices, not hide the whole alternative in one node. Completed work cannot be replaced.

The program considers the original graph, compatible substitutions and shared-work merges, then schedules each graph under dependencies, location, resource and process constraints. At most 32 graph variants and a bounded schedule search are retained; the output reports truncation/pruning. A minimum among retained estimates is not a global optimum or a measured efficiency gain.

Read `planning.operation_graph`, `graph_changes`, `steps`, `alternatives`, `coverage` and `comparison`. The result tree remains separate. Incomplete result coverage is explicit; comparing a planned subset is not optimizing the whole task. Do not invent alternatives merely to populate comparisons.

## Processes and cost

Cost is local work + travel + expected recovery + remaining wait cost. Travel uses shortest paths over your supplied directed edges; missing routes are gaps, not zero distance. Physical navigation still belongs to the executor.

For independent device work use `starts:{"process":60}` on the starting operation, `awaits:["process"]` on its later check, and a dependency from check to start. `planning.processes` holds remaining simulation seconds for already active processes. `simulation_seconds` is observed/estimated simulation progress during an operation; model/network delay does not count. `planning.wait_cost_per_sim_second` prices explicit waiting. The program can place independent work between start and check to reduce idle time. Update clocks from capture timestamps and receipts; elapsed time does not prove the desired transformation occurred.

## Selection and reconciliation

After reading the comparison, select:

```json
{"operation":"select","expected_version":1,"content":"{\"plan_id\":\"base\",\"operation_id\":\"get-tool\",\"steps\":[\"Grasp the visible tool\"],\"next_check\":\"Correct tool held\"}"}
```

Use a returned plan ID, including its rewrite suffix if applicable. Selection materializes that graph, retains unchanged goals, and stores `active.operation_id` plus your short RGB-grounded steps. It performs no robot action. The selected operation, resource conditions, predicted effects and result checks accompany action feedback.

Update after a completed operation, failure or changed information:

```json
{"execution":{"operations":[{"id":"get-tool","status":"done"}]},"planning":{"location":"shelf","state":{"tool":"held"}},"change":"Tool grasp confirmed"}
```

`update` merges goals and operations by ID; omitted nodes persist. `execution.remove_operations` removes obsolete IDs; repair their dependent edges in the same update. `execution.rewrites` replaces the alternatives list. Planning fields merge at the top level; `state`, `processes` and lists replace their whole previous values. Other top-level fields replace supplied values; omitted `active` clears.

Operation completion and goal completion are separate updates. On success, record observed resource changes and only the results actually established. On failure, keep the real partial state and revise affected work. Read the new comparison and select again. `previous_execution` retains predicted cost and measured tool/simulation time for the last selected interval; a partial attempt or recovery is not a completed-operation cost sample.

Before finish, reconcile the entire result tree and member inventory with evidence. Neither a complete predicted graph nor successful operation receipts establish official task success.
