---
name: visual-exploration
description: Search and revisit places using current RGB, bounded turns, visible navigation points and conversation history.
---
# Visual exploration

Call start_observation({}) to queue a four-camera capture without waiting, then get_observation(job_id). Read relevant skills while capture runs. When passed, inspect front/back/left/right RGB; these are simultaneous fixed-camera views, not a robot turn or panorama sweep. All four share a capture timestamp and simulation step. No wrist images are available.

Choose visible openings and target candidates from any of the four images. Only current refs are valid act targets. If a completed job is marked stale, request a new capture; previous images remain historical evidence. look(yaw_degrees, revision) still turns the robot explicitly when a new physical orientation is useful, but it is not needed merely to collect surround RGB.

For movement use act(navigate_to, target={image_ref, point:[x,y]}, revision). Mark an object surface or visible floor beyond an opening. The executor follows only that pixel; it will not find a named room or object. A floor point near your feet may produce no movement; select a clearly farther visible surface. Do not try to act through a wall or remember an expired pixel.

Use your conversation history to keep track of which opening was inspected, which remains unexplored, what a return landmark looks like, and why a direction is useful. No explicit memory or recall tool is required. Prior observations do not give you simulator coordinates or a global map. On return, identify the landmark again in RGB.

Approach in short visible stages. Inspect each new view before another hop. If blocked, change the visual approach or viewpoint rather than repeating the same point. A room guess or empty image does not prove the entire scene has been searched.
