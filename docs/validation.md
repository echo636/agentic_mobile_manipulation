# v0.1 validation — 2026-09-30

This is a development pilot on public BEHAVIOR challenge task instances, using oracle task-object observations and ideal execution. It is not a leaderboard submission or a statistically meaningful benchmark success rate. All cases below use instance 301 and seed 0.

| Task / run | Controller | Outcome | Semantic actions | Completed env steps | Extra volume-sampling physics ticks |
|---|---|---|---:|---:|---:|
| turning_on_radio / mas_radio_r3 | Scripted probe | BDDL and official success; Q=1 | 2 | 150 | — |
| turning_on_radio / mas_radio_model_r1 | gpt-6-astra via MCP | Formal finish + independently verified success; Q=1 | 2 | 150 | — |
| picking_up_trash / mas_trash_r1 | Scripted, original symbolic ray sampler | First PLACE_INSIDE failed; Q=0 | 4 | 250 | — |
| picking_up_trash / mas_trash_r2 | Scripted, official volume sampler | All three goals satisfied; Q=1 | 12 | 750 | 263 |
| picking_up_trash / mas_trash_model_r1 | gpt-6-astra via MCP, volume sampler | All three goals satisfied; Q=1 | 14 | 850 | 239 |
| putting_dirty_dishes_in_sink / mas_dishes_r1 | Scripted, volume sampler selected | PhysX invalid robot quaternion during first bowl grasp; final evaluation unavailable | Incomplete | Incomplete | Placement not reached |

Two earlier radio startup failures are retained: a scene without an embedded robot and an unsupported base-controller parameter. Their fixes were validated before the successful radio runs. A CPU mock controller initially failed to call `finish`; the controller audit now rejects a successful process exit without formal closure.

The real trash model first tried to open a bin that has no Open state. It received a precondition error, revised its plan, and completed all three placements without an operator choosing actions. The run does not demonstrate long-term learning or benefit from memory: the model did not call `remember` in this episode.

23 CPU tests pass. Real MCP SDK transport was tested against a mock backend. Radio and trash model traces were separately cross-checked against simulator traces: respectively 7 and 21 tool calls, identical order/arguments, only allowed MCP tools, formal closure, BDDL success and official TaskMetric success. The direct Responses-compatible provider is implemented but was not the provider used in these real-model runs.

The trash controller recorded a dirty source tree because the offline-only `audit.py` module was added while the simulator was already running. The exact digest difference was reconstructed to that one added file; online execution modules were unchanged. Original records retain this distinction. Runtime snapshots, dependencies, task-instance/BDDL hashes and a separate reconciliation JSON are available in the experiment archive.

Known limits: the original symbolic ray sampler can fail inside small containers; forced assisted grasp was numerically unstable for a bowl in the restaurant scene. The precise cause of the latter is not yet established. Opening, closing, on-top placement and other skills have code paths but are not all covered by successful real-simulator tests in this pilot. No 100-task or multi-seed result is claimed.

Primary execution host: S134 (`10.130.136.134`), project-specific Python 3.11 environment, OG 3.9.2, Isaac Sim 5.1, Torch 2.7.0+cu128, BDDL 3.7.0, BEHAVIOR assets 3.9.0, robot assets 3.8.2. Every archived run specifies GPU UUID, PID/unit, interpreter, commit and output path. Source commits for the two real-model runs: `b676ac4` (radio) and `163a61b` (trash). Navigation source review: `dadwadw233/habitat-gs@0815cf234ee591bacd8017e9b1def4fac13e649b`, branch `jinkai/harness`; its benchmarks were not rerun.

Lab report: [system and experiments](http://10.76.5.241:8765/manipulation_system.html#experiments). Evidence is also archived in the parent project's `operations/system_build_20260930/` directory. This repository's Git history is independent; no upstream source repository was pushed or modified.
