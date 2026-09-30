# Visual-point GT navigation

The default RGB executor now uses the visual-point GT planning strategy from
`dadwadw233/habitat-gs`, branch `jinkai/harness`, commit
`0815cf234ee591bacd8017e9b1def4fac13e649b`. Attribution and the source license
are in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The model still supplies exactly `act(primitive="navigate_to", target={image_ref,
point}, revision)`. It does not receive maps, depth, real object names, world
coordinates, path lengths, or task evaluator state. Manipulation primitives
remain symbolic/volume operations and are not changed by this navigation port.

## Transferred strategy and simulator adaptation

| Reference behavior | OmniGibson implementation |
|---|---|
| Visual-point candidate positions: direct floor projection, camera-side 0.7 m standoff, 12 directions at 0.3/0.6/0.9/1.2 m radii | Same candidate geometry, Habitat XZ converted to OmniGibson XY |
| `PathFinder.snap_point`, bounded at 0.75 m | Nearest free sample on the private eroded traversability grid, same projection bound |
| Verify candidate reachability and geodesic path length | One multi-goal Dijkstra search; diagonal corner cutting is forbidden |
| Score standoff error, snap distance and near-zero progress; use geodesic distance as tie-break | Same point-mode scoring coefficients and thresholds |
| Native greedy follower with actual step feedback and explicit reached/blocked status | Bounded turn/advance follower reading the actual robot pose after every control step; stalls, divergence, invalid cells and budget exhaustion fail explicitly |
| Per-step movement recording | Existing RGB recorder plus timestamped private base-motion and navigation-plan logs |

Habitat's native C++ navmesh and `GreedyGeodesicFollower` cannot operate on an
OmniGibson scene directly; they are not imported or claimed to be running here.
This is a strategy port with an explicit grid/kinematic adapter. Navigation
checks static grid occupancy. It does **not** provide full dynamic body/held-object
collision checking. Native navmesh geometry may be more permissive or accurate.

The existing exact-pixel depth/raycast target grounding is retained. The
reference's neighboring-pixel search and GT scene-graph object selection are
not enabled. No task object is silently substituted for the model's selected
target. Public tool schemas and RGB-only response filtering are unchanged.

## Grid-boundary regression

The original executor selected grid sample positions, then used a truncating
world-to-grid conversion. Sub-millimetre settling changes mapped 25 archived
navigation calls into neighboring obstacle cells. The new grid consistently
uses nearest-sample coordinates. Occupied or disconnected starts are rejected;
there is no unlimited start snap or cross-wall recovery teleport.

Grid paths are pruned only where the whole segment is traversable, rather than
discarding waypoints at a fixed stride. The follower reads actual position and
yaw after each simulation step and verifies the final position.

## Verification and scope

- CPU tests exercise recorded pose rounding, obstacle corners, disconnected
  regions, bounded snapping/search, doorway routes, command rate limits, stalls
  and divergence. The existing RGB information boundary tests also apply.
- `scripts/validate_gt_navigation.py` compares candidate coordinates and scoring
  directly against pure functions extracted from the pinned reference. It can
  replay archived failed navigation queries using original maps and private
  camera calibration/depth. This is offline execution validation, not task success.
- `scripts/probe_gt_navigation.py` is an explicitly scripted real-simulator
  regression using archived geometry. It does not run an LLM or count toward
  the benchmark. Its result is separate from the 100-task batch.

The original 100-task runtime remains frozen and paused at the user's request.
No automatic batch resume is part of this change. A future evaluation must use
a new recorded executor version and retain original failed/interrupted attempts.

## Validation on 2026-09-30 and scoped retest

- CPU suite: 85 tests passed, including navigation geometry/follower, RGB boundary,
  tilted robot camera audit, spectator failure isolation, legacy R1Pro scene metadata,
  and immutable batch accounting.
- Direct reference parity: 5,000 candidate coordinates (maximum discrepancy
  5.41e-7 m) and five scoring cases matched the jinkai source snapshot.
- Archived failure reproduction: all 25 invalid-start navigation queries planned and
  followed on the original static maps within the 700-step action budget.
- Real OmniGibson scripted navigation probe (source `34cd684`): both moves reached;
  172 and 207 control steps including settling, final errors below 4e-6 m.
  This checks the actual navigation adapter, not model performance or task success.
- A separate 32-task RGB/model retest is authorized: the previous 29 completed tasks
  plus three user-interrupted tasks. Instance 301, seed 0, original instructions,
  model and budgets are retained. The remaining 68 tasks are excluded. New run IDs
  and a separate report directory preserve the paused original batch.

Additional infrastructure fixes before that retest: avoid the spectator camera's
Torch-compiled matrix-to-quaternion path and isolate recording pose failures; adapt
only the known serialized legacy R1Pro class to the current Robot(model=r1pro) in a
hashed derived scene copy, retaining identity and asset hash verification. Audit
camera axes in the robot frame (world-horizontal axes change when the base tilts).
These changes do not fix depth/collision disagreement, placement sampling failures,
or all simulator initialization faults. Raw errors and independent task scores remain
separate. This is an RGB + ideal executor research protocol, not an official submission.
