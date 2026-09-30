# RGB agent protocol · skills profile / rendered-pixel executor

The current default exposes start_observation, get_observation, cancel_observation, observe, look, act, finish, list_skills and read_skill. Explicit plan and memory stores are deferred. The agent performs RGB search, recognition, action choice, recovery and verification; only motor execution is idealized. Public model messages and MCP results are replayed verbatim without forced Chinese decision summaries. v0.1 oracle runs remain historical harness probes, not validation of this RGB protocol.

## Three distinct layers

- `skills/*/SKILL.md`: agent workflow instructions, with references; no simulator code. Four packages: visual-manipulation, visual-exploration, pick-and-place, failure-recovery.
- `src/manipulation_agent/tools/`: schemas, handlers and a profile-filtered registry: nine current tools. The optional historical workflow profile additionally exposes update_plan, remember and recall; minimal reproduces the older four-tool interface.
- `src/manipulation_agent/executors/`: ten motor primitives and the OmniGibson adapter. Primitives are not the workflow skill packages.

`skill_runtime.py` freezes skill text and references into each run, hashes the bundle and permits only enumerated resources through read_skill. This lets a model consume skill documents without an arbitrary file-reading tool. This is a human-authored skill library, not automatic skill learning or evolution.

## Observation and action contract

The public observation contains observation_mode=rgb_only, revision, image metadata (image_ref, view, dimensions, MIME type and content hash), and a common capture_id/captured_at/sim_step/sim_time_seconds record. Four fixed camera views are returned at 512x512: front/back/left/right. No wrist cameras are active. Acquisition does not rotate the robot or advance physics. MCP attaches actual image content for every observation-bearing response; a file path is not used as a substitute for image input.

No task-object list, object names/IDs/categories, world poses, distances, depth, segmentation, scene graph, Inside/OnTop/Open/ToggledOn flags, inventory truth, global map or task score is returned. Normal conversation history contains the model's own hypotheses; optional historical workflow notes/plans are also hypotheses. Execution status/error categories are feedback about the requested motor operation, not a semantic state observation.

The model selects `target={image_ref, point:[x,y]}` in the latest RGB. x increases left-to-right, y top-to-bottom; both are normalized to [0,1]. `act(primitive,target,revision)` rejects names and stale references. release/wait use null target. look(yaw_degrees,revision) performs a bounded in-place turn; positive turns left. Every attempt returns fresh RGB.

Inside the current V3 motor executor, the exact selected pixel indexes private linear depth captured with the RGB. A ray through that pixel ignores robot collision proxies and accepts the first external hit only when it agrees with the rendered depth (within max(3cm, 2% of camera-to-surface distance)). There is no object-name search, task-scope filtering, candidate generation, nearby-pixel snapping or hidden-target selection. Depth/calibration are archived privately in executor_frames, never exposed in MCP. V1 could hit invisible self collision proxies; V2 added rendered instance maps but its trial ended in a native render crash. V3 removes segmentation as a mitigation; this does not establish the crash's root cause.

start_observation queues a read-only job and returns its ID immediately. get_observation returns planned/running, or passed with actual four-image content. cancel_observation requests cancellation; already completed jobs are immutable. A job records common capture time and simulation step. The bridge services capture on the simulator owner thread while external LLM/network work proceeds independently. No worker thread calls renderer/physics APIs. A render/capture batch is atomic, so a cancel arriving after completion cannot undo it.

The job's stale flag compares its frozen result with the current observation. Cached old frames remain available as evidence but act rejects expired refs. All four current camera images are valid point-selection surfaces; no robot turn is needed simply to observe another direction. Pending capture does not reserve robot motion; serialized scheduling ensures capture occurs wholly before or after a motor operation, never across one.

## Comparison to the proposed harness diagram

The core loop and asynchronous four-direction observation are implemented. Explicit initial decomposition/task-step Stack and independent plan/memory remain deferred. The same LLM judges continuation or finish; there is no forced separate completion-model stage. The private final evaluator is not model input.


## Running

Default entry point: `python -m manipulation_agent.vision_cli --backend omnigibson --policy serve --task turning_on_radio --output runs/rgb-radio --port 29440`.

`manip-agent` and `scripts/run_s134.sh` now use vision_cli. `cli.py`, `harness.py`, and `policies.py` retain v0.1 oracle integration fixtures for historical reproduction; their presence is not permission to use oracle input in current experiments. The RGB harness does not call their observation/action handlers; it reuses only plan/memory/finish bookkeeping.

A direct Responses-compatible RGB loop is implemented in vision_policy.py. Real validation must specify which model client was used. Real-model evidence must establish image delivery, public-boundary compliance, exact controller/simulator call alignment and separate task success. Passing CPU contracts or storing images does not establish a visual task success.
