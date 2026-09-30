# Simulator and placement repair validation

All attempts, including failed repair experiments, are recorded in `operations/executor_fixes_20261001`, with public evidence at `reports/executor_fixes_20261001/index.html`. Source snapshots and output paths are immutable per diagnostic. Original 32-task scores remain 2/32 passed. These probes are not replacement benchmark episodes.

## Startup

- `goal_grounding.py` interns identical literals within one BDDL grounding operation, preserves all Cartesian options, duplicate literals, stable option ordering, and scope. Predicate results are cached only within one read-only evaluation pass. The official TaskMetric formula is unchanged. Three differential tests ran against the actual pinned BDDL; local CPU runs skip those tests when BDDL is absent.
- `omnigibson_backend.py` handles a recognized legacy robot-controller state and validates task instance bindings against scene templates. The bringing-in-wood partial scene contains firewood while the pinned task/instance require plywood; the supplied full template has consistent bindings and is selected explicitly. More full-scene context is a recorded protocol difference, not a hidden asset edit.
- Real startup + four fixed RGB views passed for assembling_gift_baskets, bringing_in_wood, bringing_water and storing_food. This does not establish task success. Water still spends about 402 seconds constructing its environment. Future manifests separate a 1200-second startup budget from the unchanged 1800-second controller budget, with 3300 seconds total unit budget. Old frozen manifests are not rewritten.
- The extended unmodified gift startup also produced SIGSEGV. Profiling establishes repeated BDDL work; it does not establish the native signal's full root cause.

## Placement

The adapter previously discarded the selected support point after mapping it to an object. It now samples a bounded neighborhood of the chosen surface using upstream cuboid collision checks. Failed placements restore the pre-action simulation state and grasp. Successful candidates are released at the destination without advancing physics at the old hand location, then settled and checked. Container placement uses the official Inside volume sampler with a bounded budget and rollback.

The rack diagnostic established a second issue: a shoe on a lower shelf has `Touching=true`, the rack below, and the same rack above. Official OnTop requires that the target is absent above, so it rejects this physical shelf placement. The task's independent BDDL instead requires contact with the rack, no floor contact, and pair adjacency.

`place_on_top` therefore accepts either official OnTop or verified physical support on the selected surface: actual contact, released grasp, downward ray hitting the selected object, upward surface normal, small bottom gap, the selected shelf height/neighborhood, and low residual speed. No task goal is consulted or forced true. Benchmark predicates and scoring are unchanged; a successful motor action is still not task success.

## Remaining stability work

An experimental robot/held-object collision filter passed a shoe grasp but invalidated a PhysX tensor view when its API schema was created at runtime for a toaster. The complete filter change was removed from the final executor. Failed attempts and stack traces remain in the journal. The final-source kitchen regression reproduced the original NaN at the same food_processor grasp after a successful Inside placement. A separate transactional recovery probe now tests restoring the entire pre-grasp state, verifying finite robot pose and fresh four-camera RGB, and returning a failed `physics_instability` action. The underlying grasp instability is not declared solved.

Protocol identifier: `rgb_agent_ideal_executor_v6_selected_surface_support`. All private geometry and checks remain within the executor or offline diagnostic records. The model still receives four RGB images, its selected pixel target, and bounded action feedback; it does not receive scene truth or independent task scores.
