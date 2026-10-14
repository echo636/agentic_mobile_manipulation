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
2. Both floor and elevated clicks search the entire start-connected component,
   choosing the closest feasible point to the selected XY, then breaking ties
   by travel distance. There is no 0.75 m projection radius, ring sampling, or
   1.4 m navigation cutoff. Elevated approaches must see the target through a
   camera frustum with an 8% image-edge margin and unobstructed scene ray; a floor point may be below the
   camera at arrival. This visibility difference does not change the search
   radius or connectivity rules. Manipulation retains its separate reach check.
3. Load the GT floor support raster, query current PhysX collision geometry,
   and update dirty regions when object poses or joints change. AABBs bound the
   query region; the actual collision query determines occupied cells. Closed
   doors block travel, and opening a door changes the next navigation grid.
4. Combine occupancy eroded by the measured oriented robot footprint in each height band. Plan a
   collision-free grid route without cutting corners. If the current heading
   cannot reach the nearest target region, compare alternative headings and the whole in-place turn's swept
   footprint. Use a feasible turn followed by holonomic fixed-heading travel.
5. If the starting footprint overlaps an obstacle, seek a short continuous
   retreat with non-increasing overlap for each connected 3D obstacle across
   height bands, no new obstacle regions, and continuous contact patches. Once
   the raw base centre becomes free it cannot enter an occupied cell again. With this geometry proof, even an occupied base centre
   can retreat; floor support remains mandatory. Rebuild the live map after
   each retreat and repeat if necessary, respecting the action/deadline budget
   and rejecting repeated blocked states. Without a verified exit, retain
   `navigation_invalid_start`. No start teleport or wall crossing is permitted.
6. Execute bounded pose increments (at most 0.5 m/s and 60 degrees/s), read back
   the actual pose after each simulator step, and verify arrival. Pruned paths
   reserve a 5 mm corridor against tiny feedback deviations at grid corners.
   Recheck elevated-target visibility at the actual final pose before reporting
   success; reaching a goal behind an occluder is a navigation failure.

`reached` means the selected XY was reached to grid/feedback precision.
`approached` means the executor reached a feasible projected endpoint, which may
still be too far for manipulation. `already_at_closest_approach` explicitly
reports that another identical request cannot make progress. Only these action
outcomes cross the RGB boundary; coordinates and distances remain private.

The original bounded candidate helper retains the reference algorithm for
historical diagnostics and exact staging (`max_snap=0`). Runtime navigation
uses `max_snap=None`. OmniGibson supplies floor support, current collisions and
an ideal pose follower; Habitat's native navmesh/follower is not running here.

Each runtime planning action has a shared cooperative 20-second / 256-visibility-query
budget across headings, with at most 64 candidate cells tested per elevated-target
heading. These limit work, not the geographical search domain or episode duration.
An already visible approach within the executor's 1.4 m manipulation check stops
heading refinement; it need not project onto the object's XY within one grid cell.
When the work budget expires, an existing validated plan can execute; otherwise
`navigation_search_exhausted` returns fresh RGB for policy recovery. This is not
proof that no path exists. Native calls are cooperatively checked between calls.

The MCP proxy lets `finish` reach the bridge while another tool is pending. The
bridge requests cancellation, then runs scoring on the simulator owner thread.
Ordinary tools and RGB downloads remain serialized. The episode still uses 50
agent act/look attempts without a wall-time deadline.

## Limits and evidence

This remains ideal kinematic base actuation, not a wheel controller or a complete
SE(2) / articulated-body motion planner. One fixed heading is used for each
translation leg, with a checked turn at its start. When the start lacks room
to turn, first follow a continuous route to a reachable clear turning position. Scene obstacles and robot collision hulls are matched in four height bands,
avoiding a base-width column through the head. Convex hulls within each band
remain conservative; carried-object and articulated motion planning remain
incomplete. A disconnected point can yield only an explicitly reported partial
approach in the start-connected region; it never permits crossing a closed door. Floor raster and
cell resolution also limit how closely a selected point can be reached.

`navigation_plans.jsonl`, `navigation_grids/*.npz`, `navigation_recovery.jsonl`,
`navigation_arrivals.jsonl` and `base_motion.jsonl` retain private geometry and
actual motion evidence. Arrivals record the selected target, planned goal,
actual endpoint and final target visibility.
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
- `scripts/validate_gt_navigation.py` compares candidate coordinates against
  pure functions extracted from the pinned reference and records the intentional
  scoring differences. It can
  replay archived failed navigation queries using original maps and private
  camera calibration/depth. This is offline execution validation, not task success.
- `scripts/probe_gt_navigation.py` is an explicitly scripted real-simulator
  regression using archived geometry. It does not run an LLM or count toward
  the benchmark. Its result is separate from the 100-task batch.
- `scripts/probe_navigation_wall.py` restores the archived task003 base pose,
  carries the same bowl and reuses the same sink pixel. It checks that the
  outside-wall goal is rejected and the new arrival has target visibility, four
  RGB views, scoring and a complete video. This is a component regression, not
  a full replay of preceding actions or an autonomous task-success measurement.

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
