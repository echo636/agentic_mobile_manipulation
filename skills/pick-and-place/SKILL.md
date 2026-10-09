---
name: pick-and-place
description: Prepare a destination, visually grasp and carry an object, then place or attach it using RGB.
---
# Pick and place

1. Identify the item and intended destination in RGB. If the destination is not visible, search for it first or remember the item's visual location before exploring.
2. If the destination visibly has a closed door/lid, approach and open it before grasping the item. Do not call open just because an object is a container; an open bin may have no opening mechanism.
3. Approach the item by marking it in the current RGB. Reinspect after navigation; once its body is clearly visible, call `grasp` on a clearly exposed patch of that body rather than repeatedly navigating toward the same point. For a jar, use the middle of its visible front face; for a plate carrying food, use a visible plate rim away from the tabletop. Grasp has a bounded automatic approach to the selected point when needed; repeated public navigation may deliberately keep a small floor object at a visible standoff. If the body is cropped or hidden, change to a viewpoint that restores visibility. Do not mark the supporting table/floor.
4. Inspect the new four-camera RGB and action feedback for evidence of the result. Compare `contained_rigid_objects` with the intended carried assembly: a plate with one pizza should carry that pizza, while an unexpectedly large group means the clicked support may have been grasped. In that case, release and select the intended item's visible body in fresh RGB before traveling. Gripper visibility may be limited; do not infer held-object truth from an unavailable wrist camera. Maintain this as your belief. Carry only one item with the default arm.
5. Approach the destination using fresh RGB points. Once the destination is clearly visible and reachable, act on it instead of navigating closer. Mark the visible container body/interior for place_inside or the support surface for place_on_top.
6. Inspect the returned RGB to verify visible placement and, if visible, the released object. If uncertain, change viewpoint rather than inventing an Inside/OnTop flag.
7. Record completed items and remaining ones with visual descriptions. Repeat, then close containers if the instruction requires it.

For an attachment instruction, grasp the child, navigate to a visible approach to the compatible parent if needed, then use `attach` within reach. Select a visible point on the parent in the latest RGB, such as the tripod body or mount for a camera. If the executor reports `out_of_reach`, change viewpoint or select a different visible navigation approach before retrying. The executor aligns the pair's attachment links and reports whether the attachment state persisted after settling. Inspect the returned RGB and feedback before finishing; the independent evaluator decides task success.

release and wait require target=null. open/close operations require an empty default hand. You may toggle the object you are currently carrying by selecting it in current RGB; toggling a different object requires an empty hand. Failed placement restores the pre-action state when recovery succeeds. Inspect post-failure RGB and error feedback before retrying; do not assume either an empty hand or a successful placement.

For place_on_top, click the actual intended empty support surface and shelf level. Optional placement_yaw_degrees rotates the carried item about vertical relative to its current orientation; use it to arrange items side by side, then verify from a clear RGB viewpoint. A neighboring shelf or a different part of a large support is not evidence that the intended arrangement succeeded. Use null for this option on other actions.

For an instruction to put an item under a fixture, carry the item and select a visible point on that fixture for `place_under`. The executor samples a pose with the simulator's `Under` relation and checks it after settling. A visually nearby floor placement may not satisfy `Under`; inspect the result and continue checking the whole task.

For an instruction to place an item next to a fixture or tree, carry it and select a visible point on that same parent for `place_next_to`. The executor searches floor poses around the selected point and accepts one only if the simulator's `NextTo` relation is true after settling. Select the same parent for all items that must be together; inspect each action's result and the final RGB.
