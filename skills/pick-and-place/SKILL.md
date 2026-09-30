---
name: pick-and-place
description: Prepare a destination, visually select and grasp one object, carry it and verify placement using RGB.
---
# Pick and place

1. Identify the item and intended destination in RGB. If the destination is not visible, search for it first or remember the item's visual location before exploring.
2. If the destination visibly has a closed door/lid, approach and open it before grasping the item. Do not call open just because an object is a container; an open bin may have no opening mechanism.
3. Approach the item by marking it in the current RGB. Reinspect after navigation; mark its body again for grasp. Do not mark the supporting table/floor.
4. Inspect wrist images and action feedback to decide whether the item is held. Maintain this as your belief. Carry only one item with the default arm.
5. Approach the destination using fresh RGB points. Mark the visible container body/interior for place_inside or the support surface for place_on_top.
6. Inspect the returned RGB to verify placement and the empty gripper. If uncertain, change viewpoint rather than inventing an Inside/OnTop flag.
7. Record completed items and remaining ones with visual descriptions. Repeat, then close containers if the instruction requires it.

release and wait require target=null. open/close/toggle operations require an empty default hand. Low-level sampling can fail or release the object: inspect the post-failure image before retrying grasp or placement.
