# Reliable visible demonstrations

This is the acceptance and iteration record for the first ten public tasks.
The brain chooses every `act` primitive and RGB pixel. Executor-only geometry
may make the selected action idealized, but task bindings, evaluator predicates,
object IDs, and hand-picked pixels never enter the model controller. Model runs
use `gpt-6-astra` at `low` effort, public instance 301 and seed 0 unless a run
explicitly says otherwise. A scripted simulator probe is diagnosis, not a model
success. `docs/demo_motion.md` explains the motor and the full chronological log.

## Two independent gates

1. **Task success:** the model formally finishes, its recorded calls align with
   the simulator, the official whole-task evaluator succeeds, the hand is empty,
   and the continuous five-view video passes structural validation. The
   `scripts/audit_demo_trial.py` result is the machine evidence.
2. **Presentation quality:** watch the `scripts/build_review.py` five-view page
   from beginning to end. Record a timestamp and a concrete cause for any
   target hidden by the robot, camera pointed into a wall, object jumping away
   from the visible hand, arm passing through scenery, awkward repeated
   navigation, object spill, or unstable final pose. A passed video file or
   visible-motion count does not establish this gate. Keep a clip only after a
   human reviewer accepts its continuous action, framing, and final state.

The default experiment visualization is
`http://10.130.140.115:8769/demo_motion_first10_index.html`; it must retain
every failed attempt beside the passing ones. A demo can be shown as an
individual successful run after both gates, but **high repeatability** is a
different claim. After a change, obtain three consecutive autonomous passes on
one fixed source/configuration before treating it as a candidate. Then measure
at least ten independent runs of that same configuration and report the
observed pass fraction; the target is at least 9/10 with every passing clip
meeting the presentation gate. These are empirical thresholds, not a guarantee
of future success. Reset the cohort when code, skills, camera, model, effort,
task instance, or seed changes. Preserve earlier runs as history, never merge
their numerator into the new cohort. Start with three runs and stop immediately
on a repeated failure signature; this avoids spending ten full episodes on a
known unresolved defect.

Run `python3 scripts/demo_reliability_report.py <first-ten-trial-root>` to see
audited task successes and the pass count for each task's latest exact
source/skill/camera/model/instance/seed fingerprint. `--json` also reports wall
time, video time, action error codes, motion segments, and a false time-limit
claim. The report reads immutable experiment files; it does not rescore tasks
or grade aesthetics. The source commit and review page remain in each run.

## Failure-driven iteration

Each full task run must answer one question. Before launch, write the specific
failure signature, the proposed change, and the observation that would refute
it. First run a short replay, CPU test, or clearly labeled simulator probe that
goes red on that signature. Change one mechanism, rerun that check, then launch
one autonomous task trial. Audit with the official evaluator and build review.
Record whether the predicted failure disappeared, whether a new failure
appeared, and whether the video improved. If the same signature returns, stop
full-task retries until a different causal explanation and test exist. A
prompt-only or random-view retry is an experiment on model variance, not a
verified executor repair.

The controller must not infer that the wall-clock budget expired from a long
conversation. Managed tool replies now expose `time_remaining_seconds`. In
demo mode, a `finish(blocked)` reason that explicitly claims time exhaustion
while more than ten minutes remain is rejected without closing the episode.
This guards a false clock claim; it does not force the model to solve a truly
blocked scene or reveal evaluator state.

## Status after r34 (2026-10-11, Asia/Shanghai)

Nine of ten tasks have at least one independently audited success. The passing
attempts are radio r1/r2, trash r15, Halloween r17, plates/food r19, can-meat
r30, mousetraps r12, Easter eggs r13, toys r18, and furniture r13. The
Christmas-decorations task has no passing attempt. Each task's latest exact
configuration has only one passing trial; none meets the repeatability gate.
The continuous clips have not all received a full presentation-quality review.

Can-meat r29 exposed a specific executor failure: after the model correctly
filled two jars, contained sausages lost their relation while a jar was
settling back into the cabinet. The executor now realigns carried contents
after each placement physics step. On the revised source, the autonomous
`gpt-6-astra`/`low` r30 passed all independent checks, with 68 recorded motion
segments. Its model run took about 75 wall-clock minutes; the generated
five-view video is about 311 seconds. The run still contains nine failed
`fixed_object` actions and other recoveries, so task success is not evidence
of efficient or polished motion. Review its whole clip before promoting it.

Christmas r30 failed after 18 minutes, despite a 120-minute budget. The model
selected the loaded wicker basket on its first grasp (six rigid contents),
released it, placed the wreath and one candy cane on a sofa, then finished
`blocked` while claiming there was no execution window. The private grounding
trace and `rgb-00002-front` show the first selected pixel belonged to the
basket, not an exposed item. A CPU regression reproduced payload scattering
after `release`; the executor now carries the captured payload through each
release-settling physics step and verifies the preserved relation. A second
regression reproduced the false time-limit closure and now passes. A separate
privileged real-simulator probe, `scripts/probe_release_payload.py`, grasped
the same loaded basket and released it over 36 simulation steps: all three
candy canes, both candles, and the wreath remained `Inside` afterward. This
probe is executor evidence only, not an autonomous task success. No autonomous
post-fix Christmas run or full video-quality approval exists yet. The model
skill now requires a visibly isolated item
pixel before grasping from a mixed basket; this is guidance, not deterministic
grounding.

Christmas r31 validated the release repair in an autonomous model run:
`release` reported six preserved payloads, and the five-view video passed its
structural audit. The task still failed. The first grasp selected the loaded
basket; after release, a pixel on the basket's open wicker resolved through
the gaps to the breakfast table, which was also grasped. Its `blocked` finish
did not claim a false time limit, so the clock-specific guard correctly did
not intervene. A new default grasp preflight rejects loaded supports and
furniture-size objects before hand motion, while allowing an explicit
`confirm_bulk_grasp=true` when carrying the whole selected object is intended.
The r31 visual and private grounding trace define the regression; CPU tests
cover the guard. The next autonomous run will test whether the model uses the
feedback to reselect visible individual decorations. A can-meat r30 video
inspection also found 59 spectator-camera position jumps over 1 m within
single actions; its task and structural video pass do not imply that its
presentation gate has passed.

Christmas r32 selected and placed one individual candy cane correctly, then
hit a repeatable motor failure while placing a candle on the selected dining
table. Its first postcondition trace showed a 7.23 cm bottom gap over the
correct table despite 50 settling steps; the pose-hold loop kept the sampled
clearance from closing. The model tried another table pixel and eventually
finished `blocked`. The official evaluator failed; formal finish, call
alignment and video structure passed. A guarded pre-settle seating correction
has been added for the exact selected support. The next validation is a short
real-simulator component probe using the archived model-selected world point,
followed by one autonomous task trial. This does not retroactively pass r32.
The first natural component probe used the initial scene's upright candle and
found only a 1.32 cm gap, so it did not trigger the repair. The probe records
this as an unexercised correction and will be repeated with an explicit
sampler-height fault injection matching r32's measured gap.
That injected real-simulator probe passed: 8.00 cm initial bottom gap,
7.70 cm controlled lowering, near-zero final gap and `OnTop` true on the same
table. It used a private object handle and injected clearance, so its status
is executor-component validation only. The next autonomous trial remains the
task-level test.

Christmas r33 was a model-controlled partial run, stopped by the supervisor
after the dining table and chairs were visibly displaced; it is not a model
finish or a passing task attempt. The model selected an individual candy cane,
but the first table placement failed: RGB depth selected the table at 0.798 m,
while the post-settling support ray reported the table 0.0594 m higher.
The placed cane had a 0.0022 m support gap, so the unmodified `OnTop` predicate
was false. Subsequent table attempts also failed. Review captures 9–12 show
the table still flat at capture 9, chairs displaced by capture 10, and the
table leaning against a window by capture 12. The old navigation log used only
a static eroded grid and did not include movable furniture.

Short, explicitly non-model component probes isolated two motor hazards before
another full task run. A static-grid overlay of current movable floor-furniture
boxes at 0.48 m clearance left no visible goal for the archived table pixel;
0.38 m preserved an approach. A bare-base probe reached that point and the
subsequent basket approach without toppling any chair. Reproducing the archived
held-item posture made an unprotected retract/navigation move the table 0.75 m
and several chairs more than 1 m. Anchoring only the table while retracting
still ejected one adjacent chair by about 1.2 m. The revised executor lifts the
held item, briefly stabilizes the selected furniture cluster during the fixed-
base retreat, and then drives around current furniture bounds. The same held-
item probe reached the selected table point; the table moved 0.023 m and
rotated 0.85°, with no chair exceeding the 0.03 m / 2° stability threshold.
These are motor-component checks, not evidence of task success or polished
video. The records are outside Git under `experiments/demo_navigation_furniture_probe_20261011_r5`
through `_r11`.

The first simulator placement probe, with the robot still far from the table,
passed official `OnTop` and saw only a 0.0144 m visual/support height gap. A
second probe staged the cane in the visible right hand at r33's archived base
pose, reached the exact model-selected table pixel, and reproduced the failed
placement. Before transaction rollback, the table had moved 1.249 m and
rotated 35.5°; several chairs had moved 0.09–1.34 m. The support ray then missed
the table entirely. This refutes a fixed visual/collision mesh offset as the
primary cause, so the original 0.05 m selected-shelf tolerance remains.

The placement motor now stabilizes the selected support and adjacent floor
furniture through sampling, 50 settling steps, and the following visible arm
retreat. On the identical near-table probe, official `OnTop` passed; after
retreat and ten unanchored physics steps, no tracked furniture moved more than
0.005 m or rotated more than 0.1°. This is component evidence, not a model
task pass. The records are `experiments/demo_selected_table_placement_probe_20261011_r2`
(red) and `_r3` (green). Spectator filming also keeps one usable camera side
within each tool action, switching only if its clearance collapses; a full
model trial is needed to judge the resulting video.

Christmas r34 was an autonomous `gpt-6-astra`/`low` partial run stopped by the
supervisor after 23 model-selected actions. Its official whole-task success was
false and only 1/9 expanded goals were satisfied; the continuous video passed
structural review, but presentation quality is not approved. Furniture stayed
stable. The model visibly placed a candy cane on the dining table, the first
pillar candle on that table, and a wreath on a living-room sofa. These visible
placements are not counted as task success. It twice tried to place the first
candle on a sofa before correcting to the table. After a later grasp resolved
to `pillar_candle_223`, the model's own reasoning said it would place a *cane*
on the sofa and it put that second candle on a sofa. This repeated identity
mismatch made continuing the 120-minute episode low value. The supervisor
closed it as `aborted`; the model did not formally finish. Its failed audit and
five-view review remain at
`http://10.130.140.115:8769/demo_motion_first10_20261009/10_putting_up_Christmas_decorations_inside_r34/review_5view.html`.

The selected pixel still comes from the model's current RGB, but a successful
`grasp` now reports only the selected object's simulator category in
`effect.grasped_category`. No instance ID, pose, task binding, goal-state list,
or evaluator result is exposed. The model prompt and pick-and-place skill tell
the model to compare this bounded execution feedback with the task instruction
before choosing a destination. This is simulator-derived feedback, so new
trials must not be described as pure RGB-only perception. It is not a scripted
action or a task-answer oracle. The response allowlist has CPU regression
coverage; the next autonomous run must verify that the model actually uses it.

r34 also exposed a separate motor defect. Its cane postcheck reported a
0.0388 m gap above the selected table surface and official `OnTop=false`, yet
accepted geometric support. A short real-simulator probe staged the same cane
at the archived base pose and selected table point. In the traced red run,
`OnTop` was momentarily true at the end of placement but false on the first
arm-retreat step without item or table pose movement; the actual ray-measured
gap was 0.0439 m. The table moved less than 0.01 mm. The old code skipped
seating whenever that transient `OnTop` was true. The motor now seats a released,
settled item by its measured gap only when a ray hits the exact selected
support at the correct shelf height, then runs ten controlled physics steps.
The same cane probe finished with official `OnTop=true` before retreat and
after ten free steps; the gap was about -0.001 m, and furniture remained stable.
A second private component probe placed the wreath on r34's selected sofa:
official `OnTop` was true before retreat and after ten free steps. The red/green
records are outside Git under `experiments/demo_cane_contact_probe_20261011_r2`
through `_r5` and `experiments/demo_wreath_contact_probe_20261011_r1`.
Neither probe is a model-task pass. The next full trial is r35 on a new exact
source/skill/model configuration; its task and presentation gates remain open.

Christmas r35 was another autonomous `gpt-6-astra`/`low` attempt on public
instance 301 / seed 0. The model used the new successful-grasp category
feedback correctly for a pillar candle and candy canes. Dining-table cane
placement, dining-table candle placement, and one sofa cane placement retained
official `OnTop` after the actions. The supervisor still stopped the episode
because taking a second cane from the wicker basket overturned it and visibly
spilled items, while a later sofa placement folded the robot's body down onto
the cushion. The official whole-task result is **false** (3/9 expanded goals);
there was no formal model finish or presentation approval. The continuous
video passed structural validation only. Preserve its five-view failure page:
`http://10.130.140.115:8769/demo_motion_first10_20261009/10_putting_up_Christmas_decorations_inside_r35/review_5view.html`.

The archived second-cane attempt first reached from a distant base pose with
the left arm and missed the selected point by 0.379 m, then navigated closer
and retried with the right arm. A private real-simulator probe reproduced this
first 42-step reach with the archived base pose and XY target. It kept the
original basket at a valid floor height, shifting its XY and the chosen item's
target Z together to preserve their local geometry. Without support anchors,
the first failed reach alone moved the basket 0.076 m and rotated it 21.3°.
With the model-selected child, its existing basket, and remaining contents
briefly anchored during that same visible arm movement, the basket moved only
0.000013 m, rotated 0°, and every remaining item stayed officially `Inside`
after ten free physics steps. A longer protected component replay covering
the near-base retry, grasp, lift, stow, and ten free steps also kept every
remaining item `Inside`; maximum basket motion was 0.000015 m and 0°.
The first staging attempt mistakenly lowered the basket into the floor and
was discarded as a valid containment comparison. These probes use private
object handles and a staged base, so they verify the motor mechanism only;
r36 must verify an autonomous task run and its video.

The r35 sofa contact was officially valid but visually poor. A private probe
at the exact model-selected sofa point reproduced official `OnTop=true` and
a hand error of 0.065 m, while the combined four-joint trunk excursion was
1.66 radians; the r35 video shows the body folded across the cushion. A
0.9-radian combined trunk budget kept the body more upright at that same
point, but left the hand 0.377 m from it. The first version of the probe
incorrectly marked this as passed because it checked `OnTop` and furniture
motion without checking hand reach. Its raw result is preserved with a
`presentation_reach_audit.json` addendum marking the visible-reach gate false;
the probe now requires a hand error at most 0.18 m.

To test whether a task-valid patch is reachable, a separate **diagnostic**, not
model-selected, point was backprojected from the archived r35 front RGB near
the robot-facing cushion edge. At the same base pose and 0.9-radian trunk
budget, the hand error was 0.108 m and official `OnTop` remained true after
retreat and ten free steps. The five-view component clip shows a shallower
lean and item release near the hand; it does not approve a full-task demo.
`experiments/demo_sofa_posture_probe_20261011_near_edge/point_provenance_addendum.json`
records the diagnostic pixel and explicitly excludes it from model-choice
evidence. The autonomous model still has to select its own current RGB pixel.
When a deep sofa patch exceeds upright reach, the executor now retracts the
held item and returns `posture_limited_reach`, directing the model to select a
visible near-edge patch or another approach to the same sofa. This is a
failure-specific recovery hint, not an executor-selected replacement point.

Christmas r36 was an autonomous `gpt-6-astra`/`low` partial run on public
instance 301 / seed 0. The model selected an individual wreath from the
loaded basket, then chose a near-edge sofa pixel in its own RGB; the bounded
reach ended 0.126 m from that point and official `OnTop` survived seating.
It then selected an individual pillar candle and placed it on the breakfast
table, also retaining official `OnTop`. The supervisor stopped the run when
the basket was pushed across the floor and tipped on the return journey.
The whole-task audit is **false**, with no model finish or presentation
approval. The five-view page passed video structure only:
`http://10.130.140.115:8769/demo_motion_first10_20261009/10_putting_up_Christmas_decorations_inside_r36/review_5view.html`.

The archived return plan crossed the basket's original footprint and listed
13 other dynamic obstacles but omitted `wicker_basket_218`. The floor-object
filter required an AABB top above base height + 0.25 m, while this basket's
top is about 0.19 m. A private real-simulator replay of the archived base
pose and model-selected target recreated the exact path. The basket first
moved more than 0.01 m at simulation step 202; by the end it had translated
2.125 m and rotated 36.4°. This is causal motor evidence, not an autonomous
task result. The filter now includes large-footprint floor objects whose top
is above base height + 0.08 m, so the basket enters the current dynamic
occupancy overlay. The same-scene green replay included the basket among 14
dynamic obstacles and planned a detour. It reached the original target over
461 simulation steps, with basket displacement 0.0000037 m, maximum observed
displacement 0.0000072 m, and 0° rotation. The red and green records are under
`experiments/demo_basket_nav_collision_probe_20261011_{red2,green}` outside
Git. They establish this route's motor safety; r37 must still verify model
choices, whole-task success, and presentation quality.
