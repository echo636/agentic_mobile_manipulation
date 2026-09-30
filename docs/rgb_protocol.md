> 当前默认为minimal四工具闭环；plan/memory及workflow技能加载默认停用。使用`--record-video`录制全部env.step与离线第三人称，图像输入边界保持RGB-only。旧workflow模式仅显式启用。

# RGB agent protocol v0.2

The user requires an agent that perceives RGB and performs its own search, recognition, planning, memory, recovery and verification. Only motor execution is idealized. v0.1 oracle runs remain historical harness probes, not validation of this RGB protocol.

## Three distinct layers

- `skills/*/SKILL.md`: agent workflow instructions, with references; no simulator code. Four packages: visual-manipulation, visual-exploration, pick-and-place, failure-recovery.
- `src/manipulation_agent/tools/`: schemas, handlers and one registry for nine callable tools: observe, look, act, update_plan, remember, recall, list_skills, read_skill, finish.
- `src/manipulation_agent/executors/`: ten motor primitives and the OmniGibson adapter. Primitives are not the workflow skill packages.

`skill_runtime.py` freezes skill text and references into each run, hashes the bundle and permits only enumerated resources through read_skill. This lets a model consume skill documents without an arbitrary file-reading tool. This is a human-authored skill library, not automatic skill learning or evolution.

## Observation and action contract

The public observation contains only observation_mode=rgb_only, revision, and image metadata: image_ref, view, dimensions, MIME type and content hash. Three onboard camera views are returned at 512x512: head and left/right wrists. MCP attaches actual image content for every observation-bearing response; a file path is not used as a substitute for image input.

No task-object list, object names/IDs/categories, world poses, distances, depth, segmentation, scene graph, Inside/OnTop/Open/ToggledOn flags, inventory truth, global map or task score is returned. Notes and plans contain the model's own hypotheses. Execution status/error categories are feedback about the requested motor operation, not a semantic state observation.

The model selects `target={image_ref, point:[x,y]}` in the latest RGB. x increases left-to-right, y top-to-bottom; both are normalized to [0,1]. `act(primitive,target,revision)` rejects names and stale references. release/wait use null target. look(yaw_degrees,revision) performs a bounded in-place turn; positive turns left. Every attempt returns fresh RGB.

Inside the motor executor, the selected pixel is converted to a camera ray using private camera calibration, and the first physical collision surface identifies the action target. This is explicit ideal motor grounding: no object-name search, task-scope filtering, candidate generation, nearest semantic target snapping or hidden-target selection. Collision geometry may differ from visible geometry (especially glass/small thin features); failures are reported and require a new visual selection.

The executor uses private geometric state to navigate to a connected map endpoint and official symbolic primitives/volume sampling for manipulation. These do not represent physically controlled locomotion or learned grasping. The agent still has to decide what to look at, where to go and which visible object to operate on. No object state is sent back with the result. Error messages are allowlisted; full upstream errors and private grounding stay in offline audit records.

Evaluation is private. finish closes the episode and runs BDDL/TaskMetric, returning only closure and the agent's claimed outcome. It cannot be used to query a score and continue acting. The raw task/scene/evaluator records remain in the experiment archive and are not exposed by MCP.

## Running

Default entry point: `python -m manipulation_agent.vision_cli --backend omnigibson --policy serve --task turning_on_radio --output runs/rgb-radio --port 29440`.

`manip-agent` and `scripts/run_s134.sh` now use vision_cli. `cli.py`, `harness.py`, and `policies.py` retain v0.1 oracle integration fixtures for historical reproduction; their presence is not permission to use oracle input in current experiments. The RGB harness does not call their observation/action handlers; it reuses only plan/memory/finish bookkeeping.

A direct Responses-compatible RGB loop is implemented in vision_policy.py. Real validation must specify which model client was used. Real-model evidence must establish image delivery, public-boundary compliance, exact controller/simulator call alignment and separate task success. Passing CPU contracts or storing images does not establish a visual task success.
