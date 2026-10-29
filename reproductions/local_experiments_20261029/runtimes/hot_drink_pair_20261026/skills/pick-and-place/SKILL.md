---
name: pick-and-place
description: Grasp, transport and place visible items; handle container contents, staging, doors and destination selection.
---
# Pick and place

1. Identify the visible source and intended destination. Prepare a closed destination while empty-handed **only if opening it leaves the source visible and retrievable**. Otherwise, first retrieve the source and stage it outside the door's swing; then open the destination and verify the item remains accessible. If the destination is unknown, compare a reconnaissance trip with transporting a movable carrier to a safe staging area on the first visit; choose from observed access and hand constraints. Searching empty-handed is useful when the path or staging is unsafe, not a prerequisite for every transport.
2. Choose a visible solid patch on the item. Use explicit `navigate_to` when an approach is needed, then reselect the item in fresh RGB. Manipulation does not move the base. A successful approach is enough when the item is identifiable and usable; another approach is not progress by itself. Read visual-exploration for floor versus object targets and recovery from cropping or occlusion.
3. Call `grasp`, then check `effect.grasped_category` against the intended item and destination. This reports the class picked by the pixel, not an instance or task score. Inspect RGB for whether the item or its support moved. Carry one item or one deliberately selected assembly with the default arm. Infer contents from visible evidence; private payload counts are unavailable.
4. Approach the destination if needed. Select its visible near body/interior for `place_inside`, or an empty horizontal support patch for `place_on_top`. A deep back-wall pixel may be out of reach even when another visible part of the same container is usable. After `out_of_reach`, first check for a nearer visible part of the intended target; otherwise navigate to a clear aisle and reselect it. Preserve the intended destination and relation.
5. Count placement progress only after `ok=true`, consistent with the selected object, destination and fresh RGB. A failed placement leaves the item pending; use the returned feedback and RGB to determine whether it remains held. Before picking another item, establish that the hand is empty. Treat operation success as local evidence, not independent task success.
6. Reinspect the source after each removal: newly exposed items are separate work. Keep each seen item's description, location, intended destination and placement evidence. Unknown contents remain unknown until inspected. Close a destination after its relevant items and uncertainties are resolved; preserve any process that requires it open.

Read `references/staging-and-doors.md` through read_skill before moving a loaded container, staging an item to free the hand, or recovering an item hidden by an open door.

Read `references/target-selection.md` for thin/curved items, baskets, food on supports, high shelves or ambiguous grasp pixels. Read `references/placement.md` for `attach`, `hang`, `place_under`, `place_next_to`, arranging multiple items, or a rejected placement on a particular support. These references preserve specialized cases without prescribing a scene-specific route.

`release` and `wait` require target=null. Opening/closing another object needs an empty default hand. The currently held object's own switch may be toggled by selecting its visible body; another object's switch needs an empty hand. Use the current tool catalog for the required fields.
