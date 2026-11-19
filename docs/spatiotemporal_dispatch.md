# RGB-grounded spatiotemporal dispatch

A separate controller compiles a user instruction and the initial four RGB images into a dependency graph. A single mobile manipulator performs active work while independent processes can continue in the environment. The existing actor and DAG tooling retain their default behavior. Enable this controller with `manip-dispatch` or a batch manifest specifying `dispatch_policy`.

## Run an episode

Install the controller extra with `python -m pip install '.[dispatch]'` in the controller's Python 3.11+ environment. Start the existing simulator bridge with the public instruction, `--unbounded-episode --public-robot-clock` and the base motor protocol. A remote simulator can use a separate interpreter and installation. Its independent evaluator owns task scoring.

For local visual inference, provision the desired Qwen2.5-VL-7B weights and a compatible CUDA/PyTorch environment with the `dispatch-vision` extra. The visual service accepts requests on loopback and uses the designated GPU.

```sh
python -m manipulation_agent.clients.local_vision --model-path /models/Qwen2.5-VL-7B-Instruct --port 18467

manip-dispatch --dispatch-policy temporal --instruction "$TASK_INSTRUCTION" \
  --mcp-command python --mcp-args-json '["-m","manipulation_agent.mcp_server","--bridge","http://127.0.0.1:29430"]' \
  --output /runs/dispatch-001 --planner-model gpt-6-astra --planner-reasoning-effort high \
  --model Qwen2.5-VL-7B-Instruct --fast-vision-port 18467 --model-provider-profile yyf
```

The native planner uses the configured Codex provider and a writable isolated `MAS_CLIENT_STATE_ROOT`. Supply `--model-provider-gateway` for an existing loopback queue. To use an independent native model for visual grounding, omit `--fast-vision-port` and select its `--model` and `--reasoning-effort`. Both roles receive RGB as images. Neither model has a motor, file or simulator query tool.

Use `--dispatch-policy serial` for the matched control. It retains the model calls, evidence, graph vocabulary and motor interface, runs one transport per item and reserves passive process intervals against independent robot work. A batch uses manifest fields `dispatch_policy`, `public_robot_clock`, `planner_model`, `planner_reasoning_effort`, `model` and `episode_budget_policy=unbounded`. The scheduler config accepts `fast_vision_port`. Install the controller dependencies in the interpreter launching the batch. Model calls and robot closures are recorded separately from the independent simulator score.

## Execution semantics

- The compiler supplies active handling time, regions, directed transit time and route-length estimates, load units, observed or user-described carriers, visual assertions and protected user outcomes/order. A process window links the end of a visually confirmed startup to an earliest check **start** and latest check **completion**. The model declares which timing bounds come from the instruction. Each resource is reserved from startup through the last associated check.
- CP-SAT schedules one mobile robot and tracks the last confirmed region of each carrier. Loads must fit, members must have no intra-batch dependency and total time includes approach, movement and handling. With route estimates, the objective minimizes makespan and then route length at decimetre precision. Candidate batches are bounded per route. An incumbent provides a fallback when search times out. Logs distinguish `optimal`, `feasible`, `heuristic_feasible`, `unknown` and `infeasible`. Optimality is relative to the candidate set and input estimates.
- The fast controller binds each targeted motor command to a pixel in a hash-checked current RGB view. The local Qwen role receives labelled, aspect-preserving canvases with explicit integer pixel extents. The adapter removes padding, maps the chosen pixel back to the original view and rejects out-of-frame selections. It uses the public elapsed clock, optional odometry and explicitly sourced gripper occupancy. Semantic observations carry their source view, revision and uncertainty. A grounding call can receive up to 12 selected beliefs, five recent events and two image references or selection panels beyond the four current images. Repair input grows with the affected subgraph and live timers. Confirmed navigation-only trips can update time and distance estimates. Episode-local macros reuse a verified primitive sequence and rebind targets against current images.
- The dispatcher checks active timers between motor calls and reserves time for the next estimated action and return. Explicit waits are split at pending events. The controller can suspend work, reobserve and resume only after satisfying dependencies. It cannot interrupt a non-preemptible motor command midway. A deadline schedules a visit. Assessing material condition requires visible evidence. A completed group needs an evidence statement for each member.
- An exception or recurrent visible access pattern triggers local edits. The graph rejects cycles, edits to completed or unrelated nodes, lost user order or assertions, re-anchoring an active process and relaxation of an explicit user time bound. A damaged motor readback is recovered through a snapshot read. An uncertain motor command is never automatically sent twice.
- `dispatch.jsonl` records model inputs excluding image bytes, model outputs, schedules, motor requests, verified node IDs, evidence, repairs and timer events. Simulator events and camera files retain the executed trajectory. `controller.json` records closure, model-call counts and robot elapsed time. A policy's `achieved` claim alone does not establish task success. Use independent evaluator output and certified navigation routes for benchmark metrics.
