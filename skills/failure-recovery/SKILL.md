---
name: failure-recovery
description: Recover from incorrect visual targets, rejected actions and partial execution without oracle state or endless retries.
---
# Failure recovery

1. Inspect the error category and the returned fresh RGB. A failed operation may have moved or released an object.
2. Separate your hypotheses: wrong pixel/view, wrong object, insufficient approach, hand occupied, inaccessible opening, placement sampling failure, or simulator failure. Do not treat a hypothesis as a reported object state.
3. Review the previous tool feedback and images in your conversation. If explaining the recovery state the visible problem and the alternative to try. No plan or memory tool is needed.
4. Select a bounded alternative: a fresh point on the same clearly visible object, a closer approach, another viewpoint via look, opening a visibly closed container with an empty hand, or regrasping a visibly dropped item. If the target is at the image edge or hidden by the robot after an approach, use look or approach another visible landmark to recover a view before selecting it again; a pixel on nearby furniture is not a substitute for the target.
5. Never repeat an unchanged failed action more than once. After two alternatives fail, reassess the target and scene from RGB. Finish blocked with the actual limitation if no supported path remains.
6. Stale image/revision errors mean you must use the newest observation. Never replay an old pixel after movement.
   `out_of_reach` means repositioning is your decision: manipulation never navigates automatically. Call `act(primitive="navigate_to")` to a closer visible approach, inspect the returned RGB, then reselect the manipulation target. If an object approach remains insufficient, choose another visible landmark or viewpoint and inspect again. A floor pixel does not specify an exact base position.
7. Do not use evaluator output, hidden object names, arbitrary shell/Python or task reset to solve a failure.
8. Navigation may report `planning_error` when it cannot sample a valid stance near the selected object. Inspect the selected object and try at most one materially different visible approach target before reassessing. A sampling failure does not prove that the task is impossible. Navigation jumps to a checked endpoint; it does not search or execute an intervening walking route.
9. A rejected placement preserves the selected surface; it does not silently choose a different shelf. Inspect the target opening, clearance and carried object's extent, then choose a different visible point on the intended surface or another task-valid destination. Tool success still requires visual confirmation of the item's identity and intended relation.
