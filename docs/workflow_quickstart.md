# RGB manipulation quickstart

The model searches, selects targets, chooses actions and checks results through
four fixed RGB views. The executor performs the selected motor operation using
private simulator geometry. Tool completion and the model's finish claim remain
separate from the independent task evaluator.

## Model loop

1. Call `initialize({})` and inspect front/back/left/right RGB. Initialization
   exposes the prepared episode's current snapshot; it does not reset the scene
   or perform an extra render.
2. With the default skills profile, read `visual-manipulation/SKILL.md` through
   `list_skills` and `read_skill`, then read relevant guidance as needed.
3. Choose one action using a pixel in a current image and the returned revision.
4. Inspect the four images and execution feedback returned by `act` or `look`.
   Select the next target from those images and repeat.
5. Verify the whole instruction using the latest returned images and feedback,
   then call `finish` with achieved/blocked/aborted and a reason. Wait for
   `closed=true`. An extra observation call is not required before finishing.

The minimal profile has four tools: initialize, look, act and finish. The default
skills profile adds list_skills and read_skill. Plan and memory stores are not
part of these profiles. The legacy workflow profile keeps its additional tools,
including observe and asynchronous capture jobs, for compatibility; default
prompts and skill documents do not require them.

## Images and actions

Each snapshot contains four 512×512 RGB views, image_ref, view, image hash and
revision. The cameras share capture_id, captured_at and sim_step. Their directions
are relative to the robot. Fixed-camera acquisition does not rotate the robot or
advance physics; no wrist images are active. MCP returns actual image content.
Private depth, object identity, maps, state predicates and evaluator scores are
not model observations.

`act(primitive, target, revision)` takes a target shaped as
`{"image_ref":"current image reference","point":[0.5,0.5]}`. Coordinates are
normalized: x runs left to right and y top to bottom. Any current camera view can
supply a target. Re-mark an object in the returned RGB after approaching it.
`look(yaw_degrees, revision)` explicitly turns the base within ±90 degrees;
positive yaw turns left. It is useful for changing physical viewpoint, not for
acquiring the four surrounding views.

The primitive catalog is defined by `executors/primitives.py`. It includes
navigation, grasp/placement/attachment, state controls, and checked material
actions (`wipe`, `sweep`, `vacuum`, `spray`, `spread`, `soak`, `cut`). `hang` uses
the attachment state for a compatible nail or hook. Material actions require a
compatible held tool and a visible selected target. Release and wait use target=null.
Use wait_seconds with the wait action when an appliance process needs simulation
time, and inspect the returned RGB before another action. Reading images alone
does not advance that process. Execution attempts can change the scene even when
they fail, so use their latest returned images and feedback for recovery.

## Entry points

Python 3.11+ is required. The CPU core can be checked without simulator packages:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

An episode server and MCP proxy use:

```bash
PYTHONPATH=src python -m manipulation_agent.vision_cli \
  --backend omnigibson --policy serve --agent-profile skills \
  --task turning_on_radio --instance 301 --output runs/rgb-radio --port 29440
PYTHONPATH=src python -m manipulation_agent.mcp_server \
  --bridge http://127.0.0.1:29440
```

Use the project simulator environment and licensed assets. Before a GPU run,
check resource availability and record host, interpreter, GPU UUID, unit/PID,
source, dependencies, assets and task instance. Do not alter navigation environments.

The default external controller is `scripts/run_codex_controller.py`.
`scripts/run_model_controller.py` also supports OpenCode and Kimi configuration
and parsing; their live RGB delivery needs separate validation. All adapters and
the native Responses loop receive their profile instructions from
`vision_policy.system_prompt`. Model credentials stay in private configuration.

## Source and evidence

- `skills/` contains frozen workflow documents and evidence rules.
- `tools/` under `src/manipulation_agent/` contains profile-filtered schemas and
  handlers; `vision_harness.py` owns revisions, budgets and the recorded episode.
- `observations/` defines camera and public RGB boundaries; `executors/` owns
  private geometry and motor operations.
- `mcp_server.py` returns tool text and image content; `bridge.py` serializes
  simulator access on its owning thread.
- `replay.py` and `replay_assets/` present recorded images, tool calls and public
  model output, preserving each attempt's source version.

Runs retain source and skill snapshots, image hashes, tool/model events and
independent evaluation when available. Missing images, reasoning or motion frames
are not reconstructed. Earlier three-camera and asynchronous runs keep their
original protocol; they do not validate the current initialize/action protocol.

See [the current RGB contract](rgb_protocol.md),
[harness architecture](architecture.md), [replay records](replay_trace.md) and
[historical four-camera validation](four_camera_validation_20260930.md).
For atomic-action experiments and publication, follow
[the acceptance requirements](atomic_action_experiment_requirements.md).
