# GPU articulation sleep crash

The sampled garlic scene repeatedly crashes in native PhysX after articulations
enter sleep. The catalog adapter now keeps articulations awake on the validated
Isaac Sim 5.1.0.0 / omni.physx 107.3.26 GPU runtime. The hook runs before scene
construction, survives resets and state restoration, and is removed at close.
Ordinary rigid-body sleeping and official BDDL evaluation remain unchanged.

Evidence is under `operations/long_horizon_full_v2_20261022` in the shared root:

- R289, R295, and R297 all fail on native physics step 47, despite changing the
  root-velocity API in the latter two diagnostics.
- R303 adds read-only sleep-state tracing. At index 46 (the failing 47th step),
  `burner_mdanhg_0` (11 links) and `salt_shaker_105` (3 links) have just slept.
- R299 disables natural and explicit articulation sleeping before loading the
  identical scene. Native open, 120 physics steps, and subsequent RGB pass.
- R300 applies the same intervention to the archived failing model prefix.
  All 19 calls complete, grounding remains within the prescribed tolerance,
  and the final microwave open returns `ok=true`. Both runs pass all 61 initial
  conditions. These are diagnostic results, not autonomous task successes.

The installed GPU library's `PxCreateFoundation` version check is `0x05060100`,
confirming PhysX 5.6.1. Public tag `107.3-physx-5.6.1` contains the missing
`pathToRootOffset`, `numPathToRoot`, `offsetSlop`, and `cfmScale` writes described
in [PhysX issue 551](https://github.com/NVIDIA-Omniverse/PhysX/issues/551) and
[PR 552](https://github.com/NVIDIA-Omniverse/PhysX/pull/552). GPU update code copies
the entire link record, and path-to-root readers use those fields as indices.
This is a strong candidate for the underlying defect, not a proven instruction
trace from our stripped binary. The small upstream-style examples R301/R304
did not reproduce an error in 300 steps; preserve that negative evidence.

This policy is a workaround, not a native library patch. Keeping articulations
awake changes the simulator's sleep optimization and may affect settling and
runtime cost. Articulation additions/removals may still trigger other failures.
Each run records `articulation_sleep_policy.json`, including version, initial
articulation states and redirected explicit sleep requests. A full autonomous
task must still pass the independent official evaluator.
