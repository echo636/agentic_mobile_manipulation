# Native symbolic navigation in the mainline RGB backend

`navigate_to` resolves the selected current RGB pixel to its visible object and
calls `SymbolicSemanticActionPrimitives.apply_ref(NAVIGATE_TO, object)`, preserving
the upstream retry default. The native sampler checks candidate base poses with
CuRobo DEFAULT collision checking and ARM reachability. The Symbolic endpoint
sets the robot pose directly and yields settling actions. There is no A* route,
waypoint following, swept-path check, or custom GT fallback. A successful call
does not establish walking-path connectivity or whole-task success.

The selected pixel identifies an object, not an exact navigation coordinate.
Choose a visible object or fixture in the desired area; a floor pixel does not
request its exact XY. The next four RGB views must be inspected and the target
selected again before manipulation. Manipulation remains explicit and does not
navigate automatically. `look` retains bounded in-place turns.

## Compatibility and carry

The pinned Symbolic constructor omits its planner even though navigation needs
one. `official_navigation.py` supplies native DEFAULT and ARM planners, current
CUDA-device arguments and sufficient initial scene-mesh cache capacity. Existing
instance-scoped adapters fix the inherited endpoint keyword and world/holonomic
anchor frame mismatch. These adapters were carried from local official executor
commit `a3a7cd98a3872c04f23ac555320567efb7cfe4f0`; native candidate order, collision/IK
and endpoint settling remain in use. Upstream source files are not edited.

The navigation primitive has its own instance and reads the mainline carry state.
Native collision checking receives the held root as an attached object. Our
existing controlled carry projects the root and its rigid/particle contents to
the new robot pose before and after environment steps, including cancellation
after a native teleport. Native attached-object checking is not a full collision
check of the transitive payload assembly. This backend is explicitly a hybrid,
not the pure official manipulation branch or a physical-control submission.

## Records and validation

`official_navigation_initialization.json` records native source hashes and the
planner adapters. `symbolic_navigation.jsonl` records each call's target, retries,
poses, environment steps, internal physics ticks, status and error. These are
executor-private. Episode provenance labels `path_executed=false` and
`path_connectivity_checked=false`. The old ideal-path-distance field is omitted
from evaluation; time reflects executed simulation steps, not travel duration.

CPU tests cover native method delegation, frame/device/cache compatibility,
selected-object dispatch, payload synchronization and failure/budget cleanup.
They do not establish simulator task success. Batch-specific simulator evidence
belongs in operations records; existing frozen experiments are unchanged.
