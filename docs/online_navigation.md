# Online depth navigation on the harness branch

This branch replaces the Original executor's precomputed traversability lookup
with a map built during the episode. Frozen benchmark workers retain their
recorded source; this change does not retroactively update their results.

## Sensor and policy boundary

Four independent virtual head cameras capture front, back, left and right at
one frozen simulation state. Their optical XY origins now coincide with the
robot center, with a 20-degree downward pitch. The mount height is computed
from the robot's visual geometry so its own body stays below the lower 20%
of the image, with 5 cm clearance. The initial real test showed that the old
collision-AABB height placed the centered front view behind the visible head.
The
previous 0.35 m outward ring left a central square unobserved by all four
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
checking, stair navigation, or a learned low-level policy. A successful
navigation component is not a successful BEHAVIOR task.

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
