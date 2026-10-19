---
name: long-horizon-planning
description: Plan natural-language or BDDL robot tasks as dependent goals, track observed state, and replan after each tool result. Use when one instruction requires multiple interdependent object or room changes.
---

# Long-horizon planning

The human instruction defines the goal. The current RGB and tool feedback are evidence; your plan and memory are hypotheses, not simulator truth. You choose every action and every RGB pixel.

1. Before the first motor action, translate the instruction into a compact goal tree. State the final observable results, then make a subgoal DAG: each node has a result, its prerequisites, and what evidence would count as done. Separate prerequisites from a fixed action order. Independent ready nodes may be attempted in either order. Use `update_plan` to store this DAG and mark only one or a few ready nodes active.
2. Keep three horizons in view: the final result, currently ready subgoals, and one immediate action. Select an action that advances a ready node or obtains evidence needed to choose one. Prefer a route that preserves already completed relations and avoids unnecessary return trips, but revise when new RGB contradicts the plan.
3. Before **every** tool call, fill the required `decision` fields with a brief public decision record: `state` (what is observed versus uncertain), `active_subgoal`, `reason` (why this tool and target now), and `expected` (what feedback would confirm progress). This is a concise decision summary, not private chain-of-thought. Cite only evidence available to you. For `initialize`, state that the scene is not observed yet.
4. After each result, update the current state: what changed, what remains held or open, which subgoal is supported, and what failed. Successful execution supports only its local effect; it does not certify the whole task. If an action fails, leave its goal pending, use the fresh RGB, and change the point, viewpoint, or ready branch. Revise `update_plan` when dependencies or node status change; use `remember` for landmarks, uncertain locations, and recovery attempts that would otherwise be forgotten.
5. Before `finish`, inspect every final obligation and its evidence. Mark an obligation done only when the latest observations and action feedback support it. If a required result remains feasible, continue; if a hard limit or nonrecoverable error prevents progress, report the actual limitation.

If the task input is BDDL, also read [BDDL goal interpretation](references/bddl-goals.md) before the first motor action. Translate its formal goal conditions into the same goal ledger and dependency graph; do not assume a task title is an additional goal. Keep the human's instruction intact when one is provided. Infer dependencies yourself from the observed scene and action affordances; do not treat a room hint as proof that an object is visible or a planned relation as already true.
