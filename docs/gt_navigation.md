# Visual-point GT navigation

The default RGB executor now uses the visual-point GT planning strategy from
`dadwadw233/habitat-gs`, branch `jinkai/harness`, commit
`0815cf234ee591bacd8017e9b1def4fac13e649b`. Attribution and the source license
are in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The current harness uses a private GT grid and does not run Cartographer. The
model supplies `act(primitive="navigate_to", target={image_ref, point}, revision)`.
It receives four RGB images and tool feedback, never maps, depth, object IDs,
world coordinates or evaluator state. `initialize` supplies the first snapshot;
each `act` / `look` supplies the next one.

## Current navigation pipeline

1. Backproject the exact selected pixel using the capture's calibrated depth.
   There is no navigation object lookup or depth-to-mesh agreement gate.
2. For a floor click, find the closest reachable grid position within 0.75 m of
   the point. Do not add a camera standoff or require the floor pixel to remain
   visible at arrival. For an elevated object/surface, sample approach positions
   using the reference's 0.3 / 0.6 / 0.9 / 1.2 m rings and 0.7 m preference.
   An implicit manipulation approach still checks actual reach and visibility.
3. Load the GT floor support raster, query current PhysX collision geometry,
   and update dirty regions when object poses or joints change. AABBs bound the
   query region; the actual collision query determines occupied cells. Closed
   doors block travel, and opening a door changes the next navigation grid.
4. Combine occupancy eroded by the measured oriented robot footprint in each height band. Plan a
   collision-free grid route without cutting corners. If the current heading
   cannot fit, test alternative headings and the whole in-place turn's swept
   footprint. Use a feasible turn followed by holonomic fixed-heading travel.
5. If the existing chassis footprint partly overlaps a newly opened leaf, allow
   only a short continuous exit with non-increasing overlap and a free base
   centre, ending at zero overlap. This is not a start teleport or a general
   permission to cross obstacles.
6. Execute bounded pose increments (at most 0.5 m/s and 60 degrees/s), read back
   the actual pose after each simulator step, and verify arrival. Pruned paths
   reserve a 5 mm corridor against tiny feedback deviations at grid corners.

Candidate geometry and elevated-target scoring originate from Jinkai's visual
point strategy. OmniGibson supplies its own floor support, current collisions
and ideal pose follower; Habitat's native C++ navmesh/follower is not running
inside this system. Floor-point goals intentionally use direct-point semantics.

## Limits and evidence

This remains ideal kinematic base actuation, not a wheel controller or a complete
SE(2) / articulated-body motion planner. One fixed heading is used for each
translation route, with a checked turn at its start. Scene obstacles and robot collision hulls are matched in four height bands,
avoiding a base-width column through the head. Convex hulls within each band
remain conservative; carried-object and articulated motion planning remain
incomplete. A genuinely blocked or disconnected point must
return a navigation failure rather than cross a closed door. Floor raster and
cell resolution also limit how closely a selected point can be reached.

`navigation_plans.jsonl`, `navigation_grids/*.npz`, `navigation_recovery.jsonl`
and `base_motion.jsonl` retain private geometry and actual motion evidence.
`scripts/probe_navigation_regression.py` reproduces archived failed geometries
in the real simulator and checks closed-door negatives, arrival, four-camera
capture, scoring and video closure. These are scripted component diagnostics,
not autonomous challenge success. The new 100-task run records yyf API routing
and its own immutable outcomes separately from all historical attempts.

## Grid-boundary regression

The original executor selected grid sample positions, then used a truncating
world-to-grid conversion. Sub-millimetre settling changes mapped 25 archived
navigation calls into neighboring obstacle cells. The new grid consistently
uses nearest-sample coordinates. Disconnected starts are rejected except for the bounded continuous clearance
exit above; there is no unlimited start snap or cross-wall recovery teleport.

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

The checks below describe historical validation of the GT strategy. They do not
establish real-simulator or task success for the restored current default. New
evaluations must record their own source, assets, task instance and outcome;
previous attempts and their results remain unchanged.

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

The isolated `bringing_in_wood` startup probe on the frozen `f90e51e` runtime
failed after class-name adaptation: legacy serialized robot state lacks
`controller_groups`. The class migration is therefore only a partial compatibility
fix; the task remains affected by an asset/source state-schema mismatch. No default
controller state was substituted and no original instance was rewritten. The 32-task
retest retains this task and its failure in the denominator. Historical trash/toolbox
camera audits both passed when rechecked on copies in the robot frame; original
validation files were preserved.
