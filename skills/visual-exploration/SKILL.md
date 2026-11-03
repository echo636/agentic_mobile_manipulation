---
name: visual-exploration
description: Find objects, inspect storage and travel using visible landmarks and current RGB.
---
# Explore and travel

Inspect all four current views; they already cover front, back, left and right. `look` turns the robot when a different physical orientation is useful. Reselect targets from the latest images after moving. Pixels on mirrors or screens select that surface, not depicted objects; move to a direct view of a reflected object before targeting it.

For `navigate_to`, a visible floor pixel requests that floor location, with bounded projection if occupied and no viewing standoff. An elevated object pixel requests a nearby approach position. Select floor beyond a visible opening for travel through it. A point near your feet may produce no movement; a point beside a small object can put it below the cameras. The executor follows the pixel, not a named destination.

Break travel into visible stages. Record useful return landmarks, inspected places and unchecked openings in your notes. Stop navigating when the intended manipulation target becomes selectable. If a route fails, inspect the new views and choose a materially different approach.

Keep last confirmed identity and location separate from current visibility. After moving or staging an item, record its new location using visible landmarks. If identification later becomes uncertain, retain the earlier observation as a candidate; inspect that location and places affected by handling before searching other storage. A container's rim does not reveal its bottom: change viewpoint or stage it to expose the interior. Occlusion does not erase the last observed inventory. Opening storage requires an empty hand: stage and later retrieve held tools.

Track each storage compartment in notes as `unchecked`, `partly seen`, or `inspected`, using visible landmarks to distinguish it. Open doors and drawers separately; record only the interior actually seen. When expanding a search, choose an unchecked compartment before repeating inspected ones. Unopened or occluded compartments remain unresolved when reporting missing items.
