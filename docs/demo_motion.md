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

## First-ten public-task cohort

The next demo cohort uses the first ten entries, in insertion order, of the
pinned `src/manipulation_agent/tasks.json` catalog: `turning_on_radio`,
`picking_up_trash`, `putting_away_Halloween_decorations`,
`cleaning_up_plates_and_food`, `can_meat`, `setting_mousetraps`,
`hiding_Easter_eggs`, `picking_up_toys`, `rearranging_kitchen_furniture`, and
`putting_up_Christmas_decorations_inside`. Each uses public instance 301,
seed 0, the catalog's public instruction, the skills MCP profile, and
`gpt-6-astra` at `low` effort. The model selects every `act` action and RGB
pixel. The private executor may use depth, selected-surface geometry, joint
state/Jacobians, traversability and checked object-state setters; none of those
are supplied to the model. No scripted object handles or BDDL goal assignments
are used as action targets. Attempt directories are immutable; failures stay
visible in the cohort denominator.

`scripts/run_demo_first10.py` schedules these tasks across the declared GPU
slots but serializes model trials because the configured provider returned
HTTP 429 under concurrent requests. It calls `scripts/audit_demo_trial.py` and `scripts/build_review.py` for
each completed attempt. The generic audit requires at least one model-selected
and successfully executed manipulation action, in addition to formal finish,
ordered model/simulator call alignment, official whole-task success, passed
video, and recorded visible joint motion. Multi-object tasks must pass their
whole official goal; a partial sequence does not count. The recorded motion is
genuine simulator stepping, while object contact and goal-state changes remain
idealized and are labeled as such in the replay.

The first launch used the 1500-second controller limit and two-step video
render stride. The radio passed. The trash task reached the controller limit
with partial progress; Halloween, plates/food and mousetraps were interrupted
by provider HTTP 429 after recording partial model actions. A proposed
four-step stride failed the adapter's explicit 1–3 contract before model
startup for the remaining five tasks; those empty attempts are retained as
infrastructure failures. Subsequent attempts use valid stride 3, a 3600-second
controller limit, and serialized provider calls. `--attempt 2 --retry-failures`
selects only tasks without an earlier passing audit. Audit pages and raw runs
must retain their distinct attempt numbers; a later pass does not erase a
prior failure.

The first serialized retry exposed a scheduler race: `Popen` returned before
the new trial directory existed, so the initial directory-only busy check
briefly launched the other retries. They were stopped before model startup,
with their attempt directories retained. The scheduler now regards a live
child process as busy immediately; a CPU regression test covers this exact
race and restart from an incomplete directory. Later retries use another
attempt number after the continuing trash trial finishes.

The trash r2 model trial formally finished but failed the official 3/3 goal.
Replay inspection shows the bin upright during the first `place_inside` and
tipped after the next base navigation began with the arm still extended.
The second placement also failed because the near-bin arm displaced a resident
can during sampler/settling physics. Scripted diagnostic probes, clearly
excluded from autonomous scores, placed all three cans successfully with the
robot away from the bin; the recorded near-bin arm pose reproduced the resident
loss. The executor now preserves existing fillable-link-relative contents
during the placement transaction and retracts the visible arm after a
successful placement before another navigation action. The same near-bin
simulator probe passed with the preservation fix: both cans remained officially
`Inside`. A CPU regression test goes red against the pre-fix placement code
and green with the fix. The revised model trial must still pass the independent
whole-task evaluator; diagnostic success alone is not sufficient.

`scripts/watch_demo_site.py` publishes each new summary or audit to the
internal first-ten review index while the batch scheduler is alive. The
publisher reuses unchanged media exports so periodic updates do not copy the
same video and frame directory repeatedly. The public index includes both
reviewed attempts and pre-model infrastructure failures, with attempt numbers
and separate evidence links.

Long model turns can hit provider HTTP 429 even with one task at a time. New
demo trials may use `--rate-limit-resumes 3` in the controller. After a 120 s
backoff, the installed Codex CLI resumes the **same recorded thread** against
the **same live MCP bridge and simulator episode**, preserving the original
model, `low` effort, tool allowlist, sandbox and login. Each continuation has
its own raw event and stderr files; the combined model stream is used for
ordered call alignment and formal-finish validation. A continuation is part
of the same attempt, not a fresh policy trial. If the rate limit persists or
the episode budget expires, the attempt still fails and is preserved.

The initial first-ten attempts mistakenly used the desktop `ccswitch` provider
and its existing Codex login. That provider returned 429; those attempts do
not establish any limit on the user-provided experiment key. The experiment
key has since been recovered from the user's message into a permission-600
Codex home outside Git, paired with the user-provided `api.gpt.ge` endpoint
(`https://api.gpt.ge/v1` for the Responses route). A small `gpt-6-astra`/`low`
connectivity request completed through that isolated login. New first-ten
runs require `--codex-home` and reject a provider whose host is not
`api.gpt.ge` before the simulator starts. Never copy the key into trial output.

Navigation candidate visibility previously ignored robot links and carried
objects in its scene raytest. A target could be geometrically within the RGB
frustum but hidden by the robot's own arm at the selected endpoint. Candidate
validation now checks current visible robot/carry hulls transformed to each
candidate base pose before accepting a scene ray. This is an executor-private
visibility check on the model's selected point; it does not choose a different
object or modify the public RGB. A synthetic arm-occlusion regression covers
the rejected endpoint. Public navigation also keeps selected points out of
the lower 18% image border where the robot body is expected to appear; the
existing camera pose and 90-degree field of view are unchanged. The subsequent public-task model run and video review
must still validate the change in the simulator.

The first dedicated-key trash attempt (r5) confirmed that public navigation
used 38 own-link hulls and avoided treating the robot as transparent. It also
exposed an overconstraint: `grasp` automatically moves closer to its already
selected pixel, and the reaching arm may necessarily cross the camera ray.
That internal move was rejected as `navigation_unreachable` even though the
can remained visible in the current RGB. The own-silhouette check is now
limited to public `navigate_to` endpoints; the bounded in-action reach keeps
its existing scene visibility and contact checks. The r5 attempt is retained
as a failed model run, not scored as a successful demonstration.

In the next dedicated-key attempt (r6), the model's first `grasp` succeeded
and its public navigation while carrying the can succeeded with 39 own/carry
visual hulls participating in candidate validation. The resulting front RGB
still showed the trash can above the robot silhouette. These observations
validate this reach/navigation segment only. The model formally finished, all
call alignment and video checks passed, but the official three-can goal was
false after `place_inside` returned a sampling error. Its independent audit
therefore failed; the provider produced no 429 in this attempt.

## Demo camera clearance

The old four-camera mount had radius 0 m and a 20° downward pitch. Its
1.81 m height was explicitly calculated to put the robot's own visible body
below image row 0.8, **not** outside the image. This is why a fixed dark head
shape appeared along the front RGB's lower edge even while standing still.
Navigation cannot remove a mount-relative silhouette. A same-pose,
no-model OmniGibson probe at the r6 kitchen pose compared radius 0, 0.35 and
0.55 m: 0.35 m removed the head, but at 20° the nearby trash can was clipped
by the lower edge. Holding radius 0.35 m and varying pitch 20°, 30°, 35° and
40° showed that 35° keeps the whole can in view and leaves useful room context.
The back, left and right RGB views were also inspected at that configuration.
Comparison images are published on the first-ten internal review index. This
is a camera diagnostic, not a model trial or task-success claim.

Demo mode now uses four cameras at 0.35 m radius and 35° downward pitch. The
non-demo rig stays at its old 0 m / 20° mount. Capture and candidate visibility
share the same extrinsics; the selected RGB pixel remains the only action
target. The fresh `turning_on_radio` public instance 301 / seed 0 trial used
`gpt-6-astra` at `low` through the isolated experiment key. The model chose
its `navigate_to` and `toggle_on` actions and RGB points, formally finished,
and passed all seven demo audit checks including the independent whole-task
goal and continuous five-view video. Initial and final model-facing RGB have
no fixed head obstruction. During the short reach, the moving head/arm can
temporarily enter the frame; the arm retracts before the final observation.
This proves the camera fix on one complete public task, not all ten tasks.

The fourth trash attempt exposed a separate placement boundary: the official
`Inside.set_value` accepted the first can, but the can left the bin during the
action's subsequent physics settling ticks, so the postcondition rejected the
action. The placement transaction now snapshots the newly accepted pose
relative to the actual fillable link and holds that pose only during those
settling ticks. This extends the existing idealized protection for earlier
contents; it does not choose a placement before the official sampler accepts
one. A CPU regression covers an accepted can being ejected by settling, and
the full suite passes. The fourth model attempt was already running with the
older loaded code, so only a later independent model attempt can validate this
change in the public task.

The dedicated-key trash r7 trial formally finished, and its model calls,
video, and motion checks passed, but the official three-can goal remained
false. Its first `place_inside` selected the bin's fillable visual surface and
the official setter initially put the can `Inside`; the can left during the
following 50 environment settling steps. An exact scripted replay of the
first five recorded RGB selections reproduced the same failure. Boundary
records showed the can was included in the link-relative preservation list,
but the preservation callback was never invoked during `env.step`: that path
does not call the patched `sim.step_physics` used by the official sampler.
Placement now restores the accepted link-relative pose before and after each
settling `env.step`, as well as around sampler physics. The CPU regression
models this bypass and failed before the fix; the exact five-action simulator
replay changed from four successes plus failed `place_inside` to five
successes. This diagnostic does not establish a model-driven whole-task pass;
a fresh autonomous trial and official evaluator are still required.

The r7 Halloween trial moved several objects but ended before `finish` when
the configured provider returned a `v_api_biz_error` about a missing reasoning
item. The simulator was closed by the supervisor; the attempt is a failure.
The r7 plates/food trial formally finished but failed the official goal: its
first `grasp` pixel was on the breakfast table between the plates, so the
executor carried the table and six supported objects; the refrigerator's
official volume sampler then could not place that assembly. The generic
pick-and-place skill now tells the model to select an exposed item surface and
inspect the returned carried-assembly count before traveling. This remains a
model-selected action, not a scripted replacement pixel.

The r7 `can_meat` trial also formally finished without the official goal.
The first jar `grasp` point lay on its left lower visual edge. The old executor
reported a hidden cabinet fill-volume guide mesh; after porting the upstream
guide-purpose filter, a scripted replay of the same RGB selections still
resolved that boundary pixel to the cabinet's real base mesh. This narrows the
remaining issue to an ambiguous visible edge, not an invisible guide volume.
The model later selected another pixel and grasped a jar, but its subsequent
selected-surface placements failed sampling. The skill now asks for the middle
of an exposed jar face. Neither diagnostic replay nor skill edit is counted as
a whole-task success.

The fresh autonomous trash r8 trial used the corrected container-settling
code. The first can was placed successfully, but the remaining two official
`Inside` goals were false at formal finish. The model reported that the bin
had tipped during later navigation; subsequent placement returned
`unsupported_relation` and `sampling_error`. The dedicated-key controller,
video and visible-motion checks passed, while the official task check failed.
This is a new scene-stability problem rather than a pass for the three-can
task.

The later selected RGB ray hit the can being carried instead of the bin, and
the next successful bin ray was about 27 cm away from its first placement ray.
This supports the model's report that the movable bin shifted. The ideal
executor now holds the pose of a movable container after a verified
`place_inside` while subsequent environment steps run, releasing that hold if
the model explicitly grasps the container. This stabilization needs a fresh
autonomous trash trial and official evaluation before it can count as a fix.

The first Easter-egg demo trial r7 moved all three eggs out of the basket and
formally finished with a valid video, but the official evaluator rejected the
shared-tree `NextTo` conditions. Visual proximity on the lawn is insufficient.
The motor interface now exposes `place_next_to`: the model selects a current
RGB pixel on the intended tree or fixture; the executor searches nearby
supported floor poses and accepts only an official `NextTo` relation after
settling. The related `place_under` action uses the official `Under` sampler,
which addresses the mousetrap r7 finding: all four traps met `OnTop` for one
floor, but fewer than two met `Under` or `NextTo` for the same sink. Both new
actions preserve the model's choice of target and require independent
whole-task evaluation. Their later model trials are separate from these r7
diagnoses; no success is inferred from adding the actions.

In the full mousetrap r10 model trial, the model selected the same bathroom
sink twice and both `place_under` calls passed official `Under` checks. The
formal task evaluation still failed one of the four `OnTop` floor literals.
A privileged two-trap diagnostic reproduced the cause: the fallback sampler
placed both traps at the same XY point, stacking the second about 17 mm higher.
The first was `OnTop` the bathroom floor; the second was not. The fallback now
requires its ray to hit an actual floor link, skips poses overlapping another
movable item, and verifies official `OnTop` for the sampled floor after
settling. A separate diagnostic placed two traps at distinct points under the
same sink, with both `Under=true` and `OnTop` the same floor. This diagnostic
does not count as a model task success; a new Astra low trial must be audited.
