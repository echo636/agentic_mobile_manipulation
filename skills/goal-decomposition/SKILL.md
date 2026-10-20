---
name: goal-decomposition
description: Maintain a revisable L1/L2 goal tree for visual manipulation, using object coverage, explicit completion checks and goal-preserving recovery; plan concrete actions only for the active subgoal.
---
# Use the goal tree to decide what remains

Use the user's instruction, returned RGB and tool feedback. Hidden inventories, BDDL, recipes and evaluator state are unavailable. `task_notes` stores your beliefs; neither its storage nor a tree's done labels verify the task.

Read [references/working-state.md](references/working-state.md) before the first write. Save the initial L1/L2 tree and active L3 plan before the first act/look. Keep the same compact JSON format throughout the episode. Update at meaningful changes, not after every action.

## L1: keep the requested result fixed, its interpretation revisable

Copy the instruction unchanged into `L1.instruction`. Express its required final results as criteria with stable IDs. Preserve specified quantities, object groups, destinations, relations and final device/access states. Mark each criterion's basis as explicit instruction or your interpretation. A vague phrase still describes an outcome: infer a useful, ordinary result from visible context, using the fewest extra assumptions. Do not invent a hidden recipe or extra cleanup.

Separate **what is known** from **what is assumed**. In a plural task, “the objects seen so far” is not yet “all relevant objects.” Keep unresolved scope, an uncertain destination role or an ambiguous object identity in `uncertainties`; give the needed discovery work a child when it affects the next decision. Use visual descriptors and locations to distinguish members. Do not infer a count from the first view or from repeated successful transfers.

## L2: result commitments with checks

Each child must have `goal`, `covers`, `depends_on`, `status`, `check` and `evidence`. Before working on a child, fill its check: **what observable result would distinguish success from a merely successful action?** Every L1 criterion must have a child that establishes it; a discovery or access child alone does not establish the final result. One child is enough for a simple task. Keep atomic commands out of the tree.

Group equivalent work where useful, but track members separately in that child's evidence: distinguishing descriptor, last known location, and pending/placed/verified state. Do not count the same object twice or mark a whole group done because one member was handled. Discovery remains incomplete when relevant locations have not been inspected. Search likely task areas in an organized way; do not repeatedly revisit the same view without a new question.

Dependencies describe necessary result ordering, not a fixed serial script. Open required access before carrying through it; resolve tool/transfer feasibility before creating material that cannot be handled by the available primitives; finish a process before clearing its setup. Delay final closures until dependent transfers are done. Preserve unaffected results when choosing the next ready child.

## L3: execute one child's local plan

Select an unfinished child whose prerequisites hold. Keep only its immediate actions and next result check in `active`, outside the tree. Use current-image targets and the execution skills. Avoid a full episode action script or future pixels.

In `target_locked_local_v1`, do not automatically prepend "approach/navigate" to every pickup or placement in L3. If the intended target is clearly selectable and appears nearby in any current view, plan the operation directly; its local controller checks exact reach and prepares the stance. Macro navigation needs a reason: a clearly distant target, another task area, exploration/reacquisition, or execution feedback requesting a different approach. Being unsure of the ideal position for an otherwise nearby identifiable target is not that reason. After necessary travel, recheck current RGB before adding another navigation step.

After each returned result, compare it with the child's check:

- A tool's `ok` establishes only the reported operation. Identify which object and destination actually changed. Moving an item to a surface does not necessarily establish the requested containment, grouping or relative placement.
- For processing, initiating a device or waiting is not itself evidence that the material reached its intended state. Use visible changes and available feedback; retain uncertainty when these are inconclusive.
- Occluded results may rely on supported object continuity and action feedback. Do not spend steps reopening every closed container solely to obtain a picture. Recheck results when later handling could have disturbed them.

Write notes when a child completes or blocks, focus changes, scope expands, an assumption changes or the local approach needs revision. Include the relevant returned evidence ID and what it supports; an ID alone is not a completion check.

## Recover without substituting an easier goal

Diagnose the failed assumption before retrying: identity/role, access/reach, primitive suitability, geometry or missing capability. A rejection is not proof that the intended object was selected. If repeated feedback contradicts your identification, inspect the target's role before spending more actions on it. Retry when a concrete change addresses the failure, not because the same plan has not worked yet.

Compare any fallback with the original L1 relation. A temporary staging surface is progress toward storage, not completed storage. A changed container is valid only if it still serves the request and your stated interpretation. If the necessary result remains unsupported, mark the child blocked; continue independent useful work if possible. Never make a blocked child done by weakening its goal.

Keep stable IDs. Add discovered work; reopen a done child if its result was undone. Retire mistaken or redundant children with a reason, retaining coverage of explicit requirements. Revisions to assumed L1 criteria must name the new observation that justifies them. Use steps_remaining to prioritize unresolved required results and avoid exhausting the budget on unchanged failures.

## Finish by reconciling criteria, members and evidence

Before finish, check each L1 criterion against its children, member ledger and the latest relevant evidence. Separate **verified**, **pending/blocked**, and **uncertain** results in the final notes. Inspect newly noticed members and any arrangement likely disturbed by later work. A tree whose children all say done can still have an uncovered criterion or an unresolved group scope.

Save this reconciliation in `change` and unresolved issues in `uncertainties`. Finish achieved only when the whole interpreted result is supported, with no required member or criterion unresolved; otherwise finish blocked or aborted and state what remains. The independent evaluator may still disagree with your interpretation; do not claim access to it.
