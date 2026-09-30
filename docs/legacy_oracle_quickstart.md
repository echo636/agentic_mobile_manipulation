# Manipulation Agentic System

An independently maintained, auditable agent harness for mobile manipulation in OmniGibson. This repository has its own Git history and does not import the lvzhang, wenbo, or Habitat-GS source trees.

The first research protocol combines **oracle task-object observations**, **official symbolic manipulation primitives**, and **ideal navigation to a reachable map endpoint**. This isolates planning, object selection, ordering and recovery from low-level control. It is **not an official BEHAVIOR challenge submission** and does not establish visual perception or physical manipulation performance.

## What is implemented

- A shared tool catalog: `observe`, `act`, `update_plan`, `remember`, `recall`, `finish`.
- Semantic actions: navigate, grasp, place inside/on top, open/close, toggle on/off, release and bounded wait.
- An episode owner that enforces schemas, observation revisions, object identity, hand/container/reachability preconditions and action/step/time budgets.
- Replay-safe request IDs, structured failures, fresh observations after partial execution, plan dependencies and memory provenance.
- A Responses-compatible model loop, an optional installed Codex client controller, and a separate deterministic integration policy.
- An actual MCP stdio server, a loopback HTTP bridge, and serialized main-thread simulator execution.
- Official public task-instance restoration, BDDL goal evaluation, pinned official TaskMetric, JSONL action traces, RGB/depth snapshots and per-episode HTML.

`finish(achieved)` records a claim. The independent evaluator decides task success. Goal truth and scores are not returned to the active controller. Plan completion remains an agent claim linked to action evidence, not an independently verified BDDL subgoal.

## CPU quick start

Python 3.11 or newer; the core has no third-party dependencies.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m manipulation_agent.cli --backend mock --output runs/mock-radio
```

The mock proves software behavior only. Every output directory must be new. Open `runs/mock-radio/index.html` and inspect `run.json` / `events.jsonl`.

## Real simulator setup

The current adapter pins **OmniGibson 3.9.2 / Isaac Sim 5.1 / Python 3.11 / PyTorch 2.7.0+cu128**, BEHAVIOR assets 3.9.0 and robot assets 3.8.2. Install the official BEHAVIOR environment and licensed assets first; do not pip-install a second incompatible Torch/Isaac stack into it. OmniGibson 3.9.3 changes the environment API; this adapter rejects that version until its integration is validated.

Required environment variables:

```bash
export OMNI_KIT_ACCEPT_EULA=YES
export OMNIGIBSON_HEADLESS=1
export OMNIGIBSON_GPU_ID=0
export OMNIGIBSON_DATA_PATH=/your/project/data/omnigibson
export OMNIGIBSON_APPDATA_PATH=/your/project/cache/omnigibson
export PYTHONPATH=/your/checkout/src
```

The data root must contain `behavior-1k-assets`, `omnigibson-robot-assets` and `2026-challenge-task-instances`. Never place asset decryption keys in source control or reports.

```bash
python -m manipulation_agent.cli --backend omnigibson --policy scripted \
  --task turning_on_radio --instance 301 --output runs/radio-301
```

The deterministic probes support radio, three soda cans into trash, and dirty dishes into a sink. A recipe being available does not mean its simulator run passed. Runtime records determine the verified scope.

The bundled catalog lists 100 tasks, with 50 detailed instructions available in the captured official page manifest. A missing instruction requires an explicit `--instruction`; the CLI never silently reuses the radio instruction for another task.

`--inside-placement symbolic_raycast` preserves the official symbolic primitive and is the default. Its ray sampler failed on the first soda-can placement in the tested trash instance. The explicit alternative `--inside-placement official_volume` uses the same pinned simulator's `Inside.set_value`: it releases the held object, samples actual poses inside the fillable volume, runs collision/settling checks, then verifies `Inside.get_value` again. It does not read the task goal or write success flags. Failed placement may leave an object released. Volume sampling is limited to 120 seconds and at most `min(6000, remaining_action_steps * 4)` internal physics ticks; those ticks are recorded separately from `env.step`, so official time metrics do not represent total physics effort.

```bash
python -m manipulation_agent.cli --backend omnigibson --policy scripted \
  --task picking_up_trash --instance 301 --inside-placement official_volume \
  --output runs/trash-volume-301
```

For the lab's configured S134, `scripts/run_s134.sh` uses the project-specific environment and storage. Recheck GPU ownership, memory limits, disk and quota before starting a new job. Run it under a unique user systemd unit; do not stop other projects' processes.

## Model control

Responses-compatible endpoints:

```bash
# Set privately; never commit a key or include it in a command log.
export LLM_API_KEY=YOUR_KEY
export LLM_BASE_URL=https://YOUR_ENDPOINT/v1
export LLM_MODEL=YOUR_MODEL
python -m manipulation_agent.cli --backend omnigibson --policy responses \
  --task turning_on_radio --instance 301 \
  --instruction 'Turn on the radio on the living-room table.' --output runs/model-radio
```

This initial model interface consumes structured oracle state. RGB/depth snapshots are archived for diagnosis; the Responses policy currently does not inject image pixels. Do not call it a visual policy.

Alternatively start `--policy serve --controller codex/YOUR_MODEL --port 29430`. The simulator remains on its main thread. The optional `scripts/run_codex_controller.py` launches the installed client with existing authentication and the MCP command you specify; it disables shell, other agents, plugins, browser and web tools. It does not read or copy authentication files. The simulator record must be joined with the controller trace before calling it a real-model episode.

## MCP

Install the optional `mcp==1.28.1` dependency in a CPU Python environment. Start the episode bridge, then configure an MCP client to run:

```bash
PYTHONPATH=src python -m manipulation_agent.mcp_server --bridge http://127.0.0.1:29430
```

The MCP protocol uses stdout; diagnostics use stderr. The HTTP bridge binds only to loopback. For another machine, launch the stdio proxy through SSH or use an SSH tunnel. No evaluator, arbitrary Python, shell, reset, or arbitrary file-read tool is exposed.

Real transport test, independent of the simulator:

```bash
PYTHONPATH=src python scripts/probe_mcp.py runs/mcp-probe
```

## Research boundaries

The official symbolic executor changes poses/states and establishes grasps directly. It still needs compatible controllers, valid task objects and reachable predicate samples. Our navigation adapter uses the static eroded traversability map and teleports to a reachable endpoint; it does not simulate traversing the path or prove dynamic collision avoidance. Its reported path length and settling time must not be compared as physical navigation efficiency.

Oracle task-object identity, states and relations simplify perception. That is a separate assumption from ideal motor execution. A later vision-only arm must replace this observation adapter while preserving the same skills and evaluation contract. Cleaning, cutting, heating, liquids, attachment, dual-arm planning, learned physical execution and all-100-task coverage are not claimed by v0.1.

Official challenge rules currently require onboard RGB/depth/proprioception and forbid simulator-only policy inputs. Our symbolic research runs remain separate from leaderboard results. See [official evaluation rules](https://behavior.stanford.edu/challenge/evaluation.html).

See [architecture](docs/architecture.md) and [source provenance](docs/provenance.md).

## Verified pilot and evidence audit

Real model + MCP + OmniGibson episodes passed for radio and three soda cans into a bin. A restaurant dirty-dishes episode failed during bowl grasp with invalid physics state; it has no final task score. See [validation results and limitations](docs/validation.md), including all failed iterations and the distinction between scripted and model runs.

Cross-check a model trace against its simulator trace without modifying either:

```bash
PYTHONPATH=src python -m manipulation_agent.audit \
  --run-dir /path/to/simulator/run \
  --controller-dir /path/to/controller/run \
  --output /path/to/paired_audit.json
```

The audit checks exact tool order and arguments, permitted tools, formal closure, source commit and independent task success. Same-commit checks do not by themselves establish byte-identical dirty working trees; preserve and inspect source digests and snapshots as well.
