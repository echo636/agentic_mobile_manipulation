---
name: goal-decomposition
description: Turn a task-level goal into observable subgoals and adapt their order while acting, especially when the instruction omits a procedure.
---
# Decompose a goal from RGB

Use this skill at the start of a multi-stage or underspecified task, and revisit it when observations invalidate your approach. You have the instruction, current four-camera RGB, conversation history, and tool feedback. There is no task recipe, hidden object inventory, evaluator query, or separate planning tool.

## Establish the intended outcome

Separate what the instruction requires from what you infer. Preserve explicit quantities, destinations, relations, and final device states. For a broad goal, use the visible scene and ordinary task meaning to choose a reasonable interpretation; briefly state any consequential assumption and proceed. Do not invent exact counts, hidden contents, or benchmark-specific arrangements. When the scene does not yet support a choice, make finding the relevant objects or destination your next subgoal rather than guessing their locations. Avoid unrelated improvements that enlarge the task.

Describe completion as observable results, not a list of tool names. For a multi-stage task, briefly state the intended end state and the next few subgoals in normal assistant text. This is a working plan that may change, not a requirement to narrate every thought or write a separate plan file. A simple goal may need only one subgoal.

If a destination is unspecified, compare the options visible across all four views by their function in the task, then by access. An easy empty surface is not automatically an appropriate final home. Distinguish putting things away, grouping them for later work, and temporarily setting them down. State the intended destination's role before moving items. Check uncertain access with an informative action; neither a drawer-like appearance nor an open shelf alone proves suitability. If access fails, seek a functionally equivalent option rather than silently weakening the intended outcome. Revise a mistaken interpretation when new evidence warrants it, but do not redefine completion merely because the chosen approach is difficult.

## Order work by its prerequisites

For the next result, determine what must be true before it can happen: the target and destination must be found, an approach must be reachable, required access must be open, and the appropriate hand or tool must be available. Resolve missing prerequisites first. For example, prepare a closed destination while the hand is free before fetching an item; make a tool ready before using it. For repeated transfers, consider whether an obviously portable receptacle can safely be positioned near the work first, if the intended final arrangement permits it. Compare that preparation with repeated carrying trips; do not assume fixed furniture is movable. Choose containers with the full group and its least flexible items in mind, not just the first item that fits. Group work only when it preserves the intended objects and relations. If something must be processed, distinguish preparation, the process itself, and its resulting state; starting an appliance is not evidence that processing has completed.

Keep the plan at the subgoal level until you inspect the current RGB. Choose the next primitive and pixel only from the newest images. Manipulation does not move the base: explicitly navigate when needed, then reselect the object. Read visual-exploration for search, pick-and-place for carrying, and visual-manipulation for the available processing actions. Do not precompute a long sequence of pixels or call nonexistent plan/memory tools.

## Execute, track, and revise

Execute one action, inspect the returned RGB and feedback, and decide whether the current subgoal is still valid, completed, or needs a prerequisite. Keep completed items, remaining work, relevant landmarks, and uncertainty in the conversation. For repeated items, use visual distinctions and locations to avoid counting the same item twice or forgetting unprocessed items. Update the short progress statement when a subgoal completes or the plan materially changes; do not reread this skill on every step.

Treat tool completion as evidence about that operation. It does not establish correct object identity, the intended final arrangement, or completion of other subgoals. If an action fails, distinguish a failed plan assumption from an execution problem; read failure-recovery and change the relevant prerequisite, target selection, or approach. Preserve work already verified instead of restarting the whole plan or repeatedly executing the same failed action.

Use `execution_budget.remaining_seconds` when returned to prioritize the remaining subgoals. It is the time left when the tool replied, not a paused timer: model reasoning, tool execution, and waiting continue to consume it; simulator initialization is excluded. Compare it with recent action durations before starting a long search, recovery, or rearrangement. Leave time to review and call finish honestly. Rework completed items only when the alternative has a credible path to the intended outcome and enough time remains; an unsuccessful last item is not by itself a reason to dismantle completed groups. Changing approach must not silently weaken an explicit requirement. When no feasible completion remains, preserve verified progress and report the unresolved subgoal instead of claiming partial work is the whole goal.

## Close the whole goal

Compare the final observations and action history with the intended outcome. Check outstanding items and final access/device states that the instruction requires or your stated interpretation entails. A broad goal still requires checking the relevant area for remaining work; one successful item is not enough evidence that a plural cleanup or sorting task is finished. Prefer another informative viewpoint over inventing certainty. Use wait for necessary process time and inspect its returned images.

Call finish only after this review. Report achieved when the available evidence supports the whole interpreted goal; otherwise report blocked or aborted with the remaining subgoal and concrete limitation. Keep inferred completion distinct from the independent official score, which is not available to you.
