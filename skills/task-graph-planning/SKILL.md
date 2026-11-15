---
name: task-graph-planning
description: Model observed physical constraints with universal state, capacity, duration, and travel-cost primitives via plan_task_graph.
---
# General Precondition and Cost-Aware Task Graph Modeling

Use `plan_task_graph` (0 action steps) to formulate observed task structure using four universal physical primitives and obtain a minimum-time, deadlock-free execution schedule. The solver reads only caller-supplied beliefs and public action receipts; it never accesses private simulator state or executes physical actions.

## 1. Four Universal Physical Primitives

1. **`locations` and `travel_cost`**:
   - Declare distinct spatial stations or zones `{id, capacity, place_preconditions}`.
   - Leave `capacity: null` when space is unconstrained; set integer `capacity: K` when a station can hold at most `K` units simultaneously, and attach `place_preconditions` if placing onto that station requires a prior state condition.
   - Provide approximate `travel_cost` edges `{from_location, to_location, cost_s}` (for example, path distance divided by `0.5 m/s`). Publicly measured navigation durations from `robot_time` are fused automatically.

2. **`state_vars` and `entities`**:
   - `state_vars`: discrete or numeric variables `{name, value | flag | label, unknown, domain}`. When a relevant physical quantity is hidden inside a closed or unvisited enclosure, set `unknown: true` (optionally with candidate values in `domain`) instead of guessing.
   - `entities`: physical objects `{id, location, movable, capacity, size}`. A portable multi-slot holder is simply an entity with `movable: true` and `capacity: K` (`K >= 2`); a single movable object has `movable: true` and `capacity: 0` (or `null`).

3. **`operations`**:
   - Each candidate state transition specifies:
     - `location`: spatial node where the agent executes the operation;
     - `preconditions`: state predicates `{var, op, value | flag | label}` (`op` in `eq`, `ne`, `le`, `lt`, `ge`, `gt`, `known`; built-in query variables include `hand`, `occupancy:<id>`, and `entity:<id>.location`);
     - `effects` and `passive_effects`: post-condition updates `{var, op, value | flag | label}` (`op` in `set`, `add`, `max`, `min`);
     - `active_duration_s` and `occupies_hand`: active agent execution time and whether the single hand is required (`occupies_hand: true` requires an empty hand unless `preconditions` explicitly require holding a specific entity);
     - `passive_duration_s`: autonomous environment evolution duration (`> 0` when state progression continues without holding the agent or its hand);
     - `resolves_unknowns`: list of unknown variable names whose values become known upon executing this observation operation.

4. **`goals`**:
   - Terminal `predicates`, target `entity_locations` (`{entity_id, location}`), and `require_empty_hand`.

## 2. Lightweight Incremental Updates (Delta Merge)

- Keep calls concise: start with known locations, main entities, candidate operations, and terminal goals without enumerating unobserved details upfront.
- Follow `plan.next_step` grounded in current RGB views.
- On subsequent calls, send **only incremental deltas** (`completed_operations`, `started_passive_operations`, updated `state_vars` after resolving unknowns, or newly discovered `entities`/`operations`); previously declared entries persist automatically.
- Whenever a step has `replan_after_step: true` or physical execution reveals an unexpected blocker or state change, update the affected `state_vars` or `entities` and re-solve before committing to long-distance transfers.
- Treat `passive_duration_s` estimates as scheduling bounds only: always confirm completion in public action receipts and fresh RGB before advancing dependent transitions.
