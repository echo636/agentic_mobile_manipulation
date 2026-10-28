# Harness architecture

The default system uses RGB observations, callable workflow skills, and tools.
The model maintains hierarchical goals and working state in episode-local,
versioned task notes. See the [code map](code_organization.md) for implementation
entry points. The optional legacy
`workflow` profile remains available for older experiments, but is not enabled by
the default `skills` profile.

```text
Task instruction + fixed instance + versions + execution budget
                            |
          +-----------------+-------------------+
          |                                     |
  CLI policy adapter                      Native visual loop
  prepare_project -> run -> parse          Responses model requests
  Codex / OpenCode / Kimi                  + RGB attachment history
          |                                     |
  official SDK FastMCP                          |
  stdio / SSE / Streamable HTTP                 |
          |                                     |
  HTTP bridge (queued RPC)                      |
          +-----------------+-------------------+
                            |
             One tool registry + ToolContext
             schemas / profile / metadata
                            |
                  VisionHarness (owner thread)
           revision / budgets / execution / records
                            |
            RGBBackend -> OmniGibsonBackend
                            |
                 result + fresh four-view RGB

Formal finish -> private evaluator -> score and recorded replay
```

## One tool contract

`tools/base.py` defines the registry. `tools/__init__.py` selects the profile and
exports its catalog. MCP and the native loop use the same input schemas and
handlers through `VisionHarness.call`. `ToolContext` binds the request ID, tool,
episode profile and observation revision, and delegates episode operations.
It is a programming interface for trusted handlers, not a Python security sandbox.

Metadata describes world mutation, fresh-image requirements, idempotence and
closure. MCP annotations are hints; schema validation, profile access and image
revision checks remain enforced by the harness. An observation is read-only with
respect to the simulated world but updates image references, so it is not marked
idempotent. Every executor attempt advances the revision, including partial
failures. Invalid requests rejected before execution do not move the robot.

The default seven tools are `initialize`, `look`, `act`, `finish`, `list_skills`,
`read_skill`, and `task_notes`; minimal exposes the first four. `initialize` returns the prepared
episode's current snapshot without resetting or rendering again. Each act/look
response includes its resulting four-camera RGB, so the next decision uses that
response directly. Use the wait action when simulation time must pass. Final
verification can use the last action's images without an additional capture.
Workflow documents cover manipulation, exploration, pick and place, recovery, and
[hierarchical goal decomposition](hierarchical_goal_planning.md). The goal skill
teaches a model-written root end state, recursively refined intermediate outcomes
and directly pursuable leaf goals. Depth and child counts vary with the task;
the current action plan stays outside the tree. The tree, dependencies, item
coverage and evidence are saved as versioned model-authored `task_notes`.
The no-goal-skill control has the same note
store and tools. Documents guide the model; they do not secretly run actions.

## FastMCP and concurrency

`mcp_server.py` uses `mcp.server.fastmcp.FastMCP` from the pinned official Python
SDK `mcp==1.28.1` for every transport. The simulator bridge owns the tool catalog;
the proxy registers that exact schema without changing JSON types. Actual image
bytes are downloaded, checked against their recorded SHA-256, and returned as MCP
image content.

```bash
python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440
python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440 \
  --transport sse --host 127.0.0.1 --port 29441
python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29440 \
  --transport streamable-http --host 127.0.0.1 --port 29442
```

HTTP endpoints are `/sse` and `/mcp`. HTTP listens on loopback; a tunnel can connect
an external client. One server represents **one shared episode**. Multiple clients
share the robot, revisions, budgets and finish state; HTTP sessions do not create
independent worlds. Calls through the proxy are serialized through RGB assembly.
Only the simulator's creating thread performs render, physics or state operations.
The legacy workflow profile retains observe and asynchronous capture jobs. Those
jobs run at owner-thread scheduling points, including while the native model
request waits on network I/O. They are absent from the default model tool catalog.

A transport timeout does not cancel an action or prove its failure. The bridge
uses request IDs for duplicate RPCs, but a new MCP call gets a new operation ID.
After an uncertain call, retrieve the current snapshot with initialize and inspect
its images and revision before deciding whether another action is needed.
Cross-process exactly-once recovery is not claimed.

## Policy clients and native loop

[CLI adapters](../src/manipulation_agent/clients/README.md) provide common
capability probes, project preparation, process execution and event parsing.
`run_codex_controller.py` remains the compatible default.
`run_model_controller.py --client codex|opencode|kimi` uses the same orchestration,
MCP preflight, formal finish check and execution clock. Configuration support is
separate from installation, authenticated execution and verified RGB delivery.
OpenCode and Kimi have configuration/parser tests but are not yet live-validated.
Every client receives its profile instructions from vision_policy.system_prompt.
Default instructions start with initialize and consume act/look result images;
the adapters do not add a separate observation or polling loop.

The separate `agent_loop.py` implements the native Responses loop. It requests
one tool call at a time, executes through the same harness, attaches fresh image
bytes after success or failure, and checks turn, token and task budgets. Plain
assistant text saying “done” does not close the episode. The model must call
`finish`; otherwise an exhausted budget closes it as aborted.

`image_history.py` records capture identity and verifies every attached image.
`--image-history-captures 0` keeps all images, preserving the default behavior.
A positive value limits historical image attachments in outgoing native requests;
textual tool history, original images and experiment records remain intact. Omitted
pixels receive an explicit notice, not a generated visual-memory summary. CLI
clients manage their own model context, so this option does not configure Codex,
OpenCode or Kimi. `--model-max-turns` and `--model-max-tokens` also apply only to the
native Responses path.

Separately, `provider_gateway.py --keep-latest-image-items N` can prune old image
payloads in outbound Responses requests, including those sent by a Codex client
routed through that gateway. It is opt-in (default zero), retains the newest N
image-bearing items and replaces older image payloads with fixed notices. An item
can contain the four surround images; it is not necessarily one image or one
capture. Raw episode images and replay records remain intact. Rewriting history
can reduce provider prefix-cache reuse, so token and latency comparisons must
include cached and uncached usage.

## Evidence and limits

Raw client stdout remains `model_events.jsonl`. Canonical events and derived replay
envelopes carry provenance, call IDs, tool arguments and results. Native provider
outputs and input-capture metadata are recorded alongside tool events. Replays show
returned public assistant messages and available reasoning summaries; they do not
reconstruct unavailable internal reasoning. Model claims never override the private
BDDL/TaskMetric result.

New long-horizon batches explicitly use `unbounded` episode policy, recording
act/look counts without an episode action, physics-step or wall-time cutoff.
This is a batch setting, not a change to generic CLI defaults; native-loop model
turn/token limits are configured separately. The execution clock starts after
simulator initialization and MCP setup.
Initialization and shutdown have separate watchdogs. Historical time-budget batches
retain their configured execution deadline. Source and protocol changes are
recorded per attempt and do not rewrite historical benchmark scores.

Current timing policy:

- In time-budget mode, one execution deadline governs the model, environment steps and managed
  placement sampling. Managed actions use remaining episode steps rather than a
  separate 700-step cap; the former managed sampling 120-second/6000-tick cap is
  also removed. Native finite sampling attempts and geometry checks remain.
- FastMCP, the bridge and the Codex tool wait cover the configured execution
  budget plus 120 seconds for closure. This transport allowance does not extend
  the episode deadline. A validated `finish` request sets a thread-safe stop
  intent; the active action unwinds at its next checkpoint and the simulator
  owner then evaluates the actual state. No worker thread reads simulator state.
  Pending observations also use the episode clock rather than a 30-second queue
  timer, and are cancelled before any further background render on closure.
- New batch attempts use an initialization **no-progress** watchdog: real new
  startup milestones renew its 1800 seconds. Repeated heartbeats do not. There is
  no independent total-startup wall cap; pre-existing attempts retain their
  original startup policy. The execution clock takes over once the model starts.
- The supervisor retains a bounded closure grace for scoring/cleanup and native
  calls that never return. Network connection, image-download and SSH timeouts
  remain communication checks, not task-success criteria. A native crash may
  still make a final score unavailable; missing scores are never filled with zero.
- An accepted finish transitions through `scoring`, `persisting`, and
  `result_ready`; `closed=true` alone does not mean the reply is ready. Health and
  `closure.json` expose lifecycle metadata without private evaluator results.
  Each scoring/persistence phase receives 600 seconds by default
  (`finish_stage_timeout_seconds`); a ready result receives 120 seconds for client
  exit (`controller_exit_grace_seconds`). Repeated polls and coordinator adoption
  do not renew a phase's saved watchdog. These limits apply only to shutdown.
  A successfully sent HTTP reply is recorded separately from receipt in the model
  transcript. Forced cleanup writes sidecars while preserving raw client records.
- Audit schema v2 separates task success, evidence alignment, model finish receipt,
  and controller exit. A post-finish client failure cannot overwrite a scored goal
  failure. Explicit supervisor finish calls are tracked separately; unlabelled
  extra calls still fail trace alignment. Existing historical results stay intact.
- The recorder's 90-second pipe stall watchdog runs in the encoder worker.
  Bounded frame-queue backpressure checks episode cancellation every 50 ms.
  A pipe failure disables recording and preserves available evidence without
  aborting physics or replacing the independent final score.

Legacy profiles may configure 80 actions, 240 tool calls and 20,000 environment
steps per episode. New step-budget runs record their actual policy separately.
OpenCode and Kimi tool
timeout behavior remains unvalidated because their live clients are unavailable.

Validation levels remain separate: CPU contracts; real MCP transport with a mock;
real simulator primitives; real model with a real simulator; repeated fixed-task
evaluation; official physical-control evaluation. Passing one does not establish
the next. This project uses symbolic/idealized execution and does not claim a
physical-control leaderboard submission.

Architecture references were inspected at Jinkai's `jinkai/harness` commit
`0815cf234ee591bacd8017e9b1def4fac13e649b`: CLI adapters, centralized tool context,
FastMCP registration, and the native tool-call/image-history loop. The implementations
here retain OmniGibson's owner-thread model, RGB-only policy boundary, existing
executor profiles and independent evaluator. Navigation environments are not imported
or modified; task_notes provides episode-local model-authored planning state.
