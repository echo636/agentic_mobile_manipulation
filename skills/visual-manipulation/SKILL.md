---
name: visual-manipulation
description: Complete a manipulation instruction through RGB inspection, revisable plans, visual actions, memory and explicit verification.
---
# Visual manipulation workflow

1. Call observe and inspect every returned image. head is the forward scene camera; left_wrist/right_wrist show the grippers and nearby surfaces. Images are real RGB, not object annotations.
2. State a visual hypothesis: what the requested object looks like, where it appears, which relations matter, and what remains unknown. Do not invent simulator IDs, distances, object lists or state truth.
3. Create a plan with update_plan. Select objects by a point in the latest image, not by remembered coordinates. Each observation has image_ref and revision; every new observation invalidates old image refs.
4. Read visual-exploration when searching, pick-and-place before carrying objects, and failure-recovery after a failed action. Use read_skill on the run-local resources.
5. Execute one bounded action; inspect its returned RGB before choosing another. Remember useful visual landmarks, objects already moved and uncertainty. Memory entries are hypotheses tied to a revision.
6. For done subgoals, cite a successful act/look evidence_id and explain the visible evidence. A tool completing does not establish the whole task.
7. Verify the complete instruction from fresh RGB plus action feedback. For a device, look for its indicator or changed appearance. If the visual state is ambiguous, say so rather than inventing a state flag.
8. Call finish with achieved, blocked or aborted. Receive closed=true before answering. The model cannot access task scoring, reset the episode or read arbitrary files.

The low-level executor can use private geometry to reach the pixel YOU chose. It must not find named objects for you. Correct object selection, exploration, ordering, recovery and visual verification remain your responsibility.

Read references/evidence.md for observation and completion rules.
