---
name: visual-exploration
description: Find objects, inspect storage and travel using visible landmarks and current RGB.
---
# Explore and travel

Inspect all four current views; they already cover front, back, left and right. `look` turns the robot when a different physical orientation is useful. Reselect targets from the latest images after moving.

For `navigate_to`, a visible floor pixel requests that floor location, with bounded projection if occupied and no viewing standoff. An elevated object pixel requests a nearby approach position. Select floor beyond a visible opening for travel through it. A point near your feet may produce no movement; a point beside a small object can put it below the cameras. The executor follows the pixel, not a named destination.

Break travel into visible stages. Record useful return landmarks, inspected places and unchecked openings in your notes. Stop navigating when the intended manipulation target becomes selectable. If a route fails, inspect the new views and choose a materially different approach.

Before declaring an item missing, inspect plausible closed storage and remaining likely areas. Opening storage requires an empty hand: stage a held tool on a suitable support and retrieve it afterward. Distinguish an inspected location from an occluded or unopened one. Repeated views of the same area do not establish complete search coverage.
