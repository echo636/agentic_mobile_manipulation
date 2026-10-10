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
frames the interaction during those steps. `MAS_DEMO_MOTION=0` is the default
and disables these extra reach, lift and stroke steps. It does not restore a
historical evaluation cohort: the updated camera rig, `place_under` and
`place_next_to` actions, and movable-container/payload stabilization also
apply to the standard execution path. After a successful inside placement,
the executor restores a movable container and its recorded contents during
later steps; explicitly grasping a container or item clears stabilization
references to its entire carried assembly, including nested contents. These are changes to ideal execution,
not only video presentation, and comparisons must record their source version.

Managed evaluations retain their single episode execution deadline during
volume sampling. The legacy 180-second/6000-physics-step sampler caps apply
only to unmanaged calls; deadline and closure requests still interrupt the
sampler at its physics-step boundary.

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
For these first ten placement demos, the audit also requires the model to
finish with `achieved` and a committed empty hand. The latter is derived from
the ordered successful grasp, placement and release effects; an object held
over a container can temporarily satisfy an `Inside` predicate without
completing a visible placement.

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

Both demo and normal mode use four cameras at 0.35 m radius and 35° downward
pitch; each camera moves outward along its own front/back/left/right direction.
Capture, candidate visibility, initial mount-height calculation and low-target
approach distance now share that pitch and radius. Mount height uses the robot's
initial visual vertices to keep its body below image row 0.8 with 5 cm of vertical
clearance; later moving-arm poses can still enter the frame. The low-target
distance accounts for the forward camera offset while retaining the existing
distance policy and final visibility checks. The selected RGB pixel remains
the only action target.

The height/distance alignment has CPU projection and GT regression coverage;
the following recorded trial predates that alignment and is not a new simulator
validation of it. The fresh `turning_on_radio` public instance 301 / seed 0 trial used
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

The first Easter-egg retry r8 showed that a model can select `place_next_to`
but accidentally mark lawn rather than the tree. Its sampler spent time
searching around the wrong parent and failed; a later tree pixel was beyond
the current navigable approach. The action now rejects lawn/floor as the
selected parent immediately, and the skill tells the model to navigate toward
visible ground near a tree before reselecting its body. The NextTo placement
sampler also requires an actual floor/lawn support ray, avoids overlap with
other movable items, and verifies official `OnTop` for that support after
settling. A privileged single-egg probe at the tree pixel from r8 produced
both `NextTo(tree)=true` and `OnTop(lawn)=true`. This is a geometric check,
not an autonomous model task pass.
The same privileged diagnostic repeated the placement for all three public
instance eggs, using one selected tree point. All three finished `NextTo` that
tree and `OnTop` the same lawn, at distinct supported poses. The agent still
needs to find the eggs and tree through RGB and pass a fresh whole-task trial.

The autonomous trash r9 trial used the stabilized bin and formally finished,
but the official three-can goal was 0/3. The model marked a point below the
visible bin for its second `place_inside`; that pixel hit a non-fillable
surface, and the model later released the can. It also described one can as
visibly inside while the final evaluator said otherwise. The executor now
preserves the accepted link-relative poses of contents in a movable filled
container across later control steps, and removes an item from that
preservation list if the model explicitly grasps it. The skill now describes
where to select a small bin and how to recover from `unsupported_relation`.
These changes require another autonomous whole-task audit; the r9 video is
not a pass.

The Halloween r8 trial formally finished with visible motion and a valid
review video. The model verified the cauldron next to the living-room table
and closed the TV cabinet, but two `place_inside` calls to a selected cabinet
failed the official volume sampler, so the evaluator rejected the five item
goals. During an idealized placement the robot hand remains at the opening;
the upstream sampler treats contact between that hand and the deposited item
as a collision. The next executor revision excludes only the robot itself
from the new item's initial collision veto, while retaining container and
other-object contacts. This is a bounded motor-level accommodation and still
requires official `Inside` verification. Its model trial has not yet passed.

A diagnostic replay of the r7 `can_meat` model pixels tested a wider search
around the selected countertop point. The first `place_on_top` still failed,
so that widening was reverted. The replay was scripted, and the task remains
unverified; any future claim needs a fresh model trial and official score.

In the mousetrap r11 autonomous trial, the model selected four successful
`place_under` calls and formally finished. The direct goal evaluation at
`finish` reported every literal true, and the final `run.json` records
`task_success=true`, all current goal literals true for some alternatives,
and the pinned official TaskMetric `q_score.final=1.0`. The old
`official_task_success` field still read `false` because it copied
`env.task.success`, a flag cached by the last `env.step`; ideal motor actions
also advance lower-level simulator physics after that step. The evaluator now
queries BEHAVIOR's own current `predicate` termination condition at final
scoring and separately records `last_env_step_task_success` so this mismatch
is visible. The r11 audit remains failed and immutable. A fresh model trial
must demonstrate that the corrected final official flag and audit pass.

The trash r12 model trial selected the real small bin after grasping a can,
but the upstream stochastic `Inside.set_value` exhausted its candidates and
the model eventually finished blocked. The motor now tries a bounded grid
inside the **model-selected bin's own fillable link** only after that setter
fails. It excludes overlapping movable objects and requires the original
`Inside.get_value` relation after the action's settling steps; the state
predicate and task evaluator are unchanged. A privileged simulator probe
forced the stochastic setter to return false and verified that the grid
fallback placed a can `Inside` the real bin. That probe used object handles
and is not a model task pass. The next full trash attempt remains necessary.

A second privileged grid probe on the public Halloween instance forced the
random setter to fail, then put `pillar_candle_89` and `pillar_candle_90`
inside the same bottom cabinet. Both were officially `Inside` after the
second action, including preservation of the first resident. The r12 model
episode had started before the grid fallback was written, so its placement
errors do not validate this new path. A fresh autonomous run is required.

The Halloween r12 model episode later placed two candles inside the cabinet,
verified the cauldron next to the coffee table, closed the cabinet, and
formally finished with a valid video. It left both pumpkins and one candle
outside, so the final official goal failed. This process had loaded the old
executor before the grid fallback was written. The full task remains pending
on a new model run with the committed motor revision.

The same privileged cabinet probe was expanded to all five target items:
three candles and two pumpkins. With the stochastic sampler forced to fail,
the verified grid placed all five inside the same selected bottom cabinet;
all five remained `Inside=true` after the last placement. This establishes
motor capacity for one cabinet in this instance, while the autonomous model
still has to identify and move every item through RGB.

The plates/food r12 model trial selected `pizza_89` itself for the first
grasp, then a refrigerator placement rolled back because the carried
assembly lost its original support relation. The carry log showed the
underlying plate was incorrectly classified as an `OnTop` child of the pizza;
the model later hit the provider's missing-reasoning-item protocol error, so
its formal finish was not recorded. The carry dependency check now requires
a child to lie above its parent and rejects an inverted support relation.
The pick-and-place skill explicitly says to grasp a visible plate rim when
food must remain on a plate. A privileged real-simulator diagnostic then
grasped `plate_93` with `pizza_89` as its payload and placed the assembly in
the fridge: `OnTop(pizza, plate)`, `Inside(pizza, fridge)` and
`Inside(plate, fridge)` were all true after settling. This proves the motor
path, not autonomous task success; two plate assemblies, two bowls and the
final closed fridge still need a fresh model trial.

The can-meat r12 model trial opened the cabinet but then tried to grasp a jar
on the upper shelf from an unreachable projected navigation target. Two
high-shelf approach attempts returned `navigation_unreachable`. The model
moved to a floor point in front of the cabinet but formally finished without
reselecting the jar from that new view, so the official goal failed. The
visual-manipulation and pick-and-place skills now explain that a shelf pixel
is not traversable ground: approach the visible floor outside its opening,
then retry the grasp on the item's own fresh RGB face. This guidance still
leaves the model to choose every point and action; a new whole-task run is
needed to assess it.

The fresh mousetrap r12 autonomous trial **passed**. `gpt-6-astra` at `low`
effort selected the four grasps and placements from RGB, including two
successful `place_under` actions on the same bathroom sink and two floor
placements. The final BEHAVIOR predicate check and independent whole-task
evaluator both report success; `scripts/audit_demo_trial.py` passed its checks,
including model/simulator call alignment, passed five-view video and
visible joint motion. The preserved r11 failed audit remains separate.

The Easter-egg r12 model episode selected a real tree after grasping an egg,
but two `place_next_to` attempts rolled back because official
`OnTop(lawn)` was false. A privileged replay at that **same selected tree
point** reproduced the failure. Diagnostics showed the lawn was below the
egg but `Touching(lawn)` was false: the old executor reset the egg to a pose
3 mm above the lawn after every physics step. Changing the offset alone did
not help. A contact-depth sweep at that point showed that free settling
produced `Touching`, `OnTop` and `NextTo` together. The placement now lets
the egg settle under physics before checking the original predicates. A
second privileged replay placed all three public-instance eggs next to the
same model-selected tree and on the same lawn. This is motor validation,
not a model whole-task pass; a fresh Astra low run is still required.
After expanding table-side search, `nextto_selected_tree_probe_r7` repeated
the three-egg replay with `MAS_DEMO_MOTION=1`; all three remained `NextTo`
the selected tree and `OnTop` the same lawn. The shared primitive's new search
has therefore passed this regression diagnostic, but not a new model episode.

The subsequent **Astra-low r13 model episode passed** the independent demo
audit for `hiding_Easter_eggs`: all three distinct eggs ended next to the
same tree on the lawn, official whole-task success was true, the model
reported achieved with an empty hand, and the five-view motion video passed.
The model moved one egg twice before correcting and placing the remaining
egg; the counted result is the final audited whole episode.

The toys r12 model trial successfully put five of six toys in the box. It
grasped the final board game but every `place_inside` attempt failed. The
model correctly finished `blocked`; its final held pose happened to satisfy
the container's geometric `Inside` predicate, so the official task flag and
Q score both read 1.0 despite the unfinished visible action. The revised
first-ten audit requires `agent_reported_achieved` and
`hand_empty_at_finish`, so this attempt remains a failed demo and its
original machine-audit file is preserved as historical evidence. A new
motor/capacity diagnosis and fresh model trial are required.

The capacity diagnosis is now verified in the real simulator. A greedy
fillable-grid search put the first four toys inside but exhausted all 85
collision-free candidates for the fifth, despite a feasible three-dimensional
packing of all six toy AABBs. The executor now searches a volume-relative
packing for the selected box's existing contents plus the carried object when
ordinary placement fails. It moves only those contents and verifies the
unmodified official `Inside` predicate for every object before committing.
The privileged `toybox_capacity_probe_r7` placed all six public-instance toys
in sequence, with all six still `Inside` at the end. This validates the motor
fallback only; the pending r13 Astra-low model episode must independently
select and complete the task, and pass `scripts/audit_demo_trial.py`.
The same six-object diagnostic passed again as `toybox_capacity_probe_r8`
with `MAS_DEMO_MOTION=1`; every successive official `Inside` count rose from
one to six. This checks the actual demo carry configuration, but is still
excluded from the autonomous model score.
The r13 Astra-low toy trial did not exercise the six-toy placement path:
after an initial puzzle grasp/release, the model repeatedly selected the
movable toy box as the carried object, failed to put it on a desk, and then
tried `place_inside` with the held object and target both being that box.
The official task and independent demo audit failed. The pick-and-place
skill now tells the model to keep a movable collection container as its
destination, and the motor immediately rejects self-containment. A new
autonomous trial is required.

The trash r13 Astra-low trial placed two cans successfully. Its third
`place_inside` returned `unsupported_relation` because the chosen RGB pixel
did not resolve to a fillable container. The model then reported `achieved`
with that can still carried; the official task check and the stricter demo
audit both failed. The visual-manipulation skill now instructs the model to
count only successful placement tool results and keep a failed item pending.
This prompt change requires a new model trial; it does not retroactively
change the r13 result.
The tool session also rejects `finish(achieved)` while controlled carry still
holds an object, leaving the episode open for a corrective action. This is a
generic completion protocol check, not task-goal oracle feedback; official
BDDL evaluation remains independent. It likewise requires a fresh trial.

For the kitchen-furniture r12 close failure, a separate privileged motor
replay (`cabinet_close_probe_r2`) opened the same public-instance cabinet,
placed the toaster, food processor and French press inside it, then closed it.
All four operations succeeded, `Open` became false, and all three `Inside`
predicates remained true. This narrows the r12 failure to the particular
model-run scene/placements or its intervening actions; it does not establish
that closing a loaded cabinet is generally broken.

Halloween r13 put all five required pumpkins/candles inside the selected
living-room cabinet, but two `close` attempts failed after the drawer joints
reopened during settling. Its cauldron `place_next_to` also failed: a near-table
candidate remained 4 cm above the floor after settling, so official
`OnTop(floor)` was false. The episode finished blocked, and the independent
audit failed. The motor now holds a fully closed articulated joint target
across later control steps and excludes target-overlapping floor candidates
while searching farther around large furniture. In the privileged
`drawer_close_probe_r1`, five public-instance items stayed `Inside` after a
successful `close` and another 120 physics steps with `Open=false`. The
`cauldron_table_probe_r4` used the same r13 model-selected table point and
verified official `NextTo(table)` and `OnTop(floor)` after placement. These
are motor-only results; a fresh autonomous trial is still required before
counting task success.

Plates-and-food r13 selected `plate_93` with `pizza_89` as a carried payload,
but the plate rotated nearly vertical during navigation. The resulting
`place_inside` attempts rolled back, and the model finished blocked. The
motor now preserves the grasp-time world orientation for a carried assembly
with contents while the visible hand translates it. In privileged
`plate_fridge_navigation_probe_r2` with `MAS_DEMO_MOTION=1`, the plate stayed
approximately 2.4 cm high through a 2.9 m navigation path; the final pizza
remained `OnTop(plate)` and both objects were `Inside(fridge)`. This is motor
validation, not an r13 model pass; a new Astra-low trial is required.

Can-meat r13 grasped the high-shelf `hinged_jar_236`, but three model-selected
`place_on_top` attempts on visible counters failed with `sampling_error`; the
model finished blocked while still carrying the jar. In the public-instance
`jar_counter_probe_r3` with demo motion enabled, the strict cuboid sampler was
deliberately made to reject its candidates. The new local surface fallback
placed the jar at the model's first selected bar point, and the official
`OnTop(bar)` predicate was true after settling. This validates the fallback
branch, not the complete can-meat task; a new model run is required.

Kitchen-furniture r13 passed the independent audit: Astra low autonomously
put the toaster, food processor, and French press in one upper cabinet,
closed it, reported achieved with an empty hand, and the official BDDL
whole-task and five-view video checks passed. This brings the independently
audited first-ten successes to tasks 1, 6, 7, and 9. In a separate motor-only
diagnostic, `cabinet_close_repair_probe_r1` deliberately displaced one of
three contained objects after closing; the closed-volume reseating fallback
restored official `Inside` for all three. The fallback has not been needed to
claim the r13 model success.

Christmas-decorations r13 correctly grasped the wreath and selected a sofa,
but three `place_on_top` attempts rolled back after the wreath slid 1–3 m
away during settling. The episode finished blocked, with official whole-task
failure. In the privileged public-instance `wreath_sofa_probe_r5`, the motor
held the model's first selected sofa point through settling, lowered the wreath
by the measured support gap, and verified official `OnTop(sofa)=true` with
demo motion enabled. This motor diagnosis does not count as a model success;
a fresh autonomous trial remains required.

Halloween r14 improved the cauldron placement and completed all five item
placements into the same cabinet at least once, but its final official BDDL
goal option had two false `Inside` clauses; the whole-task audit failed and the
model finished blocked. The fixed cabinet now retains each verified item's
link-relative pose across later navigation and opening/closing, excluding an
item when the model grasps it again. In the privileged
`drawer_close_probe_r2`, five items remained officially `Inside` after close,
120 steps, reopen, reclose, and another 300 steps. This is motor validation;
the task still needs a fresh autonomous model run.

Trash r14 exited after one grasp because the external model provider rejected
a continuation with a missing linked reasoning item. Its controller and audit
failed; this is an infrastructure failure, not a successful or failed task
execution. A new Astra-low episode must be run.

Plates-and-food r14 likewise ended in a provider continuation error (`No tool
output found for function call`) after a successful placement; its official
whole-task check and audit failed. It needs a fresh autonomous episode. In
can-meat r14, several planned navigation candidates were reachable in the
static grid but the executor returned `navigation_unreachable` during path
execution. Future trials now record the private follower failure reason and
actual final XY in `navigation_failures.jsonl`, so a targeted motor repair can
be based on the precise failure rather than the sanitized model-facing error.

Toys r14 correctly selected four distinct toys and put them in the box, then
failed on `board_game_228`: the visible hand carry had rotated the thin board
upright, producing a 20 cm high bounding box that could not fit in the
fillable volume. Demo carry now preserves the grasped object's original world
orientation through hand motion. In the privileged
`toybox_model_order_probe_r1`, the exact same first-five item order plus the
remaining tennis ball all placed successfully; the fifth used verified
repacking and all six final official `Inside` checks were true. This validates
capacity at the preserved orientation only. The model trial still needs its own
successful final audit.

Toys r14 later grasped the final tennis ball, but the provider rejected the
continuation with a missing linked reasoning item. Its model controller,
official whole-task result, and independent audit failed. Christmas r14
finished blocked after an attempted grasp hit the fixed basket; no decoration
was removed. Its RGB sequence showed a small floor basket from a distant
viewpoint. The pick-and-place skill now tells the model to navigate to the
basket body/rim, inspect a larger fresh image from multiple sides, and select
an exposed item rather than a floor point or the basket wicker. Both tasks
require new autonomous trials; neither diagnostic nor prompt change counts
as success.

Trash r15 passed the independent audit. Astra low autonomously grasped all
three distinct cans and completed three `place_inside` actions into the
kitchen trash can, then reported achieved with an empty hand. Official BDDL
whole-task success, model/simulator trace alignment, visible motion, and the
five-view video review all passed. The first-ten audited successes are now
tasks 1, 2, 6, 7, and 9.

Halloween r15 became unusable during its second `place_inside`: the model tool
call timed out while the simulator continued a high-load official randomized
volume sample. The old trial was terminated and recorded as a scheduler
failure, not as an autonomous task result. Demo-mode volume sampling now stops
after 1200 physics ticks or 90 seconds and tries the already verified
fillable-grid/repack placement instead, under the same overall episode
deadline. A unit test covers that fallback, and the privileged
`drawer_close_probe_r3` still placed all five items, closed, reopened,
reclosed, and retained official `Inside` after 300 more steps. A fresh model
episode is required to validate the task.

The remaining r15 attempts (tasks 4, 5, 8, 10) failed during simulator
startup when USD could not write under `/tmp`: this user's root-filesystem
quota was at its 20 GB limit, although the experiment data volume had ample
space. Three unreferenced September OmniGibson temporary directories (1.7 GB)
were moved, without deleting them, to
`experiments/stale_tmp_archive_20261010`; no active process held them open.
The demo runner now sets `TMPDIR` to a per-GPU directory on the experiment
volume before starting the simulator and model. These r15 startup failures
are infrastructure outcomes and need new autonomous trials.

Halloween r16 initialized correctly on the data-volume scratch path, but the
model selected deep/far points on the same bottom cabinet for
`place_inside`. The arm remained 0.5–1 m from those pixels after multiple
successful navigation actions, so the motor rejected each placement as
`out_of_reach`; the model released its item and finished blocked. Its official
whole-task and independent audit failed. The pick-and-place skill now directs
the model to select the near-side drawer face or opening lip and change
viewpoint after this error. This needs a fresh model trial.

Plates-and-food r16 formally finished but failed the independent audit's
official BDDL and agent-achieved checks. Its first grasp selected the
breakfast table instead of a thin plate at the edge of the right RGB frame;
the executor carried the table with both plated pizzas and both bowls.
After release the table tipped and the pizzas separated from their plates.
The model recovered one bowl into a sink and closed the refrigerator, then
honestly finished blocked. The pick-and-place skill now asks the model to
center a thin plate in view and select an exposed rim pixel before grasping.
This is a model-facing instruction only; a fresh independent model trial is
still needed to validate task success.

Can-meat r16 also finished blocked and failed the official BDDL audit. The
model first opened an oven, recognized the mistake, and closed it. It then
selected a wall cabinet over the counter, but the visible hand stopped about
0.55 m short after the automatic approach. Its next selected floor point
beside the counter had no navigable bounded projection, so the model ended
without removing a jar. The earlier r13 trial reached a cabinet door from
an aisle-side base pose and did grasp a hinged jar; therefore this r16 failure
does not establish an impossible motor task. The pick-and-place skill now
explains the counter obstruction and asks for an aisle-side or end-around
approach before retrying the cabinet. A new model trial must verify it.

Toys r16 formally finished blocked and failed its independent BDDL audit.
Astra low autonomously placed `board_game_230`, `tennis_ball_225`,
`jigsaw_puzzle_227`, `board_game_229`, and `board_game_228` into the same toy
box. The fifth thin board placed successfully with preserved carry orientation.
The model then grasped the remaining `jigsaw_puzzle_226`, but all three
`place_inside` attempts failed with `sampling_error`; the last item remained
held at finish. The private grid log recorded 75 occupied-volume overlaps
and 10 outside-volume candidates on the first attempt. The packing motor now
tries several bounded resident orderings and records dimensions if they all
fail. This code path has passed unit tests. The privileged
`toybox_r16_order_probe_r4` then put all six same objects inside in the exact
r16 order with the official `Inside` predicate true after each action; it
forced the verified fallback for each placement and did not replay the
model's intermediate travel or original stochastic placements. This proves
capacity for that order but does not reproduce or fix the r16 crowding state.
A fresh autonomous model run and audit are still required.

The comparison `toybox_r16_official_probe_r5` reproduced the final-item
failure with the same six-item order when the official stochastic volume
sampler was allowed to place earlier items. The fifth thin `board_game_228`
had a 10.1 cm final vertical extent, versus 3.5 cm in the earlier flat
capacity probe, leaving no verified room for the final puzzle. The motor now
tries a flat, official-`Inside`-verified grid pose before stochastic sampling
for thin demo-carried items without payloads. This is a general
container-packing rule based on observed object extent, not a task-specific
pixel or object selection. In the matching `MAS_DEMO_MOTION=1` simulator
probe `toybox_r16_official_probe_r7`, all six objects in the r16 order were
officially `Inside` after each placement; flat items used the new verified
grid path where applicable. This validates the motor fix under the public
instance, while an autonomous model run and audit remain necessary.

Christmas r16 independently failed the official BDDL goal. Astra low
recovered from an initial whole-basket grasp, selected the wreath itself,
and placed it on the living-room sofa. It later grasped a candle but first
selected a distant window for `place_on_top`, then a table point without a
collision-free placement; it released that candle. After grasping a gift box,
it repeated two nearly identical `place_next_to` selections on a tree seen
across furniture, both rejected as `navigation_unreachable`, and finished
blocked with the gift still held. The pick-and-place skill now describes a
clear tabletop viewpoint and a distinct free-floor approach to the tree.
These prompt changes need a fresh model trial and independent audit.

Halloween r17 passed the independent audit in full. Astra low autonomously
selected and placed three candles and two pumpkins inside the same living-room
TV cabinet; the executor verified 0, 1, 2, 3, then 4 earlier residents before
successive placements. The model closed the cabinet, grasped the cauldron,
placed it next to the living-room table, and formally reported achieved with
an empty hand. Official whole-task BDDL, controller/action alignment,
five-view video, and visible-motion checks all passed. This is the sixth
independently audited first-ten task success.

Plates-and-food r17 failed the official whole-task audit. The model opened
the refrigerator, then selected normalized pixel `(0.17, 0.55)` in the back
RGB to `grasp` what it called a plate. The saved image shows that point in
the orange pizza center, inside the surrounding white ceramic rim; the
private carry record confirms `pizza_89` was grasped without `plate_93`.
A subsequent `place_on_top` attempt failed, the model released the pizza,
and a recovery navigation target was unreachable before it finished blocked.
The pick-and-place skill now explicitly distinguishes the white plate band
from the colored pizza and asks the model to check the marked pixel itself.
The resulting change needs a fresh autonomous episode and audit.

Can-meat r17 also failed its independent official BDDL audit, but the model
advanced beyond the r16 cabinet obstacle: it opened the intended upper
cabinet, grasped `hinged_jar_236`, and placed that jar on a bar. It then
grasped `hinged_jar_235`. Three model-selected staging surfaces for the
second jar (a stool, a countertop, and floor) all returned `sampling_error`
for insufficient collision-free support; the model finished blocked while
still holding that jar. The pick-and-place skill now explains that two bulky
jars need separate broad clear patches and that `placement_yaw_degrees` can
align a long jar with available surface length. No sausage was packed in
this trial, so the complete task still needs a fresh autonomous episode.

Toys r17 did not exercise the corrected packing motor: after grasping
`jigsaw_puzzle_226`, the model lost the toy box from its camera view and
selected the desk, wall, then floor for `place_inside`. All three were
correctly rejected as `unsupported_relation`; it finished blocked with the
puzzle in hand and failed the official audit. The saved r16 RGB shows the
open gray rectangular toy tub centered on the desk below athlete posters.
The pick-and-place skill now tells the model to turn or step back to recover
that visible tub rather than infer that the box moved when the camera turned.
No hidden object ID or scripted action was supplied to the controller.

Christmas r17 failed the official audit while still holding the wreath.
The model initially grasped `wreath_227` with `candy_cane_226` resting on it.
Its first `place_on_top` selected the coffee table rather than the sofa; the
wreath reached that table, but the cane lost its original support relation,
so the motor rolled back. Later selections hit two sofa regions: a shallow
seat placement again lost the cane relation, and a sloped sofa patch did not
meet stable support despite being the correct object. The model then sampled
other unsuitable surfaces and finished blocked. The motor now restores a
carried payload after each settling physics step and allows a verified
official `OnTop` result on the exact selected surface even where the local
mesh normal is sloped. The skill now directs the model to a broad seat cushion.
These changes require a fresh simulator probe and autonomous task audit.

In privileged `wreath_payload_r17_probe_r2`, the r17 model-selected flat
sofa point produced official `OnTop(wreath, sofa)=true` with the updated
motor. That probe did not carry a candy cane: the initial scene's noisy
`OnTop(cane, wreath)` report lacked physical `Touching`, so the carry closure
excluded it. A second probe attempted the official state setter for the
cane, but it returned false and still did not create contact. Thus the
payload-preservation change remains unverified in a faithful real-simulator
replay; neither probe counts as an autonomous Christmas success.

Plates-and-food r18 again failed its independent whole-task audit, but the
first RGB grasp selected the white rim of `plate_93` correctly. Its private
carry log had no payload, and the subsequent RGB showed `pizza_89` on the
floor. A real-simulator replay of the exact preceding model-selected `open`
and `navigate_to` actions (`plate_r18_prefix_probe_r1`) found official
`OnTop(pizza_89, plate_93)=true`, `Touching=true`, and a valid payload
candidate at initialization, after opening, and after navigation (826
environment steps). Thus the support was lost during the visible arm reach
that follows pixel grounding, before the ideal grasp captured dependencies.
The executor now snapshots supported children before that reach, restores
their relative poses after each visible arm step, and uses the snapshot for
the ensuing carry. In `plate_r18_grasp_probe_r2`, a privileged replay of the
same three recorded RGB actions carried `pizza_89` with `plate_93`; both
official `OnTop` and physical `Touching` remained true after grasp. This
validates the motor fix only; an autonomous model episode and full BDDL audit
are still required.

Can-meat r18 formally finished blocked and failed its independent whole-task
audit. The model opened upper cabinet sections but could not keep the jar
shelf in a usable camera view. It then searched lower cabinets, selected a
fixed object for grasp, and stopped without filling either jar. During that
search the chopping board was disturbed and some bratwursts fell to the
floor. The pick-and-place skill now describes backing into the clear aisle
to reframe the full high shelf before switching to lower cabinets or crossing
the preparation island. This model-facing guidance needs a new episode.

Toys r18 passed the independent demo audit. Astra low independently selected
RGB-grounded grasps and placements for three board games, two jigsaw puzzles,
and a tennis ball, placing all six in the same toy box. Its `finish` evaluation
reported official whole-task success with all six BDDL goal predicates true.
`08_picking_up_toys_r18/demo_audit.json` passed all nine checks, including
model/simulator call alignment, empty hand, visible motion, and five-view video.
The sixth placement used the verified fillable-volume repack fallback after
the first grid and official sampler could not find a stable arrangement.
This is the seventh independently audited success among the first ten tasks.

Christmas r18 failed the independent audit after the model finished
`aborted`. It initially grasped the wicker basket with its contents, released
it, then selected `wreath_227` and successfully placed the wreath on a sofa.
It placed `gift_box_221` next to the Christmas tree and `gift_box_219` under
the tree after model-selected navigation recovery. The remaining gifts,
candy canes, and candles were not completed; a later grasp selected the floor
and returned `fixed_object`. The official final evaluation had at most one
of nine predicates true in any goal option (Q score `1/9`), and the nine-check
demo audit failed on whole-task success and formal controller completion.
These successful local executor effects are not a task pass. The saved RGB
also showed that close tree branches obscured its trunk when a second gift
placement returned `navigation_unreachable`; the pick-and-place skill now
directs a side view and lower trunk selection. A fresh model episode is needed.

Plates-and-food r19 passed the independent demo audit. Astra low selected
`plate_93` and `plate_94` from RGB; each visible grasp carried one supported
pizza. The model placed both plate assemblies in the same refrigerator,
grasped `bowl_92` and `bowl_91`, placed both in the same sink, and closed the
refrigerator. The formal `finish` reported achieved, official whole-task
success, and all seven goal predicates true. The nine audit checks passed,
including model/simulator alignment, empty hand, visible motion, and
five-view video (`04_cleaning_up_plates_and_food_r19/demo_audit.json`).
This is the eighth independently audited success among the first ten tasks.

Can-meat r19 failed the whole-task audit. The model grasped `hinged_jar_235`
and staged it on the kitchen counter, but no bratwursts were placed inside
either jar; it finished blocked, with the official final goal fraction `2/9`.
The decisive RGB `grasp` attempt at normalized `(0.54, 0.727)` looked close
to a crescent-shaped cooked bratwurst. Magnifying the saved `rgb-00013-front`
shows that raster pixel `(276, 371)` is in the empty inner curve, on the bar.
Private grounding selected `bar_udatjt_0`, and the executor correctly returned
`fixed_object`. The model did not retry on a solid orange part of the sausage.
The pick-and-place and failure-recovery skills now give that exact visual
selection and recovery rule. This changes model guidance only; a new
autonomous trial is required to establish whole-task success.

Christmas r19 also failed the independent whole-task audit. The model first
grasped the wicker basket instead of its wreath and released it, scattering
the contents. Two later model-selected `grasp` points hit fixed floor rather
than movable decorations. The last saved left RGB shows the circular wreath
and a point near its empty center; magnification makes the hole visible.
The model finished blocked with no requested placement completed, and its
official goal fraction was zero. The visual selection rule now names wreaths
as well as curved food: click a solid colored segment rather than the center
of the enclosing rectangle or ring. This is guidance for the next model
episode, not a passed Christmas demonstration.

Can-meat r20 failed the independent whole-task audit despite a better start.
The model opened `top_cabinet_lkxmne_2` and `_0`, then recovered from one
`out_of_reach` grasp to pick up `hinged_jar_236`. It next called `open` while
still holding that jar; the executor returned `hand_occupied`. The model
finished blocked without staging the jar, leaving the hand occupied. Official
goal satisfaction was `2/9`, and the empty-hand audit check also failed.
The pick-and-place skill now states the staged-jar sequence explicitly and
the recovery skill treats `hand_occupied` on lid actions as a cue to place the
held item safely before retrying. A fresh model episode is needed.

Christmas r20 made substantial autonomous partial progress but failed its
independent audit. Astra low grasped `candy_cane_226` and placed it on
`sofa_lugrhk_1`, grasped `wreath_227` and placed it on `sofa_lugrhk_0`, then
placed all three distinct gift boxes next to the Christmas tree. The two
sofa supports are different simulator objects, so those two placements alone
cannot satisfy the shared-sofa requirement; the model-facing skill now asks
it to retain the exact first couch as a visual landmark. While returning for
the remaining canes and candles, the provider stream disconnected and its
retry returned a `custom_tool_call`/missing `reasoning` item error. This was
not a 429 rate limit. The controller failed without a formal `finish`; the
whole-task and controller checks were false even though the recorded motion
and five-view video passed. A new autonomous episode is required.

Can-meat r21 opened `top_cabinet_lkxmne_0` but finished blocked before
grasping either jar. Its final front RGB was close to the stovetop: the red
backsplash and cabinet lower edge filled the upper view while the high shelf
was cropped. Back, left, and right RGB also lacked a complete jar view. The
model reported that it could not reliably identify or reach the jars and
finished blocked; official whole-task success was false (goal fraction
`5/9`). The pick-and-place skill now requires a free-floor aisle viewpoint
and turn that bring the full upper shelf and jar bodies into RGB before
attempting a grasp or abandoning that search.
