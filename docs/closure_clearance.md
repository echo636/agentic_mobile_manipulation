# Container placement and closing

`Inside` is not a promise that a lid can close. The pinned simulator checks
whether the object's AABB center lies in an annotated fillable volume. An object
can satisfy that predicate while extending into a door or lid's closed pose.
The executor must retain ordinary collision, support and containment checks.

Placement now prefers arrangements that leave closing space:

* Compute the closed articulation's moving-link bounds once, without stepping
  physics. Transform proposals with their fillable link for sliding drawers.
* Rank supported arrangements by closed-link overlap, existing contents and
  height. Bounds are a ranking heuristic, never proof of collision or no fit.
* Before accepting a new arrangement, briefly preview the real closed geometry
  and check actual PhysX contacts between moving links and the new assembly.
  Restore scene, particles and random state afterwards. This is private
  executor computation, not an executed close or an evaluation step.
* If the bounded preference cannot find a closable arrangement, retain a
  physically valid open-container placement. `Inside` remains valid for
  objects that can only be stored with the container open. Such fallback is
  explicit in placement diagnostics and does not guarantee a later close.

Closing still sets the annotated joint endpoints directly and checks the
official state after 30 control steps. It does not force a penetrating lid
closed or remove contents. `articulation_diagnostics.jsonl` records the joint
thresholds, positions and exact contact body paths before rollback can erase
the failure evidence.

This policy does not solve global packing, prove continuous-space completeness,
or rearrange already committed contents. Validation is tracked separately in
`operations/close_repair_20261022`; syntax checks alone are not physics or task
success evidence. The running full100 cohort retains its frozen executor.
