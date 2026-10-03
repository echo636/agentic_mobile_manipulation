# Official executor alignment repair — 2026-10-04

The historical Official cohort finished with 6 successes and 94 failures. It used
several source versions; its scores are not results for the repairs below. Across
that cohort, all 116 NAVIGATE_TO calls failed. Even the latest frozen runtime's
39 tasks contained 51 failed navigation calls. A new private reproduction found a
coordinate integration error, independent of the model's target-selection quality.

## Confirmed coordinate error

The pinned Starter sampler produces candidate base poses in **world** x/y/yaw.
Its `_validate_poses` copied those numbers directly into the holonomic robot's
joint coordinates. The current Robot API and CuRobo use coordinates relative to
the virtual `root_link` anchor for those joints.

In `laying_tile_floors`, instance 301, seed 0, the actual robot was near
`(2.724, 9.211, 0.005)` in the world, while its virtual joint anchor was
`(300, 300, 300)`. That anchor is not the physical robot position. Assigning world
x/y as relative joint coordinates displaced the collision/IK candidate by
**424.264 metres** in the horizontal plane.

A disposable S115 GPU2 diagnostic, using the same pinned dependencies and task
instance, compared the first three native candidates:

| Check | Old assignment | Correct world-to-anchor assignment |
|---|---|---|
| Reconstructed candidate position error | 424.264 m | 0.000014–0.000019 m |
| Candidate 0, IK with world collisions | Failed | Passed |
| Candidate 1, IK with world collisions | Failed | Failed; collision blocked |
| Candidate 2, IK with world collisions | Failed | Passed |

The current robot end-effector pose passed IK both with and without world
collisions. The diagnostic did not move the robot or execute environment steps.
This isolates the coordinate error; it is not a completed navigation action or a
benchmark success.

There was a second error at the endpoint: `_get_robot_pose_from_2d_pose` reused
relative base z/roll/pitch as world values. In this scene the local z was about
`-299.995`, so fixing candidates alone would still produce a wrong world height.
Both conversions are repaired together.

`executors/holonomic_navigation_frames.py` installs an adapter on this primitive
instance. It converts candidate world poses into all six relative base joints,
synchronizes native collision-checker locked base coordinates when needed, and
retains actual world height/roll/pitch when constructing the endpoint pose.
Native sampling, candidate order, world collision checks, held-object collision
attachment, ARM IK and symbolic pose setting/settling remain in use. The adapter
and upstream source hashes, plus the root anchor, are recorded in provenance.
Original uses its separate GT navigation path through Robot's world-pose API and
did not use this faulty candidate assignment.

## Confirmed budget mismatch

The previous adapter imposed 120 wall-clock seconds and 700 environment steps on
each primitive. Native `_settle_robot` can yield 50 initial steps plus up to 500
additional steps. Many actions settle internally and then settle again in
`apply_ref`; placement also settles during release. Thus native execution can
legitimately need 1100 or 1650 steps.

The historical `packing_meal_for_delivery` grasp was cut off at exactly 700 steps
and 118.38 seconds. Other actions crossed the 120-second limit while still
settling. Recording/rendering consumed part of that wall time; the measured
pipeline intervals are not pure GPU render time.

Protocol `rgb_official_symbolic_episode_budget_v3` removes those independent
Official action caps. A primitive receives the episode's remaining environment
steps and uses the existing episode execution deadline. Internal native sampling
still checks that deadline. A supervisor enforces process termination when native
code cannot return to Python. Initialization remains outside the 30-minute
execution clock.

There was also a 300-second limit in the HTTP bridge, proxy RPC and Codex MCP
configuration. Official transport waits now allow the 1800-second episode budget
plus 120 seconds for response/cleanup, while the model process and simulator keep
the execution deadline. A transport timeout does not mean an action was cancelled.
Original retains its previous action and transport budgets.

## Useful diagnostics and remaining constraints

Private planner records now contain candidate validity and IK reachability, not
just elapsed time. Sparse settling records show base velocity and world pose
without changing the generated actions or the native stopping criterion. The
component probe requires navigation to actually complete; the obsolete condition
that accepted a missing-planner failure has been removed.

The experiment still uses one `apply_ref` attempt; upstream defaults to three.
This is a declared experimental setting, not complete equivalence to every
upstream default. A released object may require an explicit new grasp before a
placement retry, so extra attempts are not automatically harmless or effective.
The episode still has 80-action, 240-tool-call and 20,000-environment-step limits.

These repairs do not make the executor perfect. Native placement sampling and
postconditions can fail, visual point selection can be wrong, and real resource
exhaustion or native simulator faults remain possible. Symbolic grasp and state
operations also differ from physical control: some directly change poses or
states and do not enforce a physical approach. No custom placement fallback,
automatic target substitution, task-score exposure or collision bypass was added.

Specific archived failures remain separate from the confirmed alignment bugs:

- Task 090, `composting_waste`: the correct banana and trash can were selected,
  but native placement sampling failed before release in 13.61 seconds.
- Task 082, `store_batteries`: the correct battery and an allowed cabinet were
  selected; the object was released, but `Inside` was false after 150 steps and
  28.37 seconds. The next placement reported an empty hand.
- Task 080, `clean_up_broken_glass`: the executor target was a bench, while the
  instruction required a trash can. Target selection/grounding must be examined
  separately from execution reliability.

Native placement samples a bounded number of candidate poses. It then releases,
settles, teleports and settles again before evaluating the relation. A sampled
pose is not a guarantee that physical settling preserves `Inside` or `OnTop`.
Residual object velocity is a plausible contributor because the reviewed Python
pose setter does not explicitly clear it and settling checks robot base velocity;
these archived runs lack object-velocity evidence, so this is **unverified**, not
an established cause or a repair claim.

## Validation record

The new main-based harness branch passed its real Astra/Codex + FastMCP +
OmniGibson radio episode: Q=1, two actions, 73.942 seconds of execution excluding
initialization, with four-view RGB, tool/image evidence, independent score and
recorded replay. That validates the Original harness path, not Official's fixes.

The private Official frame comparison above completed in 18.09 seconds after
initialization, with unchanged robot pose/joints. CPU tests separately cover frame
conversion, episode deadlines, native generator delegation and all three MCP
transports: the integrated Official suite had 262 passing tests and seven
dependency-specific skips.

The repaired native component regression used frozen `8ec0fdb`, the same
`laying_tile_floors` instance, and a private scripted tile target:

| Native action | Result | Environment steps | Wall time |
|---|---|---:|---:|
| `NAVIGATE_TO` | Completed; endpoint world z approximately 0.005 m | 1100 | 175.228 s |
| `GRASP` | Completed; native in-hand assertion passed | 1100 | 162.291 s |
| `RELEASE` | Completed; native empty-hand assertion passed | 1100 | 148.997 s |

All three executed the native generator, retained collision/IK checks, and
restored temporary wrappers. Their 3300 raw control steps, four RGB views and
completed video are archived privately. Process cleanup and resource-lease
release passed. This is executor component evidence, not a model-driven benchmark
success. The fixture was not published as an agent replay.

Sparse settling traces show a stable world base pose but nonzero native velocity
above the 0.01 m/s stopping threshold. Each action therefore consumed two full
550-step settles. The stopping criterion was not weakened; those measured action
durations demonstrate why the former 120-second/700-step limits were premature.

A fresh Astra/Codex + FastMCP Official radio episode, also at frozen `8ec0fdb`,
completed with **task success and Q=1** in **127.454 seconds of actual execution**,
excluding initialization. The task was instance 301, seed 0, with its original
instruction. The model made six tool calls, including three native `NAVIGATE_TO`
actions and one `TOGGLE_ON`; all four actions returned successfully and executed
400 environment steps. Video, four-view RGB, tool/evidence alignment and simulator
cleanup checks passed. The separate model-wrapper duration was 133.343 seconds;
it is not the episode execution duration.

The agent itself finished with `blocked` because it could not verify an explicit
power indicator from RGB. The independent evaluator reported task success. Both
outcomes are preserved: a successful simulator predicate does not establish that
the agent can visually recognize completion.

Those radio actions each settled in 100 steps, unlike the tile component's 1100
steps. This is scene-dependent evidence, not a claim that all native actions must
take several minutes. The real radio also validates navigation selected by the
model from RGB, beyond the private scripted-target regression.

[Official radio replay](http://10.76.5.241:8765/manipulation_runs/mas_harness_phase2_official_000_turning_on_radio_i301_s0_r1/replay.html).
These individual acceptance runs do not establish a new 100-task success rate or
an improvement over Original on a controlled performance comparison.

All exact host/interpreter/GPU UUID/unit/PID, source, dependency, asset and task
hashes; failures; patches; and raw records are retained in
`operations/harness_upgrade_20261004` in the shared project workspace. These
component and acceptance runs are separate from the historical 100-task cohort.
