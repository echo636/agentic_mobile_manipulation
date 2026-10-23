# RGB agent protocol · skills profile / rendered-pixel executor

The current default exposes initialize, look, act, finish, list_skills and read_skill. Explicit plan and memory stores are deferred. The agent performs RGB search, recognition, action choice, recovery and verification; only motor execution is idealized. Public model messages and MCP results are replayed verbatim without forced Chinese decision summaries. v0.1 oracle runs remain historical harness probes, not validation of this RGB protocol.

## Three distinct layers

- `skills/*/SKILL.md`: agent workflow instructions, with references; no simulator code. Five packages: visual-manipulation, goal-decomposition, visual-exploration, pick-and-place, failure-recovery.
- `src/manipulation_agent/tools/`: schemas, handlers and a profile-filtered registry. `minimal` exposes initialize/look/act/finish; `skills` adds list_skills/read_skill. The optional historical `workflow` profile retains observe, asynchronous observation jobs, update_plan, remember and recall for compatibility.
- `src/manipulation_agent/executors/`: the motor primitive catalog and OmniGibson adapter. Primitives are not the workflow skill packages.

`skill_runtime.py` freezes skill text and references into each run, hashes the bundle and permits only enumerated resources through read_skill. This lets a model consume skill documents without an arbitrary file-reading tool. This is a human-authored skill library, not automatic skill learning or evolution.

## Observation and action contract

Call initialize({}) first. It returns the prepared episode's current snapshot without resetting the scene or performing an additional render. After inspecting the initial images, choose an act or look and inspect the next four-camera snapshot included in that same response. There is no observation-job submission or polling step in the default model loop.

Once the execution clock is armed, tool replies also report `execution_budget.remaining_seconds`, sampled after the tool finishes. This is runtime metadata, not simulator state. It excludes initialization, includes model/tool waiting, and does not add or extend any limit. Before the clock is armed, the field is absent. The goal-decomposition skill uses it to prioritize remaining work and leave time for an honest finish.

The public observation contains observation_mode=rgb_only, revision, image metadata (image_ref, view, dimensions, MIME type and content hash), and a common capture_id/captured_at/sim_step/sim_time_seconds record. Four fixed camera views are returned at 512x512: front/back/left/right. No wrist cameras are active. Acquisition does not rotate the robot or advance physics. MCP attaches actual image content for every observation-bearing response; a file path is not used as a substitute for image input.

No task-object list, object names/IDs/categories, world poses, distances, depth, segmentation, scene graph, Inside/OnTop/Open/ToggledOn flags, inventory truth, global map or task score is returned. Normal conversation history contains the model's own hypotheses; optional historical workflow notes/plans are also hypotheses. Execution status/error categories are feedback about the requested motor operation, not a semantic state observation.

The model selects `target={image_ref, point:[x,y]}` in the latest RGB. x increases left-to-right, y top-to-bottom; both are normalized to [0,1]. `act(primitive,target,revision)` rejects names and stale references. release/wait use null target. look(yaw_degrees,revision) performs a bounded in-place turn; positive turns left. Executor attempts return the resulting RGB, including after an execution failure. Invalid requests rejected before execution do not move the robot.

All four current views are valid point-selection surfaces; no robot turn is needed simply to see another direction. Use the newest returned image_ref and revision after each action. If an appliance process needs time, use act with primitive=wait, target=null and wait_seconds, then inspect its returned RGB. Check the complete instruction against the latest returned images and feedback before finish; no extra capture is required solely for final verification. An executed operation and a model's finish claim remain separate from independent task success.

The exact selected pixel indexes private linear depth captured with the RGB.
The integer pixel index `(i,j)` reads that array cell; the calibrated camera
ray uses its renderer sample center `(i+0.5,j+0.5)`. The executor backprojects
the measured axial depth on this center ray into world coordinates. This is the target position: no collision-ray or visual-mesh depth
agreement check is performed, and no mesh hit replaces the backprojected point.
Depth must still be finite, positive and below the executor's existing 30 m limit;
an absent depth sample cannot define a 3D position.

Navigation uses the backprojected point directly, without an object lookup.
The four RGB views are 512×512 by default. Experiments may set
`MAS_RGB_IMAGE_SIZE` to 768 or 1024 to test small-object perception; the
selected normalized pixel still refers to the returned image's own dimensions.
`MAS_RGB_JPEG_QUALITY` defaults to 92 and permits 70–95 for transport-size
experiments without changing grounding coordinates.
`vision_cli --wall-seconds` sets the episode execution budget after bridge
readiness; autonomous experiments must keep that budget at least as long as
their model-controller timeout.
For a low selected point, public navigation now chooses a camera-visible
standoff from the measured head-camera height and keeps that point above the
lower image edge. A later grasp can approach the already selected object to
reach it; the model must select the object again from the fresh RGB returned by
navigation. This prevents a small ground-level tool from becoming only a few
pixels at the bottom of the image after the model navigates toward it.
Grasp, placement and state operations also need a simulator object handle.
They query the visible mesh surface nearest that same backprojected world point.
There is no independent camera ray cast for identity. World-space triangle
distances handle nonuniform asset scale; transformed visual bounds only accelerate
the query and never identify the object by themselves. Rigid geometry is cached
with live transforms, and cloth vertices are read live. Invisible and USD guide
meshes, including container fill volumes, are excluded. A point on the robot is
rejected instead of being assigned to another object. No object-name search,
task-scope filtering, or neighboring-pixel substitution occurs.

For `grasp`, the same query also includes the rendered meshes of active physical
macro particles, using their current PhysX poses and instance scales. A winning
particle surface returns `particle_target`; it is not assigned to the fixed
countertop beneath it. Fixed scene objects still return `fixed_object`. Material
actions continue to resolve the supporting object. This addition covers mesh
macro particles such as popcorn, not fluid isosurfaces or micro point instancers.

Controlled carry snapshots existing macro particles inside the carried
assembly's annotated container volumes, including nested rigid containers.
Grasp also captures particles supported by collision surfaces on the selected
assembly, such as diced food on a cutting board. Short downward collision probes
sample the lower hemisphere within the particle contact radius plus OG's contact
tolerance, including off-center pile contacts. Each probe respects the nearest
upward-facing collision. Lower particles already owned by the assembly
extend this support to piles, including mixed materials. Intervening rigid
objects block selection; neighboring table particles, side contacts, and
disconnected floating particles do not follow merely because they are nearby.
Annotated containment takes priority over surface support.
Particle poses follow their own supporting link through grasp, navigation and
placement; unrelated particles are untouched. Stable prim paths survive index
reordering, while deleted particles or replaced systems invalidate the carry
instead of restoring consumed material. Rejected placements restore the carry
and raw PhysX particle frames unless target identity changed. This supplemental
snapshot avoids the center/frame offset in OG 3.9.2's macro state restoration.
Particles follow the container through placement sampling and settling;
successful placement and
release clear the carry constraint and resume particle physics. This remains
ideal pose actuation, and does not claim physical grasp control or fluid carry.
Surface support is checked again after grasp and placement, with the original
contact probe transformed in the supporting link's frame. Carrying a board
does not pour or scrape its contents into another object: no transfer primitive
is added, and `spread` retains its absorbed-tool-material semantics.

The 2026-10-13 private instance-301 popcorn regression reproduced the old
255-to-0 loss on grasp. The repaired grasp, base turn, rejected-placement
rollback and successful microwave placement each retained the original 255
contained particles. Particle-surface rejection, release of the constraint and
video finalization also passed, alongside 80 focused CPU tests. This is executor
component validation, not evidence of autonomous whole-task success. Attempts,
failures and source snapshots are in `operations/particle_payload_repair_20261013`.

The previous depth/mesh agreement tolerance is not restored: measured surface
distance is a private diagnostic, not a threshold that rejects an otherwise valid
selection. The nearest mesh point does not replace the measured action position.
Depth, calibration, mesh ownership and query diagnostics remain executor-private.
The production observer supplies only RGB to the model. Optional
`MAS_RENDERER_PIXEL_OWNER=1` captures executor-private instance ownership with
the same RGB/depth packet. It resolves only numerically coincident geometry
candidates; unrelated or missing labels retain the geometric result. The exact
clicked pixel and backprojected point never change. Labels remain in private
frame records and are not returned to the model. The default remains disabled
while full-task startup and regression are being validated.

`place_on_top` first tries its ordinary nearby poses. If they all fail, it
also covers diagonal positions and yaw aligned with the selected support.
An explicitly requested yaw stays fixed. These alternatives retain the same
selected object, shelf, 20 cm neighborhood, collision checks, settling checks
and payload verification. Cheap overlap scores order candidates without
discarding them, and rejected trials restore the scene before the next trial.
For a selected open-container rim, nearby poses may span that same rim with
the bottom center over the cavity. An actual upward rim hit and nearby points
inside the annotated cavity identify the compartment; an upward obstruction
rejects it. These poses precede marginal edge-centered poses, while preserving
the requested yaw, selected support, neighborhood, collision, settling and
payload checks. The independent task evaluator is unchanged.

`place_next_to` selects a reference object from RGB, then searches nearby upward
physical surfaces without a floor/lawn category whitelist. When the reference's
supporting surface can be identified geometrically, candidates use that support
rather than stacking an item on one already placed nearby. Each candidate is
released, allowed to settle under gravity, and checked using official `NextTo`
and `OnTop`, motion stability and payload relations. Failed trials restore the
pre-trial scene and continue searching. Exhaustion restores the pre-action carry.
Detailed support, contact and adjacency checks are recorded privately.

The 2026-10-11 targeted validation used real public instance-301 scenes with
archived camera poses and private scripted setup. The historical sausage pixel
selected and carried only the sausage while the board remained in place. Three
sequential eggs all satisfied official `NextTo(tree)` and `OnTop(lawn)` at the
end; a coffee bottle satisfied `NextTo(coffee_maker)` and `OnTop(countertop)`.
The production spatial-point query also agreed with renderer instance labels for
441/441 exported pixels, and 26 focused CPU regression checks passed. These are
executor component results, not autonomous model episodes or a new 100-task
success rate. Raw fixtures, source patches, versions, per-candidate diagnostics
and videos are retained in the `navigation100_20261010` operations journal.

## Legacy workflow compatibility

Only the optional workflow profile exposes observe and start_observation/get_observation/cancel_observation. These retain their older synchronous or queued-capture behavior for recorded workflows and compatibility tests. Background capture stays on the simulator owner thread; no worker calls renderer/physics APIs. Cached job results can be stale, and their old refs cannot target a new action. See [legacy asynchronous acquisition](async_observation.md). Default prompts and skills do not use this interface.

## Comparison to the proposed harness diagram

The core loop returns four-direction RGB at initialization and after each action. The goal-decomposition skill guides initial subgoal selection and ongoing revision in conversation; a separate task-step stack and independent plan/memory stores remain deferred. The same LLM judges continuation or finish; there is no forced separate completion-model stage. The private final evaluator is not model input.


## Running

Default entry point: `python -m manipulation_agent.vision_cli --backend omnigibson --policy serve --task turning_on_radio --output runs/rgb-radio --port 29440`.

`manip-agent` and `scripts/run_s134.sh` now use vision_cli. `cli.py`, `harness.py`, and `policies.py` retain v0.1 oracle integration fixtures for historical reproduction; their presence is not permission to use oracle input in current experiments. The RGB harness does not call their observation/action handlers; it reuses only plan/memory/finish bookkeeping.

A direct Responses-compatible RGB loop is implemented in vision_policy.py. Real validation must specify which model client was used. Real-model evidence must establish image delivery, public-boundary compliance, exact controller/simulator call alignment and separate task success. Passing CPU contracts or storing images does not establish a visual task success.
