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
   proposing the closest feasible points to the selected XY. Ground navigation
   compares complete routes within 10 cm of the closest proposed point, using
   translation time plus rotation time; elevated approaches retain the closest
   target-distance tier. There is no 0.75 m projection radius, ring sampling, or
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
   footprint. Endpoint selection uses the same footprint reserve as forward
   execution, including the required final viewing orientation. Each candidate
   also requires checked local forward arrival and departure paths (at least
   30 cm net displacement, optionally staged by 10 or 20 cm). A legal static
   pose is insufficient: outward-facing poses that can only be entered by
   reversing, and inward-facing poses that cannot leave, are both rejected.
   These are local certificates under the current robot/load geometry, not a
   guarantee that every later target remains reachable after manipulation.
5. If the starting footprint overlaps an obstacle, seek a short continuous
   retreat with non-increasing overlap for each connected 3D obstacle across
   height bands, no new obstacle regions, and continuous contact patches. Once
   the raw base centre becomes free it cannot enter an occupied cell again. With this geometry proof, even an occupied base centre
   can retreat; floor support remains mandatory. Rebuild the live map after
   each retreat and repeat if necessary, respecting the action/deadline budget
   and rejecting repeated blocked states. Without a verified exit, retain
   `navigation_invalid_start`. No start teleport or wall crossing is permitted.
   This contact-shedding retreat is the explicit, logged exception to forward
   driving: turning while already overlapping an obstacle may be unsafe.
6. Execute bounded pose increments (at most 0.5 m/s and 60 degrees/s), read back
   the actual pose after each simulator step, and verify arrival. Normal motion
   first turns in place to face the path segment, then advances; it stops to
   turn at corners and only turns at arrival if the selected endpoint heading
   differs from the last travel segment.
   Certify every segment and turn with orientation-aware height-layer collision
   geometry. If the existing polyline cannot be driven, use a bounded eight-heading
   position/heading search (forward steps and in-place turns, no strafing).
   At a geometric dead end in that lattice, also try bounded shallow-angle
   forward connections: one cell of lateral offset over several forward
   cells, with at most 65 cm of translation. Each connection checks its
   initial turn, complete translation and final turn. Intermediate poses are
   retained for execution, so a narrow exit requiring a small turn is not
   reconstructed as sideways motion. The existing time/state limits remain.
   Uncached continuous-heading translations erode only the crossed raster
   region with the full obstacle halo, using the same kernels and supercover
   checks as full-floor erosion.
   Checked entry/exit legs retain the measured and final continuous headings
   where a narrow aisle requires driving straight before turning.
   Heading/staging proposals do not execute an independent pre-turn. One route
   is planned from the measured initial pose. Ground clicks and elevated
   approaches also consider a final heading aligned with arrival, if entry
   and exit clearance still hold. Elevated targets must remain visible at
   that new heading with the same camera margin and occlusion checks. The
   elevated refinement also tests headings 15 and 30 degrees to either side
   of arrival, plus a target-facing heading, to cover gaps between the four
   cameras' margin-cropped frusta without a full return turn. These proposals
   share the same two-second refinement budget and clearance checks. The
   original validated heading stays available; neither mode requires restoring
   the departure yaw. Complete-route motion time chooses among these options.
   Bounded collision-checked shortcuts remove detours only when combined
   translation/rotation time decreases. Execution reuses the selected route
   when the measured start has not changed, and still checks every command.
   A relaxed XY geodesic guides that search around walls. All motion phases
   share the caller's remaining simulation-step budget. Pruned paths
   reserve a 5 mm corridor against tiny feedback deviations at grid corners.
   Recheck elevated-target visibility at the actual final pose before reporting
   success; reaching a goal behind an occluder is a navigation failure. The local
   departure certificate is checked again at the measured arrival pose.

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
Additional arrival-heading certification is bounded by 2 seconds. Complete-route
comparison tests at most six candidates, within a shared 10 seconds and up to
2 seconds / 10,000 heading states per candidate. A validated incumbent may win
when the refinement budget expires. Its objective uses 0.5 m/s translation and
60 degrees/s rotation; it is **not a globally shortest continuous route**.
When the work budget expires, an existing validated plan can execute; otherwise
`navigation_search_exhausted` returns fresh RGB for policy recovery. This is not
proof that no path exists. Native calls are cooperatively checked between calls.

The MCP proxy lets `finish` reach the bridge while another tool is pending. The
bridge requests cancellation, then runs scoring on the simulator owner thread.
Ordinary tools and RGB downloads remain serialized. The episode still uses 50
agent act/look attempts without a wall-time deadline.

## Limits and evidence

This remains ideal kinematic base actuation, not a physical wheel controller or a
complete continuous/articulated-body motion planner. Forward route certification
and heading search have a separate cooperative 10-second / 20,000-state limit
per execution leg. A blocked or exhausted search fails explicitly; it never
falls back to sliding sideways. The discrete heading fallback can miss paths
requiring finer orientations. Up to 2 mm of endpoint feedback correction is
permitted to prevent pose-setter drift. Scene obstacles and robot collision hulls are matched in four height bands,
avoiding a base-width column through the head. Convex hulls within each band
remain conservative. The held object and supported carried contents contribute
their collision hulls to the moving footprint, with arm posture held during
travel. This does not model deformable loads, wheel dynamics or moving arm
trajectories. Stricter turn clearance may reject a goal reachable by the old
fixed-heading translation. A disconnected point can yield only an explicitly reported partial
approach in the start-connected region; it never permits crossing a closed door. Floor raster and
cell resolution also limit how closely a selected point can be reached.

`navigation_plans.jsonl`, `navigation_grids/*.npz`, `navigation_recovery.jsonl`,
`navigation_forward_plans.jsonl`, `navigation_arrivals.jsonl` and `base_motion.jsonl` retain private geometry and
actual motion evidence. Arrivals record the selected target, planned goal,
actual endpoint, final target visibility and the local departure certificate.
Plans include entry/exit rejection counts and complete-candidate comparisons;
saved navigation maps also include the exact support raster.
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
