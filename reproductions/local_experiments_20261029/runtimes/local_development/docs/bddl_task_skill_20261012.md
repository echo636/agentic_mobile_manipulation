# BDDL-only task planning: `rearrange_your_room`

The official `rearrange_your_room/problem0.bddl` is the task specification for
this trial. The public task catalog has `instruction: null`; the old
natural-language task-decomposition experiments therefore did not test the
requested BDDL-only behavior.

The BDDL goal has two obligations:

1. `exists stand`: place the tissue dispenser on any valid stand.
2. `forpairs pillow bed`: place the two pillows on different beds, one per bed.

The initial placement of both pillows on the bedroom floor and the tissue
dispenser on an armchair is a search hint, not a final-state requirement. The
wildcard stand is a scene-dependent set, not a fixed visible instance.

`skills/task-decomposition/SKILL.md` now targets BDDL input. The model is
instructed to read it only for BDDL tasks. `scripts/run_no_demo_trial.py` can
take `--bddl-source` instead of `--instruction`; the same BDDL text is passed
to the RGB controller and its SHA-256 is recorded in `summary.json`. This is
a BDDL-derived experiment, not an official natural-language instruction.

An isolated model-only probe in
`../experiments/bddl_skill_rearrange_room_probe_20261012/response.txt` correctly
identified both final conditions and separated them from the initial scene.
A second probe on the more complex `sorting_bottles_cans_and_paper` BDDL in
`../experiments/bddl_skill_sorting_probe_20261012/response.txt` identified
three distinct existential bucket witnesses, the nested universal placements,
the negative constraints, and an asymmetry in the formal exclusions. These
probes had no simulator images or action tools, so they check translation,
not task completion.

Early attempts stopped before the controller started: Isaac Sim segfaulted in
`simulation_app._start_app`. The final runner writes the BDDL to
`task_spec.bddl` and passes `--instruction-file` to the simulator, avoiding a
long BDDL command-line argument. Both matched GPU 0 runs then initialized and
produced valid video. The startup experiments do not isolate one definitive
native cause of the earlier crash.

## Matched simulator experiment, 2026-10-12

Both runs used official `rearrange_your_room/problem0.bddl` (SHA-256
`184b170862a259cd93bf96887b6fb8152220ac46077cbb7d1b6138d156611f89`),
public-test instance 301, seed 0, `gpt-6-astra` at low reasoning effort,
the same provider profile, RGB observations, and `demo_motion=false`. The
only intended agent-level difference was whether `task-decomposition` was in
the skill manifest and model prompt. Other skills remained available. The
skill run's model-events trace confirms a `read_skill(task-decomposition)`
call; the control manifest and trace have no such call.

| Run | Skill | Official BDDL success | Goal fraction | Executed manipulation |
| --- | --- | --- | --- | --- |
| `task_bddl_rearrange_room_20261012_r14_skill` | yes | no | 0.0 | none; four grasps hit fixed objects |
| `task_bddl_rearrange_room_20261012_r15_no_skill` | no | yes | 1.0 | three grasps and three `place_on_top` actions |

Both controllers ended with a formal `finish`, model and simulator calls align,
and both videos passed. The no-skill controller reported `achieved`; the skill
controller reported `blocked`. The no-skill run satisfied the complete official
goal despite several failed navigation attempts. The skill run translated the
goal but failed to ground movable objects from RGB. This paired observation
does **not** support a benefit from the current skill; a single stochastic
pair cannot estimate its average effect. In particular, it shows that formal
goal parsing was not the limiting factor in this instance.

The same repository audit and five-view page builder used for demo-motion
experiments generated both reviews. The comparison index is
`http://10.130.140.115:8769/bddl_rearrange_room_ablation_index.html`;
each row links to its video, replay, and machine-readable audit. The runs
are RGB-agent experiments with an idealized contact executor, not official
submission-eligible rollouts.

## Additional complex-task probes, 2026-10-12

The same paired protocol was applied to two higher-complexity tasks. Both used
instance 301, seed 0, `gpt-6-astra` low reasoning effort, `demo_motion=false`,
the same provider profile, and the same BDDL-only input.

| Task | With BDDL skill | Without BDDL skill | Interpretation |
| --- | --- | --- | --- |
| `sorting_bottles_cans_and_paper` | Timed out after placing both bottles and both paper items; no formal finish; official success false | Timed out while attempting the last paper transfer; official success false | The task is too long for the current 30-minute controller budget; not a clean skill comparison |
| `setting_the_table` | Timed out after placing both plates; cupcake/fork/knife pairings remained | Formal finish after repeated fixed-object grasp failures; official success false | Both runs failed for execution/visual reasons; not evidence that translation helped or hurt |

The first pair's review page is
`http://10.130.140.115:8769/bddl_sorting_bottles_ablation_index.html`;
the second pair's page is
`http://10.130.140.115:8769/bddl_setting_table_ablation_index.html`.
These are valid audit records, but neither pair is a decisive effectiveness
test. A better next evaluation should either increase the action/time budget or
use a complex BDDL whose required manipulation sequence is shorter, then run
multiple seeds.

The third pair, `sorting_books_on_shelf`, was also run with the same protocol.
The skill run moved several books into the bookcase but timed out during
recovery from shelf-placement sampling errors. The no-skill run reached a
similar placement-sampling error and timed out without formal finish. Both
official outcomes were false. Its review page is
`http://10.130.140.115:8769/bddl_sorting_books_ablation_index.html`.

## Timeout policy correction

The original trial runner exposed only a wall-clock `--timeout`, so slow model
responses could end a complex task before its action budget was consumed. The
runner now exposes `--max-actions` and `--max-sim-steps`; these counters apply
even when an external execution clock is present. Tool results include
`actions_remaining`, and the prompt tells the model to finish once its verified
goals are complete. `--timeout` remains as a wide hard watchdog for a hung
provider, native simulator call, or unrecoverable process. This separates model
latency from the normal task budget while retaining a way to reclaim a stuck
GPU process.
