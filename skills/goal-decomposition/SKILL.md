---
name: goal-decomposition
description: Track required results, dependencies, item coverage and evidence in revisable task notes.
---
# Track goals and dependencies

Read [references/working-state.md](references/working-state.md) and write the initial goal tree before the first act/look. Notes record your beliefs, not simulator truth. Update when a result, active goal or important assumption changes.

**Define the end state.** Write the root goal in your own words: what should be true when the task is complete? Use the instruction to check coverage, rather than copying it as the goal. Give requirements stable IDs, preserving quantities, destinations, relations, final device states and any process or ordering constraints. Separate explicit requirements from assumptions; infer ordinary outcomes for vague wording without inventing a recipe or unrelated cleanup.

**Expand where useful.** Any goal may have children, and any child may be decomposed again. Depth and child counts can differ across branches; there is no fixed number of levels or nodes. Stop expanding when a result is clear enough to act on and check. Children must jointly cover their parent's required result, with observable checks. Discovery or access alone does not establish delivery or processing.

Use `parent_id` for decomposition and `depends_on` for prerequisites, including across branches. Keep dependencies acyclic and limited to necessary ordering. Choose a ready unfinished goal; a blocked branch need not stop independent work. Put only its immediate actions and next check in `active`, outside the goal tree. Choose pixels from current observations.

For groups, track distinct members by visual description, last location and pending/placed/verified state. One handled member does not complete a group; items found so far do not establish the total. Record uninspected locations and uncertain identities or destination roles.

After feedback, check the actual result. Completed children do not automatically prove their parent: verify its check too. Processing needs evidence beyond starting a device. Supported continuity and action feedback may establish occluded contents; reopen or reinspect results and affected ancestors when later handling could have disturbed them.

Save notes when goals complete or block, focus changes, or members or assumptions change. Keep stable IDs and actual evidence IDs; explain revised or retired goals without losing coverage. Expand or revise the tree as observations warrant, rather than prewriting every action.

On failure, revise the unsupported assumption or local plan while preserving requirements. Temporary staging is not completed storage. Before finish, reconcile the root, every requirement and member with evidence; record unresolved or uncertain results rather than claiming completion.
