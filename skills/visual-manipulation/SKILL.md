---
name: visual-manipulation
description: Complete manipulation through RGB, callable workflow skills, bounded actions and visible verification.
---
# Visual manipulation workflow

1. Observe and inspect head, left_wrist and right_wrist RGB. Perceive requested objects yourself; no oracle inventory or state is available.
2. Read visual-exploration for search, pick-and-place before carrying, failure-recovery after failed actions. These documents guide your next action; they do not execute actions automatically.
3. Choose one next action from visible evidence. The replay records your actual public messages and tool calls verbatim; there is no extra decision form or required language. Do not claim unseen success.
4. Select a point in the latest image_ref with the latest revision. Approach, then select again from fresh RGB before grasp/open/place/toggle. The executor cannot select objects for you.
5. Execute one bounded action and inspect its returned RGB. Use normal conversation history to track moved items and visual landmarks. Explicit plan and memory tools are unavailable in the default skills profile; no plan-writing stage is required.
6. Compare fresh visual evidence with the expected effect. A tool completing is only an operation report. Change the viewpoint or approach when uncertain, and read recovery guidance if needed.
7. Verify the entire instruction from RGB and feedback. Finish achieved/blocked/aborted with a short evidence-based reason, and receive closed=true. The evaluator remains private.

Read references/evidence.md for observation and completion rules.
