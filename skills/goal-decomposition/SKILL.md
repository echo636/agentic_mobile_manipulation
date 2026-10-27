---
name: goal-decomposition
description: Recursively decompose task outcomes and track dependencies, item coverage and evidence in task notes.
---
# Plan from the end state down to executable goals

Plan top down and verify bottom up. Use these roles to organize the hierarchy:

| Role | Purpose |
|---|---|
| Root: task end state | State what must be true when the whole task is complete, in your own words. Check coverage against the instruction. |
| Intermediate goal layers | Describe the contributing results needed to achieve each parent. Group related subresults under a meaningful parent and preserve their dependencies and constraints. Repeat this decomposition at finer levels where needed. |
| Leaf goals | State directly pursuable results, each with a feasible local plan and an observable completion check. A leaf may require several tool calls. |
| Current action plan | Put only the current leaf's immediate actions and next check in `active.steps` and `active.next_check`, outside the goal tree. Select concrete targets from current RGB. |

These are roles, not a fixed number of levels. Intermediate layers can repeat, and leaves can occur at different depths. Choose depth and child counts from the task; do not add levels just to fill a template. Keep intermediate parents when refining their children.

Read [references/working-state.md](references/working-state.md) and write the initial tree before the first act/look. Notes record your beliefs, not simulator truth.

Give requirements stable IDs, preserving quantities, destinations, relations, final device states and process or ordering constraints. Separate explicit requirements from assumptions; infer ordinary outcomes for vague wording without inventing a recipe or unrelated cleanup.

Before pursuing a goal, split it if it still bundles results that can be achieved and checked separately; apply the same test to the child you pursue. Stop when it meets the leaf criteria above. Children must jointly cover their parent's result and constraints. Refine the current branch as observations arrive; other branches can stay coarse until needed. A former leaf can acquire children when new work becomes apparent.

Use `parent_id` for decomposition and `depends_on` for prerequisites, including across branches. Keep dependencies acyclic and limited to necessary ordering. Select an unfinished leaf whose own and ancestors' prerequisites are satisfied; a blocked branch need not stop independent work.

For groups, link pending work to identifiable members or spatially distinct groups sharing the same task-relevant state. Track appearance, last location, current state and remaining work; action counts alone cannot identify what remains. If movement, mixing or a view change breaks that correspondence, mark it uncertain and regain it through a clearer view or separating items before an irreversible operation. Items found so far do not establish the total; retain uninspected locations and uncertain identities or destination roles.

After each action, check its actual result and revise the local plan. Once a leaf is supported, check its parent and continue upward only where each parent's own check is supported. Completed children alone do not prove their parent. Discovery or access does not establish delivery or processing; starting a device does not establish finished processing. Supported continuity and action feedback may establish occluded contents. Reopen or reinspect results and affected ancestors when later handling could have disturbed them.

Save notes when goals complete or block, focus changes, or members or assumptions change. Keep stable IDs and actual evidence IDs; explain revised or retired goals without losing coverage.

On failure, revise the unsupported assumption or local plan while preserving requirements. Temporary staging is not completed storage. Before finish, reconcile the root, every requirement and member with evidence; record unresolved or uncertain results rather than claiming completion.
