---
name: goal-decomposition
description: Decompose a manipulation goal into observable subgoals that cover its requirements and order the necessary prerequisites, especially when no procedure is given.
---
# Decompose a goal from RGB

Use this skill before acting on a multi-stage or underspecified goal, and when new evidence changes its decomposition. Available evidence is the instruction, current four-camera RGB, conversation history, and tool feedback. There is no hidden inventory, task recipe, evaluator query, or separate planning tool.

## Identify the required end state

Extract the task's objects or material, required changes, destinations or spatial relations, quantities, and final device/access states. Preserve qualifiers such as all, each, exactly, the same container, separate containers, and do not mix. A requested relation is not interchangeable with a nearby or easier arrangement. Distinguish a final requirement from a temporary step needed to achieve it.

Separate explicit requirements from assumptions. For a broad goal, use ordinary task meaning and visible context; briefly state any assumption that changes what counts as completion. Missing steps do not mean missing goals. Do not invent exact counts, hidden destinations, or benchmark-specific recipes. If quantity is unspecified, retain the relevant group as the scope and identify its members from observations; do not replace a plural goal with the first visible item. Missing objects or uncertain destinations become identification/search subgoals, not guessed facts.

Choose an unspecified destination by its function in the task, then by access. Temporary staging, grouping for use, and final storage serve different purposes. Check that a candidate object or device fits the required role; similar appearance or a successful open/toggle action does not establish identity or capability. When that distinction affects the plan, resolve it from RGB and informative feedback before committing dependent work. Do not add unrelated tidying or optional processing steps.

## Form subgoals that cover the goal

Define a subgoal by an observable result: which object or group must have which state or relation, to which destination if relevant. Include how that result can be checked. Avoid vague steps such as "handle the items" and bare tool lists such as "grasp, place, finish."

Split when the intended result, target group, destination, or required capability changes. Keep repeated equivalent transfers as one group subgoal with the required count or allocation; execute its members individually as needed. A simple task may have one subgoal. There is no fixed number of steps and no need to narrate private reasoning.

Before the first action, give a compact outline in ordinary assistant text: the intended end state, ordered result-level subgoals, and any unresolved prerequisite needed next. Cover every explicit outcome, including required final closures or device states. Check that each requirement appears in the outline and that each added step either achieves a requirement or enables one. Do not require a JSON report, plan file, or extra tool call.

## Order only the necessary prerequisites

For the next subgoal, check whether its object and destination are identified, access is available, and the needed hand or compatible tool is ready. Add work only for missing or uncertain prerequisites; do not reopen an already open container or move an item already correctly placed merely to follow a template.

- Prepare closed access while the hand is free before carrying an item through it. Prepare a compatible tool before a process that needs it.
- Choose a receptacle for the full intended group and its least flexible item. When useful and consistent with the final goal, prepare or position a visibly portable receptacle before repeated transfers; do not assume fixed furniture can move.
- For processing tasks, distinguish preparation, processing, and the required resulting state. Starting an appliance is not completion of heating or cleaning; include necessary process time and a result check. Do not invent a particular recipe absent from the goal or available evidence.
- Keep independent subgoals flexible. Prefer an order that avoids closing access too early or undoing a result already established. A convenient order must preserve all required objects, counts, and relations.

Expand only the next subgoal into actions using the newest RGB. Explicitly navigate when needed and reselect the target afterward; a feasible navigation approach does not guarantee manipulation reach. Do not precompute future pixels. Read visual-exploration for search, pick-and-place for carrying, and visual-manipulation for supported operations; plan/remember/recall are unavailable.

## Revise locally and check completion

After an action, use its fresh RGB and feedback to check the current result. If an assumption fails, revise the affected object choice, prerequisite, or subgoal; use failure-recovery for execution errors. Preserve valid completed work and the original requirements. Access difficulty alone is not a reason to redefine completion or change to a functionally unsuitable destination.

Keep a brief account of completed and remaining items in the conversation, distinguishing repeated objects by appearance and location. Use available remaining execution time and recent action durations to avoid starting infeasible rework; leave time to finish honestly.

Before finish, compare the observed results and action history with the original requirements. Check the relevant area for remaining members of a plural task. Tool completion alone does not prove correct object identity or the whole goal. Occlusion alone also does not disprove a result supported by identified-object continuity and successful operation feedback; distinguish supported inference from unresolved uncertainty, and seek another view only when it can resolve a material doubt. Report achieved only when evidence supports the whole interpreted goal; otherwise report blocked or aborted with the unresolved result. The independent official score remains unavailable.
