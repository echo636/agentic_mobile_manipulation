# RGB agent protocol · skills profile / rendered-pixel executor

The current default exposes observe, look, act, finish, list_skills and read_skill. Explicit plan and memory stores are deferred. The agent performs RGB search, recognition, action choice, recovery and verification; only motor execution is idealized. Public model messages and MCP results are replayed verbatim without forced Chinese decision summaries. v0.1 oracle runs remain historical harness probes, not validation of this RGB protocol.

## Three distinct layers

- `skills/*/SKILL.md`: agent workflow instructions, with references; no simulator code. Four packages: visual-manipulation, visual-exploration, pick-and-place, failure-recovery.
- `src/manipulation_agent/tools/`: schemas, handlers and a profile-filtered registry: six current tools. The optional historical workflow profile additionally exposes update_plan, remember and recall; minimal reproduces the older four-tool interface.
- `src/manipulation_agent/executors/`: ten motor primitives and the OmniGibson adapter. Primitives are not the workflow skill packages.

`skill_runtime.py` freezes skill text and references into each run, hashes the bundle and permits only enumerated resources through read_skill. This lets a model consume skill documents without an arbitrary file-reading tool. This is a human-authored skill library, not automatic skill learning or evolution.

## Observation and action contract

The public observation contains only observation_mode=rgb_only, revision, and image metadata: image_ref, view, dimensions, MIME type and content hash. Three onboard camera views are returned at 512x512: head and left/right wrists. MCP attaches actual image content for every observation-bearing response; a file path is not used as a substitute for image input.

No task-object list, object names/IDs/categories, world poses, distances, depth, segmentation, scene graph, Inside/OnTop/Open/ToggledOn flags, inventory truth, global map or task score is returned. Normal conversation history contains the model's own hypotheses; optional historical workflow notes/plans are also hypotheses. Execution status/error categories are feedback about the requested motor operation, not a semantic state observation.

The model selects `target={image_ref, point:[x,y]}` in the latest RGB. x increases left-to-right, y top-to-bottom; both are normalized to [0,1]. `act(primitive,target,revision)` rejects names and stale references. release/wait use null target. look(yaw_degrees,revision) performs a bounded in-place turn; positive turns left. Every attempt returns fresh RGB.

Inside the current V2 motor executor, the exact selected pixel indexes private renderer instance/depth buffers captured with the RGB. This routes the surface actually visible at that pixel; no object-name search, task-scope filtering, candidate generation, nearby-pixel snapping or hidden-target selection is performed. The older V1 collision ray could hit invisible robot collision geometry despite a visible bin/floor selection. Renderer buffers/calibration are archived privately in executor_frames, never exposed in MCP. A diagnostic ray remains in offline records; it does not replace the chosen rendered surface.

The executor uses private geometric state to follow a connected traversability path at <=0.5m/s, turning at <=60deg/s with explicit ideal posture holding and official symbolic primitives/volume sampling for manipulation. These do not represent physically controlled locomotion or learned grasping. The agent still has to decide what to look at, where to go and which visible object to operate on. No object state is sent back with the result. Error messages are allowlisted; full upstream errors and private grounding stay in offline audit records.

Evaluation is private. finish closes the episode and runs BDDL/TaskMetric, returning only closure and the agent's claimed outcome. It cannot be used to query a score and continue acting. The raw task/scene/evaluator records remain in the experiment archive and are not exposed by MCP.

## Running

Default entry point: `python -m manipulation_agent.vision_cli --backend omnigibson --policy serve --task turning_on_radio --output runs/rgb-radio --port 29440`.

`manip-agent` and `scripts/run_s134.sh` now use vision_cli. `cli.py`, `harness.py`, and `policies.py` retain v0.1 oracle integration fixtures for historical reproduction; their presence is not permission to use oracle input in current experiments. The RGB harness does not call their observation/action handlers; it reuses only plan/memory/finish bookkeeping.

A direct Responses-compatible RGB loop is implemented in vision_policy.py. Real validation must specify which model client was used. Real-model evidence must establish image delivery, public-boundary compliance, exact controller/simulator call alignment and separate task success. Passing CPU contracts or storing images does not establish a visual task success.
