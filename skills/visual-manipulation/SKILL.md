---
name: visual-manipulation
description: Complete manipulation through RGB, callable workflow skills, bounded actions and visible verification.
---
# Visual manipulation workflow

1. Call initialize({}) first and inspect its front/back/left/right RGB. It returns the prepared episode's current view without resetting the scene. The fixed cameras show one simulation state without rotating the robot. Perceive requested objects yourself; no oracle inventory or state is available.
2. Read visual-exploration for search, pick-and-place before carrying, failure-recovery after failed actions. These documents guide your next action; they do not execute actions automatically.
3. Choose one next action from visible evidence. The replay records your actual public messages and tool calls verbatim; there is no extra decision form or required language. Do not claim unseen success.
4. Select a point in the latest returned image_ref (any direction) with the latest revision. Approach, then select again from the RGB returned by that action before grasp/open/place/toggle. The executor cannot select objects for you.
5. Execute one bounded action and inspect its returned RGB. Use normal conversation history to track moved items and visual landmarks. Explicit plan and memory tools are unavailable in the default skills profile; no plan-writing stage is required.
6. Compare fresh visual evidence with the expected effect. A tool completing is only an operation report. Change the viewpoint or approach when uncertain, and read recovery guidance if needed.
7. Verify the entire instruction from RGB and feedback. Finish achieved/blocked/aborted with a short evidence-based reason, and receive closed=true. The evaluator remains private.

Read references/evidence.md for observation and completion rules.

## Complete-task verification

Before claiming achieved, account for every requested item/count, spatial relation, door state and appliance state using the latest returned four-camera RGB plus the actions actually executed. The images returned by the last act/look are available for this check; no additional observation call is required. Reinspect occluded items from a new viewpoint when needed. Successful act responses are not an independent task score. Washing or heating needs an appropriate process interval; use act with primitive=wait, target=null and wait_seconds of 0.1–20 simulation seconds, default 5, then inspect its returned RGB. Do not infer a completed process from one successful toggle or visual appearance alone. If required evidence remains missing, continue or report the unresolved part rather than claim full completion.
