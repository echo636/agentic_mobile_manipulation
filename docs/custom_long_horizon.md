# Custom long-horizon tasks and evaluation readiness

This pilot uses BEHAVIOR assets in joint scenes with new goals. It is not a
BEHAVIOR100 result or an official challenge submission. The user authorized
this extension beyond the historical public100-only experiment scope.

The runtime recipes in `task_designs/runtime/` are construction inputs. A recipe
is not an executable benchmark instance until a frozen scene and its SHA-256
are attached and the checks below pass. Generated scenes and experiment records
live outside Git.

The maintained evaluation candidates are now in `task_designs/evaluation_v2/`.
Historical `runtime/*_v1.json`, scenes and pilot results retain their original
meaning. V2 scores must never be substituted into a v1 result. Candidate recipes
are **not certified ready**: the release gate below is authoritative. Current
validation is recorded in `operations/long_horizon_readiness_20261019/` under the
shared workspace root, separately from the October 15 pilot.
The follow-up repair batch is `operations/long_horizon_repair_20261020/`.
Each attempt retains its own source and evidence; an earlier failure is not
replaced by a later repair.

## Tasks and dependencies

| Task | Coupled work | Independent work |
| --- | --- | --- |
| Workshop | Pack all five tools at storage → transport the closed loaded box → unpack on the workbench → return the empty closed box | Store two books and discard three cans |
| Lunch | Retrieve and cook two hotdogs → pack four food items → deliver lunch and tea | Set four dining items, discard three cans, store two leftovers and the board, restore appliances |
| Movie night | Clear and dust the table before serving; cook popcorn → switch microwave off before removal → serve popcorn | Store toys and trash; arrange six dining items; return brush and restore microwave |

The instruction is a short household request. Both controllers receive the same
public household context describing the menu, storage conventions, completion
criteria, and any historical constraints. This resolves ambiguity without giving
the controller object IDs, private predicates, the evaluator DAG, or a reference
action sequence. Context must be evaluated as part of the input, rather than
claiming the controller inferred an undisclosed preference from a short phrase.

The shared executor uses ideal navigation and controlled object carrying with
reach and collision checks; these references do not establish feasibility for a
physical robot controller. Navigation adds conservative height boundaries around the carried
assembly so a raised object does not inherit obstacles on a lower countertop. Surface placement finishes with five awake physics
steps within its existing settling budget and clears native state caches before
checking contacts. This prevents sleeping objects from retaining stale false
contact readings; it does not assign goal predicates or relax scoring.

## Independent evaluation

`CustomTaskBackend` reads actual OmniGibson object states.
`TemporalGoalEvaluator` combines simultaneous terminal conditions with committed
historical milestones and public transport constraints. Model notes are never
used to score the task. A milestone may be consumed later, such as unpacking a
previously delivered box. A later correct final pose cannot erase a transport
constraint violation. Partial credit is the fraction of terminal, historical,
and constraint conditions satisfied; it is explicitly **not official Q**.

Hypothetical placement candidates do not award milestones. An executor's
transactional rollback discards staged motion violations only when both the
tracked poses and predicates actually return to their previous values. Required
entities destroyed during execution make the task fail, instead of silently
omitting their goals.

## Validation and experiment protocol

1. Construct a joint scene from pinned asset definitions and verify initial
   assertions. Validate particle-system names against the pinned assets before
   simulator initialization; an inactive valid system differs from a misspelled
   system. Record asset, dependency, source, and instance hashes.
2. Execute a full reference trajectory through the same checked motor executor.
   Privileged target selection is permitted only for this feasibility diagnostic.
   Exercise different legal branch orders and evaluator negative cases. Preserve
   every failed attempt. A setter success or process exit does not prove task
   feasibility.
3. Restore the frozen scene in a fresh simulator. After camera creation, refresh
   contact data and state caches, then recheck the actual policy-ready conditions.
4. Freeze source, scene, and public input. Launch matched autonomous trials with
   `gpt-6.1-sol`, reasoning `low`, 200 act/look attempts and no episode wall
   deadline. Failed act/look calls count. This is a pilot budget, not evidence
   that a task requires 200 useful actions.
5. Both arms share tools, generic `task_notes`, executor, other skills, scene,
   public context, and budget. Only the hierarchical goal decomposition skill
   and its read instruction differ. Both enable the harness's mandatory-notes
   gate (`require_task_notes=true` in the actual pilot protocol): the first
   act/look requires a nonempty note in either arm. The control can write freeform
   notes and has no prescribed goal tree. Report this shared intervention when
   interpreting the ablation; it is not an entirely unprompted planning baseline.
   Do not merge this pilot with BEHAVIOR100 or
   the six-identity I/O repair cohort.
6. Audit controller closure, ordered MCP calls, independent score, observation
   alignment and continuous video. Replay shows model-authored goal-tree versions
   separately from offline actual-state evidence. The latter never enters policy
   observations. Report failed attempts and unknown scores explicitly.

Report measured useful reference/model actions, navigation, recovery attempts,
and dependency structure. Task length must not be inferred from the configured
step cap or padded with redundant motion.

## V2 task cards

| Frozen task family | Simultaneous terminal goals | Historical goals | Permanent violation checks | Reference orders |
| --- | ---: | ---: | ---: | --- |
| `warm_lunch_and_restore_v2` | 22 | 0 | 0 | cleanup first; start cooking first |
| `movie_night_and_restore_v2` | 16 | 6 | 2 | cleanup first; cooking overlaps cleanup |
| `workshop_setup_and_return_v2` | 11 | 4 | 1 | cleanup first; pack first |

**Lunch.** Cook both hotdogs; put the two hotdogs and two apple halves in the lunch
box; deliver that box, tea, two plates and two forks to the dining table. Discard
three cans, refrigerate the cookie and sandwich, store the board on the other
counter, close the refrigerator, and switch off and close the microwave. The
preparation counter must be clear of the specified lunch items, dishes, board and
leftovers. Each final condition must hold at the same time. No exact place-setting
arrangement is required. V2 directly checks preparation-counter clearance,
including cases where overlapping support surfaces also satisfy a destination.

**Movie night.** Put away two toys and three cans; clear and dust the coffee table
before serving food or any of six dining items. Cook the bag's popcorn in the
microwave, switch it off before actual removal, and serve the bag on the cleaned
table. Return the brush to the preparation counter; finish with the microwave
off and closed. V2 requires cooked popcorn with no raw popcorn remaining in the
bag, records actual removal, and keeps the power-off guard active through removal.
Turning the microwave off, turning it back on, and then removing the bag does not
satisfy the safety requirement. Particle cooking and subsequent transport must
be demonstrated in the simulator; a symbolic trace cannot establish feasibility.

The current movie-night construction recipe uses a 1.5× scale trash bin
(about 35 cm wide and 42 cm tall). The original 23 cm × 28 cm bin
failed the third-can insertion after the first two cans occupied its center.
This is a new fixture revision (`pilot_002_larger_bin`), not a change to
scoring or a capacity guarantee. Its scene hash and fresh physical references
must be validated separately; historical pilot_001 evidence is retained.

**Workshop.** At storage, pack all five tools (drill, pliers, flashlight, Allen
wrench, screwdriver) into the box and close it. Transport the closed, fully loaded
box to the workbench, unpack all five tools there, and return the empty closed box
to storage. Store two books on the short counter and discard three cans. Exact
positions on destination surfaces are unrestricted. A final photograph of five
tools on the bench and an empty box at storage cannot replace the required loaded
transport history. The reference approaches a moved object using its current
position, including the box and tools after delivery.

Every public context entry maps to one or more scored conditions in `requirements`.
The validator rejects hidden score conditions, unscored public requirements,
duplicate constraint IDs and changed frozen scenes. Raw predicate names describe
the measured state (`microwave_open`, `microwave_on`); terminal goal names describe
the desired outcome (`microwave_closed`, `microwave_off`).

## Additional task candidates

`task_designs/candidates/` contains two new contracts. Both reuse an existing
pilot scene with new goals; they are not additional layouts or evidence of scene
generalization. They remain `candidate_physics_unvalidated` until the same
physical and policy acceptance gates pass. Logical tests only validate scoring.

| Candidate | Required sequence | Terminal / history / constraints | Negative cases |
| --- | --- | --- | --- |
| `workshop_inventory_roundtrip_v1` | Pack at storage → deliver closed full kit → lay all five tools out together → repack at bench → return closed full kit | 11 / 5 / 2 | Skip unpacked inventory; return without drill; move return load with lid open |
| `kitchen_service_and_reset_v1` | Cook and pack four food items at prep → deliver complete meal → serve one hotdog and one apple half on each plate → return empty carrier | 23 / 4 / 1 | Serve without loaded delivery; return nonempty carrier; plate uncooked food |

The inventory task also requires the original book and trash cleanup. It ends
with the tools in the returned box, unlike the workshop task that leaves tools
on the bench. Taking out and immediately replacing each tool separately does
not satisfy the simultaneous inventory milestone. Both outbound and return
transport retain a permanent violation if the box is moved open or incomplete.

The serving task also requires table settings, tea, leftovers, board storage,
trash cleanup, cleared preparation space and restored appliances. Either
hotdog and either apple half can go on either plate; private object identity
does not impose an undisclosed serving assignment. All food on one plate is
insufficient. The final empty carrier alone cannot replace the loaded-delivery
and simultaneous-serving milestones.

References use the same checked action interface and 200-attempt ceiling. The
new references extend the original legal branch orders; they never set food,
contact or container states to force success. Frozen candidate specifications
bind to the parent scene hash without duplicating scene files. Their acceptance
records remain separate from the original three-task release.

## Reproducible acceptance

Use Python 3.11 or newer, and the pinned OmniGibson environment for physical runs.
Do not change the editable install in a shared environment. Point `PYTHONPATH` at
the frozen source checkout after sourcing the host's environment script.

```bash
python scripts/validate_custom_tasks.py --require-frozen /path/to/task.json
python scripts/validate_custom_task_physics.py --spec /path/to/task.json \
  --order cleanup_first --output /new/immutable/reference-output
python scripts/validate_custom_task_physics.py --spec /path/to/task.json \
  --negative-case skip_loaded_transport --output /new/immutable/negative-output
python scripts/validate_custom_readiness.py /path/to/release.json \
  --output /path/to/readiness.json
```

Physical commands run only through the shared resource coordinator after GPU,
host/cgroup memory, disk, quota, dependency and asset admission. Record host,
interpreter, GPU UUID, PID, unit, source commit/digest, spec/scene hashes and output.
Each invocation restores a fresh simulator; output directories cannot be reused.
References use privileged identity selection through the ordinary checked motor
executor. They never count as autonomous visual-policy success. Failed attempts
are retained; a continuation from a partly solved state is diagnostic evidence
and cannot count as a full reference.

Each task requires both declared legal orders, at least two fresh restores and
all declared negative cases:

| Task | Negative cases |
| --- | --- |
| Lunch | packed but uncooked food; an item returned to the preparation counter; removal of a required entity |
| Movie night | serving before cleaning; off → on → removal; uncooked popcorn served |
| Workshop | tools delivered without loaded-box transport; open-lid transport; one tool omitted from packing |

Negatives must reach and record their specific witness and produce the expected
independent rejection. A setup error is a failed diagnostic. Required-entity
removal is explicitly labeled simulator fault injection; all other negatives use
the checked action interface. Neither is a model trial.

A release manifest pins evidence files by path and SHA-256. Each task has `spec`,
`references`, `negatives`, and `policy_acceptance`. Each physical bundle binds
`validation`, `launch`, `initial`, `admission`, and `assets`; policy acceptance
binds `run`, `controller`, `observation`, `video_validation`, and `video`.
All records must match the release's exact source and frozen instance. The gate
recomputes the RGB/MCP audit from simulator and controller traces and verifies
video bytes. Interface acceptance requires real model interaction, formal finish,
aligned calls, observations and recording; a policy's task failure remains a valid
measured outcome and is reported separately. Missing evidence always leaves
`benchmark_ready=false`.

Archived source has no Git metadata. Its `source_freeze.json` must bind the commit
and the executable-source digest used by the physical release. The model recorder
recomputes that digest before recording the archive's identity; changed source
bytes are rejected. Policy acceptance checks both the commit and this digest,
separately from the recorder's historical `src/`-only hash.

After all gates pass, create matched evaluation manifests:

```bash
python scripts/prepare_custom_evaluation.py --release /path/to/release.json \
  --asset-manifest /path/to/assets-manifest.json --tag long_v2_eval \
  --output /new/paired-batch
```

This creates separate `hierarchical/manifest.json` and `unguided/manifest.json`
for the existing batch runner. Both use `gpt-6.1-sol`, low effort, skills profile,
mandatory generic notes, 200 counted act/look attempts, zero policy retries and
no episode wall deadline. Resource waiting, initialization and closure are
recorded separately. The generator refuses to produce a formal evaluation batch
from an unready release; exploratory acceptance runs remain explicitly separate.

## Reporting scope and limitations

The current fixtures are three hand-built task instances, each based on instance
301 with seed 0. Two valid reference orders demonstrate feasibility of those
instances, not robustness across layouts or seeds. Additional seeds/layouts need
new frozen scenes and their own acceptance records. Report results per task and
arm; do not infer statistical superiority from three paired instances. Publish
whole-task success alongside terminal/history/constraint breakdown, action and
recovery counts, model finish, infrastructure failures and evidence quality.
Unknown scores remain unknown. The partial fraction is a custom diagnostic with
equal weight per declared condition, not official Q and not directly comparable
across tasks with different condition sets.

The executor uses private ground truth and idealized carrying/navigation. These
tasks evaluate agent decisions and long-range ordering under that executor. They
do not establish end-to-end learned motor control or physical-robot performance.
