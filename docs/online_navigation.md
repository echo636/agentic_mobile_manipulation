# Online depth navigation on the harness branch

This branch replaces the Original executor's precomputed traversability lookup
with a map built during the episode. Frozen benchmark workers retain their
recorded source; this change does not retroactively update their results.

## Sensor and policy boundary

Four independent virtual head cameras capture front, back, left and right at
one frozen simulation state. Their optical XY origins now coincide with the
robot center, with a 20-degree downward pitch. The mount height is computed
from the robot's initial visual geometry so its body projects into the bottom
20% of the image or below it, with 5 cm clearance. The R2 component measured
a mount height of 1.811811 m relative to the robot base reference. The initial real test showed that the old
collision-AABB height placed the centered front view behind the visible head.
The previous 0.35 m outward ring left a central square unobserved by all four
cameras. We changed the rig instead of inventing a free starting disk.

The model still receives only RGB and public tool results. Calibrated axial
depth, full camera transforms, robot geometry, map cells and simulator pose
remain private to the executor. There is no wrist camera. Depth rays matching
the robot's own visual geometry are omitted; they do not become free rays
through the body. Original RGB and depth captures remain available for audit.

## Mapping and localization

`depth_scan.py` backprojects valid depth with the actual intrinsics and USD
camera convention (-Z forward). It records each camera's true origin, separates
obstacle hits from floor support, and produces an explicitly **2D projected**
range scan. Invalid depth supplies no evidence. This projection is a planar
navigation approximation, not proof that the complete robot height is clear.

The project-owned native worker calls Cartographer 2.0's `ProbabilityGrid` and
`ProbabilityGridRangeDataInserter2D`. Every episode starts with an unknown map;
only that episode's sensor packets update it. The worker exports immutable
free / occupied / unknown snapshots at 5 cm resolution. Recent observed hits
block new obstacles immediately; repeated valid free observations can clear
old occupied evidence. Missing observations alone never clear an obstacle.

Localization uses the simulator's pose as ideal private odometry. This is
online **mapping with ideal localization**, not scan-only SLAM or loop closure.
`ObservedNavigationScene` skips the precomputed layout-map loader and raises
on `trav_map` access. There is no NavMesh or static-grid fallback.

The source reference is `dadwadw233/habitat-gs`, `jiarui/memory-slam`, commit
`136950b2fb419b90d2607d4cfc6832f3957eeaa5`. Its occupancy-navigation pipeline
provided the online mapping / A* structure. The separately named
`jinkai/harness` branch at `0815cf2` uses NavMesh; these must not be confused.
We adapted the native range inserter directly rather than importing the entire
graph-memory, SLAM localization and model-facing map interface.

## Planning and execution

The public action remains `act(primitive="navigate_to", target={image_ref,
point}, revision=...)`. The selected RGB pixel is grounded privately to the
same visible surface. Approach candidates are evaluated on the current
observed map, with a desired 0.7 m standoff and maximum 1.4 m manipulation
reach. They must lie in the real camera frusta.

Occupied cells, unknown cells and map boundaries are inflated by the radius
of the robot base and wheels. An eight-neighbor A* search cannot cut diagonal
corners. Path simplification and each movement use the same exact grid
supercover check. A missing observed route returns a navigation error; it
does not fall back to a scene oracle or teleport the robot.

The existing ideal kinematic base actuator advances at at most 0.5 m/s and
turns at at most 60 degrees/s, using actual pose feedback. After approximately
25 cm or 15 degrees it captures new depth and replans to the same chosen
endpoint. The episode execution deadline is shared with the rest of the
harness. Final RGB, private scoring and recording closure remain separate.

This does not implement a physical wheel controller, full-body 3D collision
checking, stair navigation, or a learned low-level policy. The self-depth mask
currently includes robot links only; it does not automatically include a held
payload or enlarge the navigation footprint for that payload. Carrying while
navigating requires separate validation. A successful navigation component is
not a successful BEHAVIOR task.

## Runtime and validation

Build and package the CPU worker as described in
[`native/cartographer/README.md`](../native/cartographer/README.md), and set
`MAS_CARTOGRAPHER_RUNTIME` to the project-owned runtime prefix. The existing
navigation environment is used only as a read-only build source. In particular,
the worker and Cartographer library must use compatible Eigen headers; the
existing library was built with Eigen 3, and an Eigen 5 header build produced
incorrect map growth and a stuck inserter during the native smoke test.

`scripts/probe_online_navigation.py` checks an actual current-camera pixel,
map updates during motion, at least 0.5 m travel, four RGB outputs, independent
scoring and video closure with precomputed-map access disabled. It is a private
component experiment, not a benchmark attempt. A complete Astra task is an
additional validation level. Per-attempt outcomes, source/runtime hashes and
raw artifacts are recorded in `operations/jinkai_live_navigation_20261004`
outside the source repository; consult the recorded level before claiming
acceptance or task success.


## Validated runtime and current acceptance level

The tested runtime is `73b1b9a45275893c273fe5253a72f0ad968ebdd5`.
The S115 R2 navigation component passed on 2026-10-04:

| Recorded check | Result |
| --- | --- |
| Actual displacement | 1.1252775 m |
| Replanning during motion | 7 replans |
| Map sequence | 1 → 11 |
| Simulator steps | 113 |
| Precomputed traversability reads | 0 |
| Initial and final four-view RGB | Passed |
| Video | 115 frames, 3.8333 s of simulation time; verified |
| Independent score and resource cleanup | Saved / passed |

The component selected a visible floor point and tested navigation, not the
radio task's goal. Its independent Q was 0; this is neither a complete task
failure nor a task-success result. The complete Astra task was validated separately, as recorded below. Earlier checks
include 35 focused CPU project tests and 9 tests using the actual native mapper.

R1 exposed a map-crop coordinate roundoff (approximately 0.2 micrometers) and an
incorrect requirement that the fixed endpoint exactly equal a newly cropped
cell center. The tested version exports map origins in double precision and
preserves the fixed WORLD endpoint through a checked free connection to the new
grid. This correction does not clear unknown cells or change the intended goal.

Evidence is kept in the shared workspace at
`operations/jinkai_live_navigation_20261004/radio_r2_s115/component_result.json`,
`validation.json`, and the referenced execution manifest. The existing Original
100-task results still refer to their archived implementations, including the
static-GT navigation path described at `ac2995b`; Official remains a separate
native-symbolic implementation. The online component is not merged into either
method's benchmark statistics.


The existing system guide embeds `web/assets/online_navigation_r2.svg`, a plot
of the actual initial/final occupancy files and recorded robot positions. It
uses the same world extent in both panels and distinguishes the selected RGB
floor hint from the fixed approach goal. It is executor-private diagnostic
evidence, not an extra model observation. Regenerate it with
`scripts/render_online_map_progress.py`; input hashes and coordinate conventions
are recorded in the batch's `map_visualization.json`.


## Complete Astra task acceptance

The same runtime `73b1b9a` completed `turning_on_radio`, instance 301, seed 0,
with official Q=1, `task_success=true`, and the model's formal
`finish(outcome="achieved")`:

- 4 actions and 14 tool calls; 337.48 seconds of model execution, excluding
  simulator initialization.
- 775 simulation control steps and 5.6996 m cumulative actual navigation.
- 782 original video frames covering 26.0667 seconds of simulation time.
- Video, four-camera observation, event alignment and resource cleanup checks
  passed; the archived replay reported no replay errors.
- The model navigated twice, toggled the radio on (including internal approach
  to the same selected radio), then attempted a fourth navigation action for confirmation. That action
  partially moved and returned `navigation_invalid_start`. The model read the
  failure-recovery skill, used asynchronous observation to confirm the radio
  indicator, and finished successfully. **The task passed with one local
  navigation failure; it was not an error-free sequence of actions.**

[Open the complete model replay](http://10.76.5.241:8765/manipulation_runs/mas_online_navigation_original_000_turning_on_radio_i301_s0_r1/replay.html).

This is one complete task acceptance, not a 100-task success rate. It does not
exercise carrying or placing objects. The R2 map figure remains a separate,
no-model navigation component with Q=0; it is not relabeled as the Astra task's
map or trajectory. Frozen Original/Official benchmark statistics remain
separate from both new validations.
