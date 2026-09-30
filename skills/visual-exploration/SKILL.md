---
name: visual-exploration
description: Search and revisit places using current RGB, bounded turns, visible navigation points and a visual frontier journal.
---
# Visual exploration

Inspect the current head and wrist views. Record visible openings, target candidates and unresolved areas. If the target is absent, use look(yaw_degrees, revision), bounded to +/-90 degrees per call, to inspect another direction. Positive yaw turns left. Turning moves the robot with the ideal motor executor and returns fresh RGB.

For movement use act(navigate_to, target={image_ref, point:[x,y]}, revision). Mark an object surface or visible floor beyond an opening. The executor follows only that pixel; it will not find a named room or object. A floor point near your feet may produce no movement; select a clearly farther visible surface. Do not try to act through a wall or remember an expired pixel.

Maintain a compact census with remember: which opening was inspected, which remains unexplored, what a return landmark looks like, and why a direction is useful. Retrieve notes with recall. Notes do not give you simulator coordinates or a global map. On return, identify the landmark again in RGB.

Approach in short visible stages. Inspect each new view before another hop. If blocked, change the visual approach or viewpoint rather than repeating the same point. A room guess or empty image does not prove the entire scene has been searched.
