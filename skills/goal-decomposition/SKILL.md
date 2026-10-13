---
name: goal-decomposition
description: Turn a brief or ambiguous manipulation instruction into a revisable two-level goal tree, then plan and execute only the current subgoal's concrete steps using RGB evidence.
---
# Maintain goals, plan the next subgoal

Use the instruction, returned RGB and action feedback. No hidden BDDL, inventory, recipe, evaluator or oracle state is available. The goal tree is your working interpretation, not ground truth. Keep it in `task_notes` as JSON using the format below. `task_notes` only stores your text; it does not plan, validate completion, or execute actions.

## L1: interpret the requested outcome

After initialize, interpret what the user wants to be true when the task is over. Preserve the original request, object groups, quantities, destinations, spatial relations and required final device/access states. Separate explicit conditions from assumptions inferred from ordinary task meaning and visible context. A short phrase is a goal to interpret, not an instruction to perform one arbitrary action.

For an unspecified detail, choose the least additional commitment consistent with the request and scene. Prefer functional suitability before convenience. Do not invent exact counts, invisible objects or a benchmark-specific recipe. For a plural or group task, retain the full relevant group: discover members from RGB instead of treating the first item as the entire scope. Missing identity, scope or destination is an uncertainty to resolve. Do not add unrelated cleanup or optional processing.

Write observable completion criteria for L1. These criteria anchor revisions: an execution obstacle can change the means, but cannot erase the requested result. If a new observation corrects an assumption, record the change and preserve explicit requirements.

## L2: decompose into result-level subgoals

Create a child subgoal when a distinct intermediate or final result needs its own completion check. Each child records its result, the L1 criteria it serves, necessary dependencies, status and evidence. Required results, enabling prerequisites and discovery can all be children. There are exactly two levels in the goal tree; dependencies between children describe ordering without adding more tree depth.

Use the granularity the task needs. One child can cover a simple task. Equivalent transfers can share one child, with progress on individual members tracked in its evidence. Do not fill the tree with atomic grasp/navigate/place commands or force a fixed number of children. Each child must achieve an L1 criterion or enable another child; every L1 criterion needs coverage.

Check prerequisites that affect ordering: identify the object and destination; open access with an empty hand before carrying through it; obtain a compatible tool before processing; preserve finished arrangements and leave final closures/device states until dependent work is done. Starting a device is a process prerequisite, not proof of completed cooking or washing. Search or capability uncertainty may require a discovery subgoal. Do not plan unsupported operations as though they were available.

Before the first act/look, save the initial L1/L2 tree and the current child's L3 plan with task_notes. Read [references/working-state.md](references/working-state.md) for the compact format and update example. The public record should show what you intend to achieve before execution begins.

## L3: plan only the active child's steps

Choose an unfinished child whose necessary dependencies are satisfied. Expand only this child into a short, revisable sequence of immediate actions and checks in `active.steps`, outside the goal tree. Other children keep outcome descriptions until their turn. If discovery is needed before manipulation, make that the active work.

Choose concrete primitives using the other skills and current RGB. Do not preselect future pixels: every act target must come from the latest returned image. An approach does not guarantee manipulation reach. Resolve unexpected feedback with a changed approach, prerequisite or local step sequence rather than repeating blindly.

After each action inspect its returned RGB and feedback against the active child's check. Continue the local steps while the plan is valid; writing notes after every motor action is unnecessary. Save a new note version whenever a child completes or blocks, focus changes, scope expands, an assumption is corrected, or the local approach changes materially. Include a short public reason and relevant observation/tool evidence IDs. Track completed and remaining members of repeated work.

## Revise the tree without losing the task

Keep stable child IDs. Add discovered prerequisites or missing group members; revise dependencies and local steps when evidence warrants it. Reopen a done child if its result was undone. Preserve unaffected completed work. Retire a redundant or mistaken child with a reason instead of silently deleting it. If the L1 interpretation changes, explain which assumption changed; keep explicit user criteria intact.

Statuses are pending, active, done, blocked and retired. Done means your evidence supports the child's result, not merely that a tool returned ok. A failed grasp need not invalidate the whole tree; a wrong destination may require revising multiple children. Blocked means the result remains unresolved. Use steps_remaining to prioritize required outcomes and avoid spending all 50 steps on one unchanged failure.

Before finish, reconcile all L1 criteria with the current child states and available evidence, including remaining members of a group and required final states. Supported object continuity and operation feedback can complement occluded RGB; unresolved uncertainty must stay explicit. Save the final notes, then finish achieved only when the whole interpreted goal is supported; otherwise finish blocked or aborted with the remaining result. Never replace independent evaluation with your tree's done labels.
