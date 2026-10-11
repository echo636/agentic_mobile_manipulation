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

## Status after r31 (2026-10-11, Asia/Shanghai)

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
