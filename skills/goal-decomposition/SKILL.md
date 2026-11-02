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

Split goals where their results have different prerequisites, resources, locations or completion checks. Children must jointly cover their parent. A method must achieve its entire owner result, not merely advance it: inspecting one location cannot stand for locating all supplies. Give such intermediate progress its own result node. Keep understood work across branches available for comparison; refine when observations expose a new decision.

Use `parent_id` for decomposition and `depends_on` for necessary prerequisites, including across branches. Avoid global phases such as finding everything before processing anything: each operation depends on its own required inputs and information; combination depends on the contributing results. Keep dependencies acyclic. Select ready work whose own and ancestors' prerequisites are satisfied; a blocked branch need not stop independent work.

The tree describes results; the scheduler compares ways to achieve them. Add `methods` directly to executable goals and a small `planning` context using working-state.md. Include all understood unfinished work: discovery/access, processing, assembly, device use and waiting, delivery/arrangement, and final checks as applicable. Add methods to the known feasible branches before choosing one. For an unknown branch, retain its result and connect it to the discovery needed to plan it. Do not invent locations, compatibility or costs to make coverage look complete.

Offer alternatives when they change the work: different workstations or tools, independent versus shared processing, a combined inspection, staged versus direct assembly, or individual versus carrier delivery. A shared method names the other goals it achieves. Respect compatibility, capacity and distinguishable item states; fewer actions do not justify mixing incompatible work. Include handling, setup changes, travel and expected recovery in estimates. Do not create an alternative merely to fill a template.

First write the current DAG and read the returned `planning` coverage, order and costs. Then use `task_notes(operation="select")` to record the chosen method and short RGB-grounded steps without rewriting the tree. Equal-cost options are acceptable; if a cheaper recommendation is physically unsupported, correct its assumptions before choosing. A partial plan optimizes only its modeled scope. Resolve relevant coverage gaps as observations arrive; do not repeatedly choose among one-item plans while known independent work is omitted.

Execute the selected local method with act/look. Replies preserve the declared method and accumulate its tool time, excluding model requests. Continue supported steps without rewriting notes after every primitive. On completion, failure or changed assumptions, use write to reconcile goals, members, location and resources, clear `active`, and update estimates from receipts; read the recomputed comparison and select again. Expected effects and tool success never mark goals done. Starting a device does not establish finished processing; keep its remaining processing/check in the DAG.

Finish a storage visit with door closure before leaving or doing unrelated work unless explicitly required open. Include stable staging to free a hand. Choose transport and processing batch sizes separately; a shared trip need not imply processing an indistinguishable pile. Revise methods if actual handling mixes or spills items.

For groups, link pending work to identifiable members or spatially distinct groups sharing the same task-relevant state. Track appearance, last location, current state and remaining work; action counts alone cannot identify what remains. If movement, mixing or a view change breaks that correspondence, mark it uncertain and regain it through a clearer view or separating items before an irreversible operation. Items found so far do not establish the total; retain uninspected locations and uncertain identities or destination roles.

Verify bottom up: completed children do not by themselves prove a parent's own check. Supported continuity and action feedback may establish occluded contents. Reopen affected results and ancestors when later handling could have disturbed them. Keep stable IDs and evidence IDs when revising goals; temporary staging is not completed storage.

Before finish, reconcile the root, every requirement and member with evidence. Preserve valid progress and report remaining gaps rather than claiming completion.
