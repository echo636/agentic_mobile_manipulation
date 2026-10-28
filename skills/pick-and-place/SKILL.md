---
name: pick-and-place
description: Prepare access, grasp the intended item, and place or attach it to the requested destination.
---
# Carry and place

1. Identify the item and destination. Open a visibly closed destination before grasping; open bins need no opening action. The default hand carries one item or supported assembly.
2. Grasp an exposed patch of the item's body, avoiding its support or carried contents. Check all current views if it is cropped. With target-locked approach, a selectable item can be grasped directly.
3. Confirm the carried item and its contents from RGB and feedback. If the wrong support or object moved, put it down safely before reselecting. Check that supported contents remain with the carrier.
4. Select the intended destination using the relation below. Inspect the returned result, then update completed and pending items. Close storage after dependent transfers if required.

For several items sharing a route, compare individual trips with loading a tray, board or container at the source. Grasping that carrier transports its supported contents together. Use `pour` to unload loose objects into a common vessel or onto a work surface while keeping the carrier in hand. Loose material requires a receiving vessel; distinct final placements still need distribution. Batch only when the saved travel outweighs loading, unloading and carrier handling, and keep the load stable and identifiable.

| Action | Select in current RGB |
|---|---|
| `place_inside` | Container body or open interior, clear of the held item and floor |
| `place_on_top` | The intended free support surface and shelf level |
| `place_under` | The fixture the item must be under |
| `place_next_to` | The reference object's body, rather than nearby ground |
| `attach` / `hang` | The compatible parent, mount, nail or hook for the held child |

For two items that must be beside each other, use the first item as the second's `place_next_to` reference. Their both being near other furniture is insufficient. If the reference is unreachable, navigate toward visible nearby floor and reselect its body.

`placement_yaw_degrees` rotates a `place_on_top` item about vertical relative to its current orientation; use null for other actions. Preserve the requested shelf and relation when choosing an alternative pose.

Open/close requires an empty hand. You can toggle the currently held object; toggling another object requires an empty hand. Release/wait takes a null target.

A failed placement may leave the object moved or released; use RGB and feedback to establish the current hand and object state. `place_inside: unsupported_relation` can mean the pixel hit the held item or a surface without a fillable volume. If still holding the item, reselect the container without grasping again.
