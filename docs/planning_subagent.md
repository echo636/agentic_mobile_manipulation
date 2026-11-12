# Visual planning subagent

An optional experiment arm adds `plan_task(progress)` to the basic-skills actor.
The actor still chooses all robot actions and RGB target pixels. A separate model
compares ways of completing the remaining task and returns one concise plan:

```json
{
  "nodes": [
    {"id": "load", "work": "Cups and snacks are supported on the tray", "after": []},
    {"id": "deliver", "work": "Tray and its contents are on the serving table", "after": ["load"]}
  ],
  "next": ["load"]
}
```

Nodes describe work and observable results; edges are necessary prerequisites.
`next` lists the next coherent work segment in execution order. Completed work is
removed when revising. There is no separate goal tree, operator-cost table or
program search in this arm. Model comparison of methods is heuristic, not a proof
of a globally shortest plan.

The actor consults after initial inspection, segment completion, material new
information or blockage. It handles ordinary local corrections without replanning
every atomic action. No note or plan gate prevents `act` or `look`.

The planner receives the original request, current four RGB images, the actor's
short summary, previous remaining-work DAG, up to eight recent public action
receipts and segment timing/counts. Omitted earlier receipts are explicitly
counted. It receives no simulator state, object ids or private evaluation. Native
MCP images pass through unchanged to the actor. Planning runs outside the
simulator process and makes no simulator calls.

Enable with the controller flags `--agent-profile basic-skills --goal-skill none
--planner-model MODEL --planner-reasoning-effort EFFORT --planner-python PYTHON`.
Use the existing explicit Codex provider and gateway options. `PYTHON` must have
the pinned MCP extra. Batch manifests use `planner_model` and
`planner_reasoning_effort`; batch config supplies `planner_python`.

The planner uses an isolated Codex call with image inputs and a JSON output
schema, with no robot tools. `planner/events.jsonl` records its public inputs,
image references/hashes, output, usage and latency. `planner/plan.json` retains
the latest accepted plan across client reconnects; per-call client evidence is
under `planner/calls/`. Temporary image files are removed after each call.

Compare official success first, then robot actions, action errors, tool time,
planner calls/time/tokens and total episode time. A generated plan or a tool-only
integration test is not evidence of task success or improved efficiency.
