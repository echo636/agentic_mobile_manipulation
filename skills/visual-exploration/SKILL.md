---
name: visual-exploration
description: Search and revisit places using current RGB, bounded turns, visible navigation points and conversation history.
---
# Visual exploration

Call initialize({}) first and inspect its front/back/left/right RGB. Continue with the four images returned by every act/look. These fixed-camera views share a capture timestamp and simulation step; acquisition does not turn the robot or perform a panorama sweep. No wrist images are available. Read relevant skills as needed.

Choose visible openings and target candidates from any of the four images. Only the latest returned refs and revision are valid act targets; previous images remain historical evidence. look(yaw_degrees, revision) turns the robot explicitly when a new physical orientation is useful, but it is not needed merely to collect surround RGB. To let an ongoing process advance, use act with primitive=wait and target=null, then inspect the returned images.

For movement use act(navigate_to, target={image_ref, point:[x,y]}, revision). Mark an object surface or visible floor beyond an opening. The executor follows only that pixel; it will not find a named room or object. A floor click requests that XY destination, with bounded projection if the exact cell is occupied; it does not keep a viewing standoff. An elevated object click requests a nearby approach position. A point near your feet may produce no movement; select a farther visible floor point when traveling. Do not try to act through a wall or remember an expired pixel.

Use your conversation history to keep track of which opening was inspected, which remains unexplored, what a return landmark looks like, and why a direction is useful. No explicit memory or recall tool is required. Prior observations do not give you simulator coordinates or a global map. On return, identify the landmark again in RGB.

Approach in short visible stages. Inspect each new view before another hop. If blocked, change the visual approach or viewpoint rather than repeating the same point. A room guess or empty image does not prove the entire scene has been searched.
