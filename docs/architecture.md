# Harness architecture

The default system uses RGB observations, callable workflow skills, and tools. It
has no explicit planning stack or persistent memory. The optional legacy
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
              Original or Official backend
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

The default nine tools are `start_observation`, `get_observation`,
`cancel_observation`, `observe`, `look`, `act`, `finish`, `list_skills`, and
`read_skill`. Four workflow documents cover manipulation, exploration, pick and
place, and recovery. They guide the model; they do not secretly run actions.

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
Background observation jobs run at owner-thread scheduling points, including while
the native model request waits on network I/O.

A transport timeout does not cancel an action or prove its failure. The bridge
uses request IDs for duplicate RPCs, but a new MCP call gets a new operation ID.
After an uncertain call, observe before deciding whether another action is needed.
Cross-process exactly-once recovery is not claimed.

## Policy clients and native loop

[CLI adapters](../src/manipulation_agent/clients/README.md) provide common
capability probes, project preparation, process execution and event parsing.
`run_codex_controller.py` remains the compatible default.
`run_model_controller.py --client codex|opencode|kimi` uses the same orchestration,
MCP preflight, formal finish check and execution clock. Configuration support is
separate from installation, authenticated execution and verified RGB delivery.
OpenCode and Kimi have configuration/parser tests but are not yet live-validated.

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

## Evidence and limits

Raw client stdout remains `model_events.jsonl`. Canonical events and derived replay
envelopes carry provenance, call IDs, tool arguments and results. Native provider
outputs and input-capture metadata are recorded alongside tool events. Replays show
returned public assistant messages and available reasoning summaries; they do not
reconstruct unavailable internal reasoning. Model claims never override the private
BDDL/TaskMetric result.

The 30-minute benchmark execution clock starts after simulator initialization and
MCP setup, immediately before the model starts. Initialization has a separate
watchdog. Native checks are cooperative; an external supervisor enforces process
limits when native code cannot return. Source and protocol changes are recorded
per attempt and do not rewrite historical benchmark scores.

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
or modified; explicit planning and memory remain deferred.
