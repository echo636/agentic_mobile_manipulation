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

Trace every required final result backward to its prerequisites, including branches not yet executable. Separate independently verifiable results with different destinations, processing states or resource needs; keep aggregate outcomes as parents. The scheduler cannot reorder work hidden inside one broad method. Preserve those separate results when batching: a shared method connects them with `also`. Each method must achieve its entire owner result, and children must jointly cover their parent. Keep unknown results with explicit discovery prerequisites.

Use `parent_id` for decomposition and `depends_on` for necessary prerequisites, including across branches. Avoid global phases such as finding everything before processing anything: each operation depends on its own required inputs and information; combination depends on the contributing results. Keep dependencies acyclic. Select ready work whose own and ancestors' prerequisites are satisfied; a blocked branch need not stop independent work.

Separate confirmed prerequisites from search hypotheses. An access failure blocks that attempted method; it does not establish where unseen inputs are or rule out other sources. Keep plausible uninspected areas as discovery work and revise the affected method before blocking the required result.

Add `methods` directly to executable goals and a `planning` context using working-state.md. Cover discovery/access, processing, assembly, device use, waiting, delivery and final checks as needed, including known future work. In `route` and `planning.travel`, distinguish observed workstations or destinations that require travel, even within one room; using one room label makes that travel cost zero. Unknown locations or capabilities remain explicit gaps.

Compare ways to achieve the same result: workstation/tool choices, shared processing or inspection, staged/direct assembly, process overlap and delivery. Put prerequisites common to every method on the goal; put method-specific prerequisites on that method. Mark auxiliary results needed only by some methods `optional:true`; requested outcomes remain required. This lets the scheduler compare a direct method against one with extra setup, including the setup's full cost. Shared methods name all results they achieve. Respect compatibility, capacity and distinguishable item states; include handling, setup, travel and recovery costs. Do not invent alternatives or capabilities.

Write the DAG and read `planning` coverage, `unplanned_results`, order, costs and `comparison`. A single method set compares ordering only; equal costs show no estimated advantage. Represent observed differences in workstations, setup, shared work and process overlap, with a short `description` of each candidate's approach. Do not invent alternatives or spend extra actions just to populate the graph. Fill known missing work; discover and refine unknowns as needed. Use `task_notes(operation="select")` to record the chosen method and short RGB-grounded steps. Correct physically unsupported recommendations. Partial coverage is not whole-task optimization.

Execute the selected local method with act/look. Replies retain `selected_method`: its approach, work locations, resource conditions/setup, predicted effects, process timing and checks for every served result. Ground these in current RGB when choosing actions; a planned setup change still needs physical execution. Tool time and observed simulation progress accumulate separately. Continue supported steps without rewriting notes after every primitive. At completion, failure or changed assumptions, use `task_notes(operation="update")` for changed goals and beliefs; unchanged branches persist and `active` clears. Read the new comparison and select again. Refine methods as unknown inputs become visible rather than retaining a task-wide discovery phase. Expected effects and tool success never mark goals done.

For unattended processing, separate starting the process from checking its result. Use the process timing fields in working-state.md to compare waiting against doing independent work. Estimate simulation progress from capture timestamps and action receipts; model/network waiting does not advance processing. Reconcile remaining process times after execution. Reaching an estimated time only makes a check worthwhile; it does not establish completion.

Finish a storage visit with door closure before leaving or doing unrelated work unless explicitly required open. Include stable staging to free a hand. Choose transport and processing batch sizes separately; a shared trip need not imply processing an indistinguishable pile. Revise methods if actual handling mixes or spills items.

For groups, link pending work to identifiable members or spatially distinct groups sharing the same task-relevant state. Track appearance, last location, current state and remaining work; action counts alone cannot identify what remains. If movement, mixing or a view change breaks that correspondence, mark it uncertain and regain it through a clearer view or separating items before an irreversible operation. Items found so far do not establish the total; retain uninspected locations and uncertain identities or destination roles.

Verify bottom up: completed children do not by themselves prove a parent's own check. Supported continuity and action feedback may establish occluded contents. Reopen affected results and ancestors when later handling could have disturbed them. Keep stable IDs and evidence IDs when revising goals; temporary staging is not completed storage.

Before finish, reconcile the root, every requirement and member with evidence. Preserve valid progress and report remaining gaps rather than claiming completion.
