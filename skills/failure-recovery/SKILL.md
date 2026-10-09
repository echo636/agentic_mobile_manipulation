---
name: failure-recovery
description: Recover from incorrect visual targets, rejected actions and partial execution without oracle state or endless retries.
---
# Failure recovery

1. Inspect the error category and the returned fresh RGB. A failed operation may have moved or released an object.
2. Separate your hypotheses: wrong pixel/view, wrong object, insufficient approach, hand occupied, inaccessible opening, placement sampling failure, or simulator failure. Do not treat a hypothesis as a reported object state.
3. Review the previous tool feedback and images in your conversation. If explaining the recovery state the visible problem and the alternative to try. No plan or memory tool is needed.
4. Select a bounded alternative: a fresh point on the same clearly visible object, a closer approach, another viewpoint via look, opening a visibly closed container with an empty hand, or regrasping a visibly dropped item. If the target is at the image edge or hidden by the robot after an approach, back away toward visible free floor before selecting it again; a pixel on nearby furniture is not a substitute for the target.
5. Never repeat an unchanged failed action more than once. After two alternatives fail, reassess the target and scene from RGB. Finish blocked with the actual limitation if no supported path remains.
6. Stale image/revision errors mean you must use the newest observation. Never replay an old pixel after movement.
7. Do not use evaluator output, hidden object names, arbitrary shell/Python or task reset to solve a failure.
8. `navigation_invalid_start` is a base-executor limitation, not an unreachable destination. Repeatedly selecting different destinations cannot fix it. Report the limitation instead of consuming the episode on identical navigation failures. For `navigation_stalled`, inspect the latest RGB and try at most one materially different route or viewpoint before reassessing.
9. A rejected placement preserves the selected surface; it does not silently choose a different shelf. Inspect the target opening, clearance and carried object's extent, then choose a different visible point on the intended surface or another task-valid destination. Tool success still requires visual confirmation of the item's identity and intended relation.
