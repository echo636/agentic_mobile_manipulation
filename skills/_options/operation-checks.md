
## Before each action: general operation checks

These are physical preconditions of the actions themselves. They say nothing about which task step comes next.

- **grasp**: select the item's own body, not the counter, shelf or floor under or beside it. If the item is behind a closed door, drawer or glass panel, open that first. The hand must be empty.
- **taking an item out of an appliance, cabinet or other enclosure**: after opening it, first stand in front of the open door so the item itself is visible in the image, then grasp the item. A point on the door, its frame or an empty shelf grasps nothing.
- **open / close / toggle an object you are not holding**: the hand must be empty. Select the door front, drawer front, handle, lid or switch itself, not the outer frame or a counter.
- **place_inside**: the container or appliance must already be open, and the point must be on its open interior or opening.
- **place_on_top**: the point must be on a free, upward-facing surface with room for the held item.
- **cut**: hold a cutting tool first, then select the item to cut itself, not the board under it.
- **pour / scoop**: hold the source vessel or tool. The receiver must be open and within reach.
- **After a failure**: compare the fresh RGB with what you expected before retrying. Change the point, view, stance or action instead of repeating the same request.
