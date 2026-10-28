# Placement and approach repairs (2026-10-21)

A recorded food task found a 2.186 m approach whose repeated turns totaled
32.583 rad. Its estimated 35.49 simulated seconds could not fit a 600-step
(20 simulated second) local allowance. The follower moved partway and returned
`action_timeout`. Separately, the same task rejected all chicken/box placement
proposals. Neither result proved physical infeasibility.

## Changes

- The shared roadmap uses 16 forward stride directions, with cell supercovers,
  angle-dependent turn costs and an independent swept recheck. Continuous-pose
  search remains the fallback. This is not a continuous optimality guarantee.
- Certified routes reserve estimated motion steps plus two feedback steps per
  phase and ten settling steps. The original 600-step atomic allowance remains
  available. The caller's remaining limit, cancellation, collision and stall
  checks remain binding. Insufficient caller budget fails before route motion
  with `local_control_budget`. Route rendering time is tracked separately from
  the 300-second non-motion work limit.
- Unladen items may try side poses; absent asset metadata, principal faces are
  proposals that still require collision, support and official Inside checks.
  Rigid/particle payloads retain the existing gravity-preserving policy.
- Support-derived proposals check the predicted resting center against the
  volume, excluding the temporary 25 mm release clearance from admission.
  Initial collision and final official containment checks remain unchanged.
- Unladen support proposals include the cavity center and give each orientation
  a supported trial before spending the candidate cap on arbitrary heights.
  No target IDs, task predicates or recorded coordinates enter the algorithm.

## Evidence and limits

Private real-simulator comparison: operations/placement_route_box_repair_20261021.
Baseline source f77d59a. Same saved chicken/box fixture: baseline failed all108
candidates (83 initial collisions,25 outside volume); the production candidate
accepted trial44 (41 collisions,2 outside), then passed50 settling environment
steps and60 extra environment steps with official Inside still true.

The recorded route geometry changed from35.49s/2.186m to27.92s/2.150m.
Execution from the recreated pre-step3 base/carry pose, with fresh live collision
geometry, reached the endpoint in893 environment steps (883 motion+10 settling),
with1.62 micrometre position residual. This is not an exact full-world snapshot
replay. Total wall time was329.8s, demonstrating why rendering time cannot serve
as a fixed route-feasibility limit. The route still turns extensively; optimal
routes and elimination of all search-budget failures are not established.

Narrow coordinator checks:24 local-controller tests,20 forward-navigation tests
and7 sampler tests passed.22 dependency-dependent tests were skipped on that
interpreter. One pre-existing stale test expected a superseded planning event;
it also failed against the baseline and now checks the current preparation
budget stage. The real simulator comparisons above are the physical evidence.

Full autonomous RGB MCP task reruns are tracked separately in this batch;
component success must not be reported as whole-task success.
