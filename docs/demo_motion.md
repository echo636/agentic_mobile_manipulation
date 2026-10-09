# Visible manipulation for demos

`MAS_DEMO_MOTION=1` enables an opt-in presentation path in the RGB OmniGibson
adapter. The public MCP `act` interface and the model-selected image pixel do
not change. Before an ideal object-state operation, the adapter solves a
position-only reach with bounded torso and arm joints to that selected pixel
and advances the simulator for each joint-space waypoint. If a visually useful
stand-off remains outside the hand's workspace, it tries one closer approach
to the same model-selected point. It lifts a newly carried object and returns
the arm to a transport posture over recorded control steps, keeps carried
objects relative to the visible end effector, and
shows a short tool stroke for material and cutting actions. The spectator view
frames the interaction during those steps. `MAS_DEMO_MOTION=0` preserves the
existing executor and its experiment cohort.

These are actual simulator control steps and camera frames, not interpolated
video. The arm posture is kinematically projected and contact is idealized;
the existing checked state setter or transition still determines the final
effect. The video and evaluator explicitly record `demo_motion`, and
`demo_motion.jsonl` records the arm, selected point, step count and joint
distance. An unreachable target or a hand that remains too far from the
selected surface returns a structured error instead of silently changing the
object state. Do not describe this mode as physical grasp, force control,
or an official challenge submission.

Before using a clip as a demo result, review its continuous five-view video,
confirm that the arm/tool motion is visible rather than a repeated camera
frame, and compare the independent whole-task result with the original mode.
Diagnostic runs using simulator object handles are not autonomous model trials.
For model trials, retain the existing public-task, model, effort and audit
requirements in `atomic_action_experiment_requirements.md`.

A demo trial passes only if the model formally finishes, its ordered MCP calls
match the simulator, it selects and successfully executes the target `act`
primitive, the independent public-task evaluator succeeds, the five-view video
passes validation, and `demo_motion.jsonl` contains visible joint motion. The
reviewer must also watch the clip for pose/camera problems; those qualitative
findings remain separate from the machine checks. Preserve failed attempts.

To run a public-task model trial, use `scripts/run_demo_trial.py` with the
task instruction, an output directory outside Git, the local OmniGibson source
path, and the name of an already configured Codex provider profile. The script
starts the RGB MCP simulator with `MAS_DEMO_MOTION=1`, runs `gpt-6-astra` at
`low` effort through the real `act`/`finish` interface, and records model and
simulator evidence. It reads the provider endpoint from the installed Codex
config while keeping the rest of that config disabled; authentication stays
in the installed login. Run `scripts/audit_demo_trial.py <trial> --primitive
<action>` on the result; it checks the model/simulator call order, the selected
and executed action, formal finish, independent task score and video, then
calls `scripts/build_review.py` for the five-view page. Publish the review on
the internal experiment site at
`http://10.130.140.115:8769/autonomous_model_index.html`. A scripted
simulator-handle probe may diagnose posture or contact, but is not a model
trial and cannot satisfy this acceptance gate.

The 2026-10-09 `clean_a_keyboard` public instance 301 / seed 0 cohort has three
preserved model attempts. Attempt 1 stopped before model startup because the
private HTTP provider profile failed local validation. Attempt 2 used the real
model and recorded a grasp but failed to wipe or satisfy the task goal; it
formally finished with a blocked outcome. Attempt 3
used `gpt-6-astra` at `low` effort, selected `navigate_to`, `grasp`, and `wipe`
through MCP, and passed all seven checks in `demo_audit.json`. Its first wipe
reach missed by about 0.52 m; the one bounded approach to that same RGB point
reduced the error to about 0.00001 m before the stroke. The independent task
evaluator and continuous five-view video both passed. The review pages are
linked from the internal index above. The visible chair interaction and
idealized contact are still qualitative limitations of this demo mode.
