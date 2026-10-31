---
name: visual-exploration
description: Search and revisit places using current RGB, bounded turns, visible navigation points and conversation history.
---
# Visual exploration

Call initialize with the fields required by the current tool catalog first and inspect its front/back/left/right RGB. Continue with the four images returned by every act/look. These fixed-camera views share a capture timestamp and simulation step; acquisition does not turn the robot or perform a panorama sweep. No wrist images are available. Read relevant skills as needed.

Choose visible openings and target candidates from any of the four images. Only the latest returned refs and revision are valid act targets; previous images remain historical evidence. look(yaw_degrees, revision) turns the robot explicitly when a new physical orientation is useful, but it is not needed merely to collect surround RGB. To let an ongoing process advance, use act with primitive=wait and target=null, then inspect the returned images.

For movement use act(navigate_to, target={image_ref, point:[x,y]}, revision). Mark an object surface or visible floor beyond an opening. The executor follows only that pixel; it will not find a named room or object. Check that the chosen point lies on the intended visible surface. Approaching an opening or container exterior can expose an item deeper inside without bringing that item into reach; when it appears, select its own visible body for the next approach. For small floor items, navigate toward the visible item body to retain a useful viewing distance. A floor click is a footfall destination: clicking directly under an item can put it below the camera. Use clear floor points for travel through openings or along an aisle. A floor point near your feet may produce no useful movement; select a point that visibly advances the intended route. Do not try to act through a wall or remember an expired pixel.

Use your conversation history to keep track of which opening was inspected, which remains unexplored, what a return landmark looks like, and why a direction is useful. No explicit memory or recall tool is required. Prior observations do not give you simulator coordinates or a global map. On return, identify the landmark again in RGB.

Approach in short visible stages. Inspect each new view before another hop. If blocked, change the visual approach or viewpoint rather than repeating the same point. A room guess or empty image does not prove the entire scene has been searched.

For a recessed container, distinguish its outside surface from its opening. When an approach reveals only an exterior wall, rim or lid, use visible clear floor to move around that obstruction toward a view into the opening, then inspect the new four views. Turning in place changes camera direction but cannot remove an obstruction between the camera and contents. Opening an already raised lid may leave the view unchanged; the next useful action can be a translation rather than another open or look. Keep unseen contents unresolved until a view into the accessible interior supplies evidence, or concrete access failures establish the limitation.

Before each movement, name the missing evidence: access to another area, a visible item patch, an unobstructed opening, or a reachable manipulation surface. Inspect all four current views first. If one already provides that evidence, use its image_ref directly. A turn is useful only when it changes orientation enough to resolve something those views do not already resolve.

After navigating, compare with the previous view. If the intended object is visible and usable, try the intended manipulation instead of another approach. After an out_of_reach result, check for a nearer visible part of the same valid target before moving again; otherwise choose a new clear approach. If the object drops below the image edge, back or sidestep onto visible free floor until its body is framed. Use the resulting viewpoint as a visual landmark for the next similar operation. A successful navigation is not evidence that the object was manipulated.
