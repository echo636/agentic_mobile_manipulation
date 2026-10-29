---
name: failure-recovery
description: Recover from incorrect visual targets, rejected actions and partial execution without oracle state or endless retries.
---
# Failure recovery

1. Inspect the error category and the returned fresh RGB. A failed operation may have moved or released an object.
2. Separate your hypotheses: wrong pixel/view, wrong object, insufficient approach, hand occupied, inaccessible opening, placement sampling failure, or simulator failure. Do not treat a hypothesis as a reported object state.
3. Review the previous tool feedback and images in your conversation. If explaining the recovery state the visible problem and the alternative to try. Use conversation history in the default profile; when planning tools are available, update the affected goal or recovery note after a material change.
4. For `out_of_reach`, inspect for a nearer visible part of the same intended object; if none is usable, explicitly navigate to a different clear approach and reselect from fresh RGB. For door-hidden staged items or blocked carrier contents, read pick-and-place/references/staging-and-doors.md. Select a bounded alternative: a fresh point on the same clearly visible object, a closer approach, another viewpoint via look, opening a visibly closed container with an empty hand, or regrasping a visibly dropped item. If the target is at the image edge or hidden by the robot after an approach, back away toward visible free floor before selecting it again; a pixel on nearby furniture is not a substitute for the target.
   After `fixed_object`, locate the selected point in its RGB image and check which surface it covers. If it lies on a wall, frame, support or a gap in a thin item, select a solid visible patch of the intended item in fresh RGB. When that patch is occluded, change the approach to expose it before another grasp.
   After `hand_occupied` on `open` or `close`, use a task-valid `place_on_top` or `place_inside` to empty the hand, inspect the result, then retry the door or lid action on a fresh RGB point. This error is a recoverable precondition while the held item can still be staged.
5. Never repeat an unchanged failed action more than once. After two alternatives fail, reassess the target and scene from RGB. Finish blocked with the actual limitation if no supported path remains.
6. Stale image/revision errors mean you must use the newest observation. Never replay an old pixel after movement.
7. Do not use evaluator output, hidden object names, arbitrary shell/Python or task reset to solve a failure.
8. `navigation_invalid_start` is a base-executor limitation, not an unreachable destination. Repeatedly selecting different destinations cannot fix it. Report the limitation instead of consuming the episode on identical navigation failures. For `navigation_stalled`, inspect the latest RGB and try at most one materially different route or viewpoint before reassessing.
9. A rejected placement preserves the selected surface; it does not silently choose a different shelf. Inspect the target opening, clearance and carried object's extent, then choose a different visible point on the intended surface or another task-valid destination. Tool success still requires visual confirmation of the item's identity and intended relation.
