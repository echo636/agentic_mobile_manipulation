# Public model

The request has exactly two keys: `public_model` and `proposal`. The model has
seven integer fields (Booleans are not integers here):

| Field | Meaning and supported values |
|---|---|
| `count` | One or two raw items, initially on the left surface |
| `capacity` | Box capacity, 1–7 units |
| `travel_cost` | Cost of crossing between stations, 1–20 |
| `tool_start`, `box_start` | Initial surface: 0 left, 1 right |
| `tool_goal`, `box_goal` | Required final surface: 0 left, 1 right |

The robot starts left with an empty hand; the box starts empty. Surfaces have
unlimited staging space. Each raw item takes two capacity units. Cutting one
raw item produces two products of one unit each. The tool occupies one unit.
Cutting conserves capacity. The hand holds one loose item, the tool, or the box;
carried box contents remain inside.

The terminal state requires all products directly on the right surface, tool
and empty box on their specified surfaces, and an empty hand. The robot's final
station is unrestricted. There are no additional intermediate obligations.

Every action except `move` costs one. Actions are single strings:

- `move`: cross with whatever is held.
- `pick_raw_surface`, `pick_product_surface`: take one matching item from the
  current surface into the empty hand.
- `put_raw_surface`, `put_product_surface`: put the matching held item on the
  current surface and free the hand.
- `pick_raw_box`, `pick_product_box`: take one matching item from a box resting
  at the current station into the empty hand.
- `put_raw_box`, `put_product_box`: put the matching held item into a box resting
  at the current station, respecting capacity, and free the hand.
- `pick_tool`: take the tool from the current surface or a box resting there
  into the empty hand.
- `put_tool_surface`, `put_tool_box`: put the held tool on the current surface,
  or into a box resting there with one free unit, and free the hand.
- `pick_box`, `put_box`: take the box into an empty hand or place it on the
  current surface. Contents remain inside.
- `cut`: while holding the tool, convert one raw item on the current surface
  into two products there; retain the tool.
- `cut_in_box`: the same conversion inside a box resting at the current station.
- `pour_all`: while holding a nonempty box, transfer **all** contents, including
  any tool, onto the current surface. The empty box remains held.

The proposal is a list of at most 100 action strings. These guaranteed symbolic
operations do not establish a physical robot's grasp, capacity, stability,
pouring or cutting capabilities. Encode only tasks that actually use this model.
