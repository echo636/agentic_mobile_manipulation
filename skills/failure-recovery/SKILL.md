---
name: failure-recovery
description: Diagnose failed actions and choose a concrete recovery while preserving task progress.
---
# Recover from feedback

Inspect the error and returned RGB. Failure may still move or release objects. Distinguish what the feedback reports from hypotheses about identity, access, hand state, geometry or tool readiness.

Choose an alternative that addresses the cause:

- Wrong, obscured or fixed target: inspect all current views; select an exposed part of the intended object. Change viewpoint if none shows it reliably.
- Stale image/revision: use the newest observation and reselect.
- Occupied hand or closed access: stage the held item safely, then open the required access.
- Placement sampling failure: inspect clearance and the object's extent; try another point on the intended surface or another task-valid destination.
- `local_adjustment_blocked/exhausted`: reassess target and approach. Tiny navigation corrections do not resolve incorrect identities or incompatible relations.
- `navigation_invalid_start`: report the base-executor limitation; changing destinations cannot fix it. For `navigation_stalled`, try one materially different route or viewpoint before reassessing.

Never repeat an unchanged failed action more than once. After two failed alternatives, reconsider the cause. Mark a blocked subgoal honestly and continue independent useful work. Finish blocked when no supported path remains; retain the original required result.

In legacy `explicit_navigation_v1`, `out_of_reach` requires navigation and a fresh selection. Follow the protocol reported by initialize.
