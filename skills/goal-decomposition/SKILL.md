---
name: goal-decomposition
description: Decompose task outcomes, compare costed methods across a work DAG, and reconcile progress from RGB evidence.
---
# Plan from the end state down to executable goals

Plan top down and verify bottom up. Use these roles to organize the hierarchy:

| Role | Purpose |
|---|---|
| Root: task end state | State what must be true when the whole task is complete, in your own words. Check coverage against the instruction. |
| Intermediate goal layers | Describe the contributing results needed to achieve each parent. Group related subresults under a meaningful parent and preserve their dependencies and constraints. Repeat this decomposition at finer levels where needed. |
| Leaf goals | State directly pursuable results, each with a feasible local plan and an observable completion check. A leaf may require several tool calls. |
| Current action plan | Choose a short sequence serving one or several ready leaves; put it in `active.steps` with the next check, outside the goal tree. Select concrete targets from current RGB. |

These are roles, not a fixed number of levels. Intermediate layers can repeat, and leaves can occur at different depths. Choose depth and child counts from the task; do not add levels just to fill a template. Keep intermediate parents when refining their children.

Read [references/working-state.md](references/working-state.md) and write the initial tree before the first act/look. Notes record your beliefs, not simulator truth.

Give requirements stable IDs, preserving quantities, destinations, relations, final device states and process or ordering constraints. Separate explicit requirements from assumptions; infer ordinary outcomes for vague wording without inventing a recipe or unrelated cleanup.

Before pursuing a goal, split it if it still bundles results that can be achieved and checked separately; apply the same test to the child you pursue. Stop when it meets the leaf criteria above. Children must jointly cover their parent's result and constraints. Refine the current branch as observations arrive; other branches can stay coarse until needed. A former leaf can acquire children when new work becomes apparent.

Use `parent_id` for decomposition and `depends_on` for prerequisites, including across branches. Keep dependencies acyclic and limited to necessary ordering. When a result requires combining inputs, include that combination in its prerequisites; processing one input alone does not establish the finished result. Select ready work whose own and ancestors' prerequisites are satisfied; a blocked branch need not stop independent work.

The tree describes results; the scheduler compares ways to achieve them. Add `methods` directly to executable goals and a small `planning` context using working-state.md. Cover known unfinished work across branches, including discovery, access, processing, assembly, placement, device use and verification where relevant. Unknown work stays visible as a gap; model the next useful discovery step instead of inventing locations or costs.

Offer alternatives when they change the work: different workstations or tools, independent versus shared processing, a combined inspection, staged versus direct assembly, or individual versus carrier delivery. A shared method names the other goals it achieves. Respect compatibility, capacity and distinguishable item states; fewer actions do not justify mixing incompatible work. Include handling, setup changes, travel and expected recovery in estimates. Do not create an alternative merely to fill a template.

Read `planning` in the tool reply. It reports coverage, the estimated order, competing first choices and missing information. Choose the next method in `active.method_id`, its goals in `active.serves`, and a short RGB-grounded sequence in `active.steps`. If you reject the recommendation, explain the concrete unsupported assumption in `change` and revise it. A partial plan optimizes only its modeled scope; coarse unknown branches must not disappear.

Every act/look reply asks you to reconcile its result with that checkpoint. Continue a supported local method without rewriting notes after every primitive. Update notes when a method completes or fails, the branch changes, or observations alter locations, resources or membership; the reply recomputes the remaining DAG. Neither expected method effects nor tool success automatically marks goals done. Starting a device does not establish finished processing; represent the remaining processing/check as work.

Finish a storage visit with door closure before leaving or doing unrelated work unless explicitly required open. Include stable staging to free a hand. Choose transport and processing batch sizes separately; a shared trip need not imply processing an indistinguishable pile. Revise methods if actual handling mixes or spills items.

For groups, link pending work to identifiable members or spatially distinct groups sharing the same task-relevant state. Track appearance, last location, current state and remaining work; action counts alone cannot identify what remains. If movement, mixing or a view change breaks that correspondence, mark it uncertain and regain it through a clearer view or separating items before an irreversible operation. Items found so far do not establish the total; retain uninspected locations and uncertain identities or destination roles.

Verify bottom up: completed children do not by themselves prove a parent's own check. Supported continuity and action feedback may establish occluded contents. Reopen affected results and ancestors when later handling could have disturbed them. Keep stable IDs and evidence IDs when revising goals; temporary staging is not completed storage.

Before finish, reconcile the root, every requirement and member with evidence. Preserve valid progress and report remaining gaps rather than claiming completion.
