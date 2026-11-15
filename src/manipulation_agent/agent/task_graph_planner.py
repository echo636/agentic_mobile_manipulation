"""General precondition and cost-aware task graph planner (zero domain assumptions).

Strictly Harness-side (CPU-only, zero simulator imports, zero private scene or
evaluator access). Accepts and reasons exclusively over four universal physical
primitives:
1. Spatial nodes and point-to-point travel costs (`locations`, `travel_cost`);
2. State variables and physical entities with generic `movable` and `capacity`
   attributes (`state_vars`, `entities`);
3. State-transition operators with `location`, `preconditions`, `effects`,
   `active_duration_s`, `occupies_hand`, `passive_duration_s`, and
   `resolves_unknowns` (`operations`);
4. Target terminal conditions (`goals`).

A single unified search solver minimizes completion time (with spatial travel
cost tie-breaking) over concrete states and finite-domain belief states,
allowing asynchronous passive overlap, multi-item capacity batching and return
consolidation, minimum-reset monotonic ordering, deadlock-free bounded-buffer
swapping, and value-of-information observation scheduling to emerge naturally
without domain-specific rules.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from dataclasses import dataclass, field
import heapq
import itertools
import math
from typing import Any

from ..contracts import SkillError

DEFAULT_MANIP_DURATION_S = 10.0
DEFAULT_UNCONNECTED_TRAVEL_S = 25.0
TRAVEL_TIEBREAK_WEIGHT = 1e-3
EPS = 1e-6


def _clean_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise SkillError(
            'invalid_task_graph_input',
            f'{field_name} must be a non-empty string (<= 120 chars).',
        )
    return value.strip()


def _canonical_scalar(raw: Any, field_name: str) -> bool | float | str:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        val = float(raw)
        if not math.isfinite(val):
            raise SkillError('invalid_task_graph_input', f'{field_name} must be finite.')
        return int(round(val)) if abs(val - round(val)) <= 1e-9 else round(val, 6)
    if isinstance(raw, str):
        return raw.strip()
    raise SkillError(
        'invalid_task_graph_input',
        f'{field_name} must be a number, boolean, or string.',
    )


def _state_atom(val: Any) -> tuple[str, Any]:
    """Type-tagged hashable atom avoiding Python's True == 1 key collision."""
    if isinstance(val, bool):
        return ('b', val)
    if isinstance(val, (int, float)):
        fval = float(val)
        return ('n', int(round(fval)) if abs(fval - round(fval)) <= 1e-9 else round(fval, 6))
    if val is None:
        return ('null', None)
    return ('s', str(val))


def _values_equal(lhs: Any, rhs: Any) -> bool:
    if isinstance(lhs, bool) or isinstance(rhs, bool):
        return isinstance(lhs, bool) and isinstance(rhs, bool) and lhs == rhs
    if isinstance(lhs, (int, float)) and isinstance(rhs, (int, float)):
        return abs(float(lhs) - float(rhs)) <= EPS
    return lhs == rhs


def _as_float(val: Any, field_name: str) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise SkillError('invalid_task_graph_input', f'{field_name} requires a numeric state value, got {val!r}.')
    return float(val)


def _extract_typed_value(entry: dict[str, Any], field_name: str, *, allow_none: bool = False) -> Any:
    present = [
        key for key in ('value', 'flag', 'label')
        if entry.get(key) is not None
    ]
    if len(present) > 1:
        raise SkillError(
            'invalid_task_graph_input',
            f'{field_name} must specify at most one of value, flag, or label.',
        )
    if not present:
        if allow_none:
            return None
        raise SkillError(
            'invalid_task_graph_input',
            f'{field_name} must specify value (number), flag (boolean), or label (string).',
        )
    return _canonical_scalar(entry[present[0]], f'{field_name}.{present[0]}')


@dataclass(frozen=True)
class Predicate:
    var: str
    op: str = 'eq'
    target: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {'var': self.var, 'op': self.op}
        if self.op != 'known':
            if isinstance(self.target, bool):
                out['flag'] = self.target
            elif isinstance(self.target, (int, float)):
                out['value'] = self.target
            elif self.target is not None:
                out['label'] = str(self.target)
        return out


@dataclass(frozen=True)
class Effect:
    var: str
    op: str = 'set'
    target: Any = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {'var': self.var, 'op': self.op}
        if isinstance(self.target, bool):
            out['flag'] = self.target
        elif isinstance(self.target, (int, float)):
            out['value'] = self.target
        elif self.target is not None:
            out['label'] = str(self.target)
        return out


@dataclass(frozen=True)
class LocationSpec:
    id: str
    capacity: int | None = None
    place_preconditions: tuple[Predicate, ...] = ()


@dataclass(frozen=True)
class StateVarSpec:
    name: str
    known: bool = True
    value: Any = 0
    domain: tuple[Any, ...] = ()
    probabilities: tuple[float, ...] = ()


@dataclass(frozen=True)
class EntitySpec:
    id: str
    location: str
    movable: bool = False
    capacity: int | None = None
    size: int = 1
    place_preconditions: tuple[Predicate, ...] = ()


@dataclass(frozen=True)
class OperationSpec:
    id: str
    location: str | None = None
    preconditions: tuple[Predicate, ...] = ()
    immediate_effects: tuple[Effect, ...] = ()
    passive_effects: tuple[Effect, ...] = ()
    active_duration_s: float = DEFAULT_MANIP_DURATION_S
    occupies_hand: bool = True
    passive_duration_s: float = 0.0
    resolves_unknowns: tuple[str, ...] = ()
    repeatable: bool = True
    status: str = 'pending'
    started_at_s: float | None = None


@dataclass(frozen=True)
class GoalSpec:
    predicates: tuple[Predicate, ...] = ()
    entity_locations: tuple[tuple[str, str], ...] = ()
    require_empty_hand: bool = False


@dataclass(frozen=True)
class CompiledTaskGraph:
    agent_location: str
    hand_entity: str | None
    locations: dict[str, LocationSpec]
    travel_s: dict[tuple[str, str], float]
    travel_sources: dict[tuple[str, str], str]
    assumptions: tuple[str, ...]
    state_vars: dict[str, StateVarSpec]
    entities: dict[str, EntitySpec]
    operations: dict[str, OperationSpec]
    goals: GoalSpec
    manip_duration_s: float = DEFAULT_MANIP_DURATION_S
    initial_clock_s: float = 0.0


@dataclass(frozen=True)
class PassiveEvent:
    ready_at_s: float
    op_id: str
    effects: tuple[Effect, ...]


@dataclass(frozen=True)
class WorldState:
    agent_loc: str
    hand: str | None
    entity_loc: tuple[tuple[str, str], ...]
    vars: tuple[tuple[str, Any], ...]
    unknown_vars: frozenset[str]
    passive_events: tuple[PassiveEvent, ...]
    executed_once: frozenset[str]
    clock_s: float
    travel_s: float = 0.0

    def entity_map(self) -> dict[str, str]:
        return dict(self.entity_loc)

    def vars_map(self) -> dict[str, Any]:
        return dict(self.vars)


@dataclass(frozen=True)
class PlanStep:
    kind: str  # 'operation' | 'pick' | 'place' | 'wait_passive'
    op_id: str
    from_location: str
    location: str
    travel_cost_s: float
    active_duration_s: float
    passive_duration_s: float
    wait_duration_s: float
    start_time_s: float
    end_time_s: float
    passive_ready_time_s: float | None = None
    entity_id: str | None = None
    target_host: str | None = None
    resolves_unknowns: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()

    def to_dict(self, index: int, causal_predecessors: list[int] | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            'step_index': index,
            'kind': self.kind,
            'op_id': self.op_id,
            'from_location': self.from_location,
            'location': self.location,
            'travel_cost_s': round(self.travel_cost_s, 2),
            'wait_duration_s': round(self.wait_duration_s, 2),
            'active_duration_s': round(self.active_duration_s, 2),
            'passive_duration_s': round(self.passive_duration_s, 2),
            'start_time_s': round(self.start_time_s, 2),
            'end_time_s': round(self.end_time_s, 2),
        }
        if self.passive_ready_time_s is not None:
            row['passive_ready_time_s'] = round(self.passive_ready_time_s, 2)
        if self.entity_id is not None:
            row['entity_id'] = self.entity_id
        if self.target_host is not None:
            row['target_host'] = self.target_host
        if self.resolves_unknowns:
            row['resolves_unknowns'] = list(self.resolves_unknowns)
            row['replan_after_step'] = True
        if causal_predecessors is not None:
            row['causal_predecessors'] = causal_predecessors
        return row


def _parse_predicate_list(raw: Any, field_name: str) -> tuple[Predicate, ...]:
    if raw is None:
        return ()
    out: list[Predicate] = []
    if isinstance(raw, dict):
        for var_name, spec in raw.items():
            v = _clean_id(var_name, f'{field_name}.key')
            if isinstance(spec, dict):
                if 'known' in spec and spec['known'] is True and len(spec) == 1:
                    out.append(Predicate(var=v, op='known', target=None))
                    continue
                op_keys = [k for k in ('eq', 'ne', 'le', 'lt', 'ge', 'gt') if k in spec]
                if len(op_keys) == 1:
                    ok = op_keys[0]
                    out.append(Predicate(var=v, op=ok, target=_canonical_scalar(spec[ok], f'{field_name}.{v}.{ok}')))
                    continue
                op = str(spec.get('op') or 'eq')
                if op == 'known':
                    out.append(Predicate(var=v, op='known', target=None))
                else:
                    target = _extract_typed_value(spec, f'{field_name}.{v}')
                    out.append(Predicate(var=v, op=op, target=target))
            else:
                out.append(Predicate(var=v, op='eq', target=_canonical_scalar(spec, f'{field_name}.{v}')))
        return tuple(out)
    if not isinstance(raw, list):
        raise SkillError('invalid_task_graph_input', f'{field_name} must be a list or mapping.')
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SkillError('invalid_task_graph_input', f'{field_name}[{idx}] must be an object.')
        v = _clean_id(item.get('var'), f'{field_name}[{idx}].var')
        op = str(item.get('op') or 'eq')
        if op not in {'eq', 'ne', 'le', 'lt', 'ge', 'gt', 'known'}:
            raise SkillError('invalid_task_graph_input', f'{field_name}[{idx}].op {op!r} is unsupported.')
        if op == 'known':
            out.append(Predicate(var=v, op='known', target=None))
        else:
            target = _extract_typed_value(item, f'{field_name}[{idx}]', allow_none=(v == 'hand'))
            out.append(Predicate(var=v, op=op, target=target))
    return tuple(out)


def _parse_effect_list(raw: Any, field_name: str) -> tuple[Effect, ...]:
    if raw is None:
        return ()
    out: list[Effect] = []
    if isinstance(raw, dict):
        for var_name, spec in raw.items():
            v = _clean_id(var_name, f'{field_name}.key')
            if isinstance(spec, dict):
                op_keys = [k for k in ('set', 'add', 'max', 'min') if k in spec]
                if len(op_keys) == 1:
                    ok = op_keys[0]
                    out.append(Effect(var=v, op=ok, target=_canonical_scalar(spec[ok], f'{field_name}.{v}.{ok}')))
                    continue
                op = str(spec.get('op') or 'set')
                target = _extract_typed_value(spec, f'{field_name}.{v}', allow_none=(v == 'hand'))
                out.append(Effect(var=v, op=op, target=target))
            elif spec is None and v == 'hand':
                out.append(Effect(var=v, op='set', target=None))
            else:
                out.append(Effect(var=v, op='set', target=_canonical_scalar(spec, f'{field_name}.{v}')))
        return tuple(out)
    if not isinstance(raw, list):
        raise SkillError('invalid_task_graph_input', f'{field_name} must be a list or mapping.')
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SkillError('invalid_task_graph_input', f'{field_name}[{idx}] must be an object.')
        v = _clean_id(item.get('var'), f'{field_name}[{idx}].var')
        op = str(item.get('op') or 'set')
        if op not in {'set', 'add', 'max', 'min'}:
            raise SkillError('invalid_task_graph_input', f'{field_name}[{idx}].op {op!r} is unsupported.')
        target = _extract_typed_value(item, f'{field_name}[{idx}]', allow_none=(v == 'hand'))
        if op in {'add', 'max', 'min'} and (isinstance(target, bool) or not isinstance(target, (int, float))):
            raise SkillError('invalid_task_graph_input', f'{field_name}[{idx}] with op={op!r} requires numeric value.')
        out.append(Effect(var=v, op=op, target=target))
    return tuple(out)


def _compute_all_pairs_travel(
    location_ids: list[str],
    declared_edges: list[dict[str, Any]],
    measured_edges: dict[tuple[str, str], float] | None = None,
) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], str], tuple[str, ...]]:
    idx = {loc: i for i, loc in enumerate(location_ids)}
    n = len(location_ids)
    dist = [[0.0 if i == j else math.inf for j in range(n)] for i in range(n)]
    direct_source: dict[tuple[str, str], str] = {(loc, loc): 'zero' for loc in location_ids}

    for k, edge in enumerate(declared_edges):
        u = _clean_id(edge.get('from_location'), f'travel_cost[{k}].from_location')
        v = _clean_id(edge.get('to_location'), f'travel_cost[{k}].to_location')
        if u not in idx or v not in idx:
            raise SkillError(
                'invalid_task_graph_input',
                f'travel_cost[{k}] references undeclared location ({u!r} -> {v!r}).',
            )
        if u == v:
            continue
        raw_cost = edge.get('cost_s')
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
            raise SkillError('invalid_task_graph_input', f'travel_cost[{k}].cost_s must be a non-negative number.')
        cost_s = float(raw_cost)
        if not math.isfinite(cost_s) or cost_s < 0:
            raise SkillError('invalid_task_graph_input', f'travel_cost[{k}].cost_s must be finite and >= 0.')
        bidir = True if edge.get('bidirectional') is None else bool(edge.get('bidirectional'))
        i, j = idx[u], idx[v]
        if cost_s < dist[i][j]:
            dist[i][j] = cost_s
            direct_source[(u, v)] = 'declared'
        if bidir and cost_s < dist[j][i]:
            dist[j][i] = cost_s
            direct_source[(v, u)] = 'declared'

    assumptions: list[str] = []
    if measured_edges:
        for (u, v), meas_s in sorted(measured_edges.items()):
            if u in idx and v in idx and u != v and math.isfinite(meas_s) and meas_s >= 0:
                i, j = idx[u], idx[v]
                dist[i][j] = float(meas_s)
                direct_source[(u, v)] = 'measured_public_receipt'
                if (v, u) not in measured_edges:
                    dist[j][i] = float(meas_s)
                    direct_source[(v, u)] = 'measured_public_receipt_symmetric'

    for m in range(n):
        for i in range(n):
            if not math.isfinite(dist[i][m]):
                continue
            for j in range(n):
                cand = dist[i][m] + dist[m][j]
                if cand < dist[i][j] - 1e-9:
                    dist[i][j] = cand
                    direct_source[(location_ids[i], location_ids[j])] = 'shortest_path_closure'

    out: dict[tuple[str, str], float] = {}
    for i, u in enumerate(location_ids):
        for j, v in enumerate(location_ids):
            if math.isfinite(dist[i][j]):
                out[(u, v)] = round(dist[i][j], 4)
            else:
                out[(u, v)] = DEFAULT_UNCONNECTED_TRAVEL_S
                direct_source[(u, v)] = 'default_fallback'
                if i < j:
                    assumptions.append(
                        f'No travel_cost path between {u} and {v}; using default {DEFAULT_UNCONNECTED_TRAVEL_S:.1f}s.'
                    )
    return out, direct_source, tuple(assumptions)


def compile_task_graph(
    spec: dict[str, Any],
    *,
    measured_travel_s: dict[tuple[str, str], float] | None = None,
    initial_clock_s: float = 0.0,
) -> CompiledTaskGraph:
    if not isinstance(spec, dict):
        raise SkillError('invalid_task_graph_input', 'Task graph specification must be an object.')

    locations: dict[str, LocationSpec] = {}
    raw_locs = spec.get('locations') or []
    if not isinstance(raw_locs, list):
        raise SkillError('invalid_task_graph_input', 'locations must be a list.')
    for i, entry in enumerate(raw_locs):
        if isinstance(entry, str):
            lid = _clean_id(entry, f'locations[{i}]')
            loc_spec = LocationSpec(id=lid)
        elif isinstance(entry, dict):
            lid = _clean_id(entry.get('id'), f'locations[{i}].id')
            cap_raw = entry.get('capacity')
            cap: int | None = None
            if cap_raw is not None:
                if isinstance(cap_raw, bool) or not isinstance(cap_raw, int) or cap_raw < 0:
                    raise SkillError('invalid_task_graph_input', f'locations[{i}].capacity must be an integer >= 0.')
                cap = cap_raw
            pp = _parse_predicate_list(entry.get('place_preconditions'), f'locations[{i}].place_preconditions')
            loc_spec = LocationSpec(id=lid, capacity=cap, place_preconditions=pp)
        else:
            raise SkillError('invalid_task_graph_input', f'locations[{i}] must be an object or string.')
        locations[loc_spec.id] = loc_spec

    agent_loc_raw = spec.get('agent_location')
    if agent_loc_raw is not None:
        agent_loc = _clean_id(agent_loc_raw, 'agent_location')
        if agent_loc not in locations:
            locations[agent_loc] = LocationSpec(id=agent_loc)
    elif locations:
        agent_loc = next(iter(locations))
    else:
        agent_loc = 'origin'
        locations[agent_loc] = LocationSpec(id=agent_loc)

    # Also auto-register any measured locations referenced by travel edges if needed
    raw_edges_in = spec.get('travel_cost') or []
    norm_edges: list[dict[str, Any]] = []
    if isinstance(raw_edges_in, dict):
        for pair_key, val in raw_edges_in.items():
            if isinstance(pair_key, tuple) and len(pair_key) == 2:
                norm_edges.append({'from_location': pair_key[0], 'to_location': pair_key[1], 'cost_s': val})
            elif isinstance(pair_key, str) and '->' in pair_key:
                u_part, v_part = pair_key.split('->', 1)
                norm_edges.append({'from_location': u_part.strip(), 'to_location': v_part.strip(), 'cost_s': val})
            else:
                raise SkillError('invalid_task_graph_input', f'Invalid travel_cost key {pair_key!r}.')
    elif isinstance(raw_edges_in, list):
        norm_edges = list(raw_edges_in)
    else:
        raise SkillError('invalid_task_graph_input', 'travel_cost must be a list of edges.')

    travel_s, travel_sources, assumptions = _compute_all_pairs_travel(
        list(locations.keys()), norm_edges, measured_edges=measured_travel_s
    )

    # Parse state_vars
    state_vars: dict[str, StateVarSpec] = {}
    raw_vars = spec.get('state_vars') or []
    if isinstance(raw_vars, dict):
        for k, v in raw_vars.items():
            vname = _clean_id(k, 'state_vars.key')
            if isinstance(v, dict) and ('unknown' in v or 'domain' in v or 'value' in v or 'flag' in v or 'label' in v):
                unknown = bool(v.get('unknown', False))
                dom_raw = v.get('domain') if v.get('domain') is not None else v.get('label_domain')
                dom = tuple(_canonical_scalar(x, f'state_vars.{vname}.domain') for x in (dom_raw or []))
                probs_raw = v.get('probabilities')
                probs = tuple(float(p) for p in probs_raw) if probs_raw else ()
                val = None if unknown else _extract_typed_value(v, f'state_vars.{vname}', allow_none=True)
                state_vars[vname] = StateVarSpec(name=vname, known=not unknown, value=val, domain=dom, probabilities=probs)
            elif v == 'unknown':
                state_vars[vname] = StateVarSpec(name=vname, known=False, value=None)
            else:
                state_vars[vname] = StateVarSpec(name=vname, known=True, value=_canonical_scalar(v, f'state_vars.{vname}'))
    elif isinstance(raw_vars, list):
        for i, entry in enumerate(raw_vars):
            if not isinstance(entry, dict):
                raise SkillError('invalid_task_graph_input', f'state_vars[{i}] must be an object.')
            vname = _clean_id(entry.get('name'), f'state_vars[{i}].name')
            unknown = bool(entry.get('unknown', False))
            num_dom = entry.get('domain')
            lbl_dom = entry.get('label_domain')
            if num_dom is not None and lbl_dom is not None:
                raise SkillError('invalid_task_graph_input', f'state_vars[{i}] cannot set both domain and label_domain.')
            dom_src = num_dom if num_dom is not None else (lbl_dom or [])
            dom = tuple(_canonical_scalar(x, f'state_vars[{i}].domain') for x in dom_src)
            probs_raw = entry.get('probabilities')
            probs: tuple[float, ...] = ()
            if probs_raw is not None:
                if len(probs_raw) != len(dom) or not dom:
                    raise SkillError('invalid_task_graph_input', f'state_vars[{i}].probabilities must match domain length.')
                total_p = sum(float(p) for p in probs_raw)
                if total_p <= 0 or any(float(p) < 0 for p in probs_raw):
                    raise SkillError('invalid_task_graph_input', f'state_vars[{i}].probabilities must be non-negative and sum > 0.')
                probs = tuple(float(p) / total_p for p in probs_raw)
            val = _extract_typed_value(entry, f'state_vars[{i}]', allow_none=unknown)
            state_vars[vname] = StateVarSpec(name=vname, known=not unknown, value=val, domain=dom, probabilities=probs)
    else:
        raise SkillError('invalid_task_graph_input', 'state_vars must be a list or mapping.')

    # Parse entities
    entities: dict[str, EntitySpec] = {}
    raw_entities = spec.get('entities') or []
    if not isinstance(raw_entities, list):
        raise SkillError('invalid_task_graph_input', 'entities must be a list.')
    for i, ent in enumerate(raw_entities):
        if not isinstance(ent, dict):
            raise SkillError('invalid_task_graph_input', f'entities[{i}] must be an object.')
        eid = _clean_id(ent.get('id'), f'entities[{i}].id')
        if eid in locations:
            raise SkillError('invalid_task_graph_input', f'Entity id {eid!r} collides with a location id.')
        eloc_raw = ent.get('location')
        eloc = _clean_id(eloc_raw, f'entities[{i}].location') if eloc_raw is not None else agent_loc
        movable = bool(ent.get('movable', False))
        cap_raw = ent.get('capacity')
        cap = None
        if cap_raw is not None:
            if isinstance(cap_raw, bool) or not isinstance(cap_raw, int) or cap_raw < 0:
                raise SkillError('invalid_task_graph_input', f'entities[{i}].capacity must be an integer >= 0.')
            cap = cap_raw
        size_raw = ent.get('size')
        size = 1 if size_raw is None else int(size_raw)
        if isinstance(size_raw, bool) or size < 0:
            raise SkillError('invalid_task_graph_input', f'entities[{i}].size must be an integer >= 0.')
        pp = _parse_predicate_list(ent.get('place_preconditions'), f'entities[{i}].place_preconditions')
        entities[eid] = EntitySpec(
            id=eid, location=eloc, movable=movable, capacity=cap, size=size, place_preconditions=pp
        )

    hand_raw = spec.get('hand_entity')
    hand_entity = _clean_id(hand_raw, 'hand_entity') if hand_raw is not None else None
    if hand_entity == 'empty':
        hand_entity = None
    for eid, espec in list(entities.items()):
        if espec.location == 'hand':
            if hand_entity is not None and hand_entity != eid:
                raise SkillError('invalid_task_graph_input', 'Single hand cannot hold multiple entities simultaneously.')
            hand_entity = eid
        elif espec.location != 'unknown' and espec.location not in locations and espec.location not in entities:
            raise SkillError(
                'invalid_task_graph_input',
                f'Entity {eid!r} initial location {espec.location!r} is neither a declared location nor an entity.',
            )
    if hand_entity is not None:
        if hand_entity not in entities:
            raise SkillError('invalid_task_graph_input', f'hand_entity {hand_entity!r} must be declared in entities.')
        entities[hand_entity] = EntitySpec(
            id=hand_entity,
            location='hand',
            movable=entities[hand_entity].movable,
            capacity=entities[hand_entity].capacity,
            size=entities[hand_entity].size,
            place_preconditions=entities[hand_entity].place_preconditions,
        )

    # Parse operations
    operations: dict[str, OperationSpec] = {}
    implicit_goal_preds: list[Predicate] = []
    raw_ops = spec.get('operations') or []
    if not isinstance(raw_ops, list):
        raise SkillError('invalid_task_graph_input', 'operations must be a list.')
    for i, op in enumerate(raw_ops):
        if not isinstance(op, dict):
            raise SkillError('invalid_task_graph_input', f'operations[{i}] must be an object.')
        oid = _clean_id(op.get('id'), f'operations[{i}].id')
        if oid in operations:
            raise SkillError('invalid_task_graph_input', f'Duplicate operation id {oid!r}.')
        oloc_raw = op.get('location')
        oloc = _clean_id(oloc_raw, f'operations[{i}].location') if oloc_raw is not None else None
        if oloc is not None and oloc not in locations:
            raise SkillError('invalid_task_graph_input', f'Operation {oid!r} references undeclared location {oloc!r}.')
        pre = _parse_predicate_list(op.get('preconditions'), f'operations[{i}].preconditions')
        eff = _parse_effect_list(op.get('effects'), f'operations[{i}].effects')
        peff = _parse_effect_list(op.get('passive_effects'), f'operations[{i}].passive_effects')

        act_raw = op.get('active_duration_s')
        act_s = DEFAULT_MANIP_DURATION_S if act_raw is None else float(act_raw)
        pas_raw = op.get('passive_duration_s')
        pas_s = 0.0 if pas_raw is None else float(pas_raw)
        if not math.isfinite(act_s) or act_s < 0 or not math.isfinite(pas_s) or pas_s < 0:
            raise SkillError('invalid_task_graph_input', f'operations[{i}] durations must be finite and >= 0.')

        occ_raw = op.get('occupies_hand')
        occ_hand = True if occ_raw is None else bool(occ_raw)
        res_unk = tuple(_clean_id(u, f'operations[{i}].resolves_unknowns') for u in (op.get('resolves_unknowns') or []))
        rep_raw = op.get('repeatable')
        repeatable = True if rep_raw is None else bool(rep_raw)
        status = str(op.get('status') or 'pending')
        if status not in {'pending', 'active_passive', 'done'}:
            raise SkillError('invalid_task_graph_input', f'operations[{i}].status {status!r} is invalid.')
        started_raw = op.get('started_at_s')
        started_s = float(started_raw) if started_raw is not None else None

        # If passive_duration_s > 0 and passive_effects was omitted, effects materialize at the end of passive evolution
        if pas_s > 0 and not peff and eff:
            imm_eff: tuple[Effect, ...] = ()
            pas_eff = eff
        else:
            imm_eff = eff
            pas_eff = peff

        # Synthesize completion state variable only if operation has no effects and resolves no unknowns
        if not imm_eff and not pas_eff and not res_unk:
            synth_var = f'__op_done:{oid}__'
            state_vars.setdefault(synth_var, StateVarSpec(name=synth_var, known=True, value=(status == 'done')))
            if pas_s > 0:
                pas_eff = (Effect(var=synth_var, op='set', target=True),)
            else:
                imm_eff = (Effect(var=synth_var, op='set', target=True),)
            repeatable = False
            implicit_goal_preds.append(Predicate(var=synth_var, op='eq', target=True))

        # Auto-register any referenced plain state_vars that were not explicitly listed (default 0 / False)
        for p in pre:
            if not _is_special_var(p.var) and p.var not in state_vars:
                default_v: Any = False if isinstance(p.target, bool) else (0 if isinstance(p.target, (int, float)) else '')
                state_vars[p.var] = StateVarSpec(name=p.var, known=True, value=default_v)
        for e_item in (*imm_eff, *pas_eff):
            if not _is_special_var(e_item.var) and e_item.var not in state_vars:
                default_v = False if isinstance(e_item.target, bool) else (0 if isinstance(e_item.target, (int, float)) else '')
                state_vars[e_item.var] = StateVarSpec(name=e_item.var, known=True, value=default_v)
        for u_var in res_unk:
            if not _is_special_var(u_var) and u_var not in state_vars:
                state_vars[u_var] = StateVarSpec(name=u_var, known=False, value=None)

        operations[oid] = OperationSpec(
            id=oid,
            location=oloc,
            preconditions=pre,
            immediate_effects=imm_eff,
            passive_effects=pas_eff,
            active_duration_s=act_s,
            occupies_hand=occ_hand,
            passive_duration_s=pas_s,
            resolves_unknowns=res_unk,
            repeatable=repeatable,
            status=status,
            started_at_s=started_s,
        )

    # Parse goals
    raw_goals = spec.get('goals')
    goal_preds: list[Predicate] = []
    goal_ent_locs: list[tuple[str, str]] = []
    req_empty_hand = False
    if isinstance(raw_goals, dict):
        if 'predicates' in raw_goals or 'entity_locations' in raw_goals or 'require_empty_hand' in raw_goals:
            goal_preds.extend(_parse_predicate_list(raw_goals.get('predicates'), 'goals.predicates'))
            raw_el = raw_goals.get('entity_locations')
            if isinstance(raw_el, dict):
                for eid, dst in raw_el.items():
                    goal_ent_locs.append((_clean_id(eid, 'goals.entity_locations'), _clean_id(dst, 'goals.entity_locations')))
            elif isinstance(raw_el, list):
                for idx, row in enumerate(raw_el):
                    if not isinstance(row, dict):
                        raise SkillError('invalid_task_graph_input', f'goals.entity_locations[{idx}] must be an object.')
                    goal_ent_locs.append((
                        _clean_id(row.get('entity_id'), f'goals.entity_locations[{idx}].entity_id'),
                        _clean_id(row.get('location'), f'goals.entity_locations[{idx}].location'),
                    ))
            req_empty_hand = bool(raw_goals.get('require_empty_hand', False))
        else:
            # Shorthand dictionary of goal predicates
            for p in _parse_predicate_list(raw_goals, 'goals'):
                ent_id = _extract_entity_loc_var(p.var)
                if ent_id is not None and p.op == 'eq' and isinstance(p.target, str):
                    goal_ent_locs.append((ent_id, p.target))
                else:
                    goal_preds.append(p)
    elif isinstance(raw_goals, list):
        goal_preds.extend(_parse_predicate_list(raw_goals, 'goals'))
    elif raw_goals is not None:
        raise SkillError('invalid_task_graph_input', 'goals must be an object or list.')

    if not goal_preds and not goal_ent_locs and implicit_goal_preds:
        goal_preds.extend(implicit_goal_preds)

    for eid, dst in goal_ent_locs:
        if eid not in entities:
            raise SkillError('invalid_task_graph_input', f'Goal references undeclared entity {eid!r}.')
        if dst != 'hand' and dst not in locations and dst not in entities:
            raise SkillError('invalid_task_graph_input', f'Goal destination {dst!r} for {eid!r} is undeclared.')

    for p in goal_preds:
        if not _is_special_var(p.var) and p.var not in state_vars:
            default_v = False if isinstance(p.target, bool) else (0 if isinstance(p.target, (int, float)) else '')
            state_vars[p.var] = StateVarSpec(name=p.var, known=True, value=default_v)

    manip_s_raw = spec.get('manip_duration_s')
    manip_s = DEFAULT_MANIP_DURATION_S if manip_s_raw is None else float(manip_s_raw)
    if not math.isfinite(manip_s) or manip_s < 0:
        raise SkillError('invalid_task_graph_input', 'manip_duration_s must be finite and >= 0.')

    return CompiledTaskGraph(
        agent_location=agent_loc,
        hand_entity=hand_entity,
        locations=locations,
        travel_s=travel_s,
        travel_sources=travel_sources,
        assumptions=assumptions,
        state_vars=state_vars,
        entities=entities,
        operations=operations,
        goals=GoalSpec(
            predicates=tuple(goal_preds),
            entity_locations=tuple(goal_ent_locs),
            require_empty_hand=req_empty_hand,
        ),
        manip_duration_s=manip_s,
        initial_clock_s=float(initial_clock_s),
    )


def _is_special_var(var_name: str) -> bool:
    return (
        var_name == 'hand'
        or var_name.startswith('occupancy:')
        or var_name.startswith('loc:')
        or (var_name.startswith('entity:') and var_name.endswith('.location'))
    )


def _extract_entity_loc_var(var_name: str) -> str | None:
    if var_name.startswith('loc:'):
        return var_name[4:]
    if var_name.startswith('entity:') and var_name.endswith('.location'):
        return var_name[len('entity:'):-len('.location')]
    return None


def _host_occupancy(target_id: str, entity_loc: dict[str, str], entities: dict[str, EntitySpec]) -> int:
    return sum(entities[eid].size for eid, loc in entity_loc.items() if loc == target_id)


def _resolve_resting_location(host_id: str, entity_loc: dict[str, str], locations: dict[str, LocationSpec]) -> str | None:
    """Return spatial location ID where host_id currently rests, or None if in hand or unknown."""
    cur = host_id
    visited: set[str] = set()
    while cur not in locations:
        if cur in {'hand', 'unknown'} or cur in visited or cur not in entity_loc:
            return None
        visited.add(cur)
        cur = entity_loc[cur]
    return cur


def _eval_predicate(
    pred: Predicate,
    vars_map: dict[str, Any],
    entity_loc: dict[str, str],
    hand: str | None,
    unknown_vars: frozenset[str],
    graph: CompiledTaskGraph,
) -> bool:
    v = pred.var
    if v == 'hand':
        if pred.op == 'known':
            return True
        if pred.target is None or pred.target == 'empty' or pred.target is False:
            is_empty = hand is None
            return is_empty if pred.op == 'eq' else not is_empty
        if pred.target is True:
            has_item = hand is not None
            return has_item if pred.op == 'eq' else not has_item
        match = hand == str(pred.target)
        return match if pred.op == 'eq' else not match

    if v.startswith('occupancy:'):
        host_id = v.split(':', 1)[1]
        occ = _host_occupancy(host_id, entity_loc, graph.entities)
        if pred.op == 'known':
            return True
        return _compare_values(occ, pred.op, pred.target)

    ent_id = _extract_entity_loc_var(v)
    if ent_id is not None:
        if ent_id not in entity_loc:
            return False
        cur_loc = entity_loc[ent_id]
        if cur_loc == 'unknown' or v in unknown_vars:
            return False
        if pred.op == 'known':
            return True
        return _compare_values(cur_loc, pred.op, pred.target)

    if v in unknown_vars:
        return False
    if pred.op == 'known':
        return v in vars_map and vars_map[v] is not None
    if v not in vars_map or vars_map[v] is None:
        return False
    return _compare_values(vars_map[v], pred.op, pred.target)


def _compare_values(actual: Any, op: str, target: Any) -> bool:
    if op == 'eq':
        return _values_equal(actual, target)
    if op == 'ne':
        return not _values_equal(actual, target)
    if isinstance(actual, bool) or isinstance(target, bool) or not isinstance(actual, (int, float)) or not isinstance(target, (int, float)):
        return False
    a_f, t_f = float(actual), float(target)
    if op == 'le':
        return a_f <= t_f + 1e-9
    if op == 'lt':
        return a_f < t_f - 1e-9
    if op == 'ge':
        return a_f >= t_f - 1e-9
    if op == 'gt':
        return a_f > t_f + 1e-9
    return False


def _apply_effects(
    effects: tuple[Effect, ...],
    vars_map: dict[str, Any],
    entity_loc: dict[str, str],
    hand: str | None,
    unknown_vars: set[str],
    graph: CompiledTaskGraph,
) -> tuple[bool, str | None]:
    """Mutate copies of vars_map, entity_loc, unknown_vars in-place; return (ok, new_hand)."""
    for eff in effects:
        v = eff.var
        if v == 'hand':
            new_h = None if eff.target in (None, 'empty', False) else str(eff.target)
            if new_h is not None and new_h not in graph.entities:
                return False, hand
            hand = new_h
            if new_h is not None:
                entity_loc[new_h] = 'hand'
            continue

        ent_id = _extract_entity_loc_var(v)
        if ent_id is not None:
            if ent_id not in entity_loc:
                return False, hand
            dest = str(eff.target)
            if dest == 'hand':
                if hand is not None and hand != ent_id:
                    return False, hand
                hand = ent_id
                entity_loc[ent_id] = 'hand'
            else:
                if not _can_place_into_host(ent_id, dest, vars_map, entity_loc, hand, frozenset(unknown_vars), graph):
                    return False, hand
                if hand == ent_id:
                    hand = None
                entity_loc[ent_id] = dest
            unknown_vars.discard(v)
            continue

        unknown_vars.discard(v)
        if eff.op == 'set':
            vars_map[v] = eff.target
        else:
            cur_raw = vars_map.get(v, 0)
            if isinstance(cur_raw, bool) or not isinstance(cur_raw, (int, float)):
                return False, hand
            cur_num = float(cur_raw)
            tgt_num = float(eff.target)
            if eff.op == 'add':
                res = cur_num + tgt_num
            elif eff.op == 'max':
                res = max(cur_num, tgt_num)
            elif eff.op == 'min':
                res = min(cur_num, tgt_num)
            else:
                return False, hand
            vars_map[v] = int(round(res)) if abs(res - round(res)) <= 1e-9 else round(res, 6)
    return True, hand


def _can_place_into_host(
    eid: str,
    dest: str,
    vars_map: dict[str, Any],
    entity_loc: dict[str, str],
    hand: str | None,
    unknown_vars: frozenset[str],
    graph: CompiledTaskGraph,
) -> bool:
    if dest == eid:
        return False
    ent = graph.entities[eid]
    if dest in graph.locations:
        loc_spec = graph.locations[dest]
        if loc_spec.capacity is not None:
            occ = _host_occupancy(dest, entity_loc, graph.entities)
            if entity_loc.get(eid) != dest and occ + ent.size > loc_spec.capacity:
                return False
        for pp in loc_spec.place_preconditions:
            if not _eval_predicate(pp, vars_map, entity_loc, hand, unknown_vars, graph):
                return False
        return True
    if dest in graph.entities:
        host_ent = graph.entities[dest]
        if host_ent.capacity is None or host_ent.capacity < ent.size or ent.size <= 0:
            return False
        # Prevent nesting cycles (e.g., placing a container inside one of its own descendants)
        cur = dest
        while cur in entity_loc:
            nxt = entity_loc[cur]
            if nxt == eid:
                return False
            if nxt in graph.locations or nxt in {'hand', 'unknown'}:
                break
            cur = nxt
        occ = _host_occupancy(dest, entity_loc, graph.entities)
        if entity_loc.get(eid) != dest and occ + ent.size > host_ent.capacity:
            return False
        for pp in host_ent.place_preconditions:
            if not _eval_predicate(pp, vars_map, entity_loc, hand, unknown_vars, graph):
                return False
        return True
    return False


def _advance_clock_and_fire_passive(
    vars_map: dict[str, Any],
    entity_loc: dict[str, str],
    hand: str | None,
    unknown_vars: set[str],
    passive_events: tuple[PassiveEvent, ...],
    new_clock_s: float,
    graph: CompiledTaskGraph,
) -> tuple[bool, str | None, tuple[PassiveEvent, ...]]:
    remaining: list[PassiveEvent] = []
    for ev in sorted(passive_events, key=lambda x: x.ready_at_s):
        if ev.ready_at_s <= new_clock_s + 1e-9:
            ok, hand = _apply_effects(ev.effects, vars_map, entity_loc, hand, unknown_vars, graph)
            if not ok:
                return False, hand, ()
        else:
            remaining.append(ev)
    return True, hand, tuple(remaining)


def _goals_satisfied(world: WorldState, graph: CompiledTaskGraph) -> bool:
    if graph.goals.require_empty_hand and world.hand is not None:
        return False
    eloc = world.entity_map()
    for eid, dst in graph.goals.entity_locations:
        if eloc.get(eid) != dst:
            return False
    vmap = world.vars_map()
    for pred in graph.goals.predicates:
        if not _eval_predicate(pred, vmap, eloc, world.hand, world.unknown_vars, graph):
            return False
    return True


@dataclass
class _SearchAnalyzer:
    """Precomputed structural invariants for 100% general, optimality-preserving pruning & heuristics."""

    graph: CompiledTaskGraph
    goal_entity_map: dict[str, str] = field(init=False)
    referenced_entities: set[str] = field(init=False)
    occupancy_checked_hosts: set[str] = field(init=False)
    explicit_entity_writers: set[str] = field(init=False)
    symmetry_groups: list[tuple[str, ...]] = field(init=False)
    entity_to_group: dict[str, int] = field(init=False)
    sym_travel: dict[tuple[str, str], float] = field(init=False)
    unique_var_producer_cost: dict[tuple[str, Any], tuple[float, str | None, frozenset[str]]] = field(init=False)

    def __post_init__(self) -> None:
        g = self.graph
        self.goal_entity_map = dict(g.goals.entity_locations)
        for p in g.goals.predicates:
            eid = _extract_entity_loc_var(p.var)
            if eid is not None and p.op == 'eq' and isinstance(p.target, str):
                self.goal_entity_map[eid] = p.target

        self.referenced_entities = set()
        self.occupancy_checked_hosts = set()
        self.explicit_entity_writers = set()

        all_preds = [
            *g.goals.predicates,
            *(p for loc in g.locations.values() for p in loc.place_preconditions),
            *(p for ent in g.entities.values() for p in ent.place_preconditions),
            *(p for op in g.operations.values() for p in op.preconditions),
        ]
        for p in all_preds:
            if p.var.startswith('occupancy:'):
                self.occupancy_checked_hosts.add(p.var.split(':', 1)[1])
            eid = _extract_entity_loc_var(p.var)
            if eid is not None:
                self.referenced_entities.add(eid)
            if p.var == 'hand' and isinstance(p.target, str) and p.target in g.entities:
                self.referenced_entities.add(p.target)

        for op in g.operations.values():
            for u in op.resolves_unknowns:
                eid = _extract_entity_loc_var(u)
                if eid is not None:
                    self.referenced_entities.add(eid)
            for eff in (*op.immediate_effects, *op.passive_effects):
                eid = _extract_entity_loc_var(eff.var)
                if eid is not None:
                    self.referenced_entities.add(eid)
                    self.explicit_entity_writers.add(eid)
                if eff.var == 'hand' and isinstance(eff.target, str) and eff.target in g.entities:
                    self.referenced_entities.add(eff.target)
                    self.explicit_entity_writers.add(eff.target)

        # Symmetry groups: indistinguishable leaf entities (capacity in {None, 0}, not referenced in ops)
        sym_groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for eid, ent in sorted(g.entities.items()):
            if (
                ent.movable
                and not ent.capacity
                and eid not in self.referenced_entities
                and not ent.place_preconditions
            ):
                sig = (ent.movable, ent.size, self.goal_entity_map.get(eid))
                sym_groups[sig].append(eid)
        self.symmetry_groups = [tuple(members) for members in sym_groups.values() if len(members) >= 2]
        self.entity_to_group = {
            eid: g_idx for g_idx, members in enumerate(self.symmetry_groups) for eid in members
        }

        self.sym_travel = {}
        for u in g.locations:
            for v in g.locations:
                self.sym_travel[(u, v)] = min(g.travel_s[(u, v)], g.travel_s[(v, u)])

        # Unique producer lower bounds for goal eq predicates
        self.unique_var_producer_cost = {}
        for p in g.goals.predicates:
            if p.op != 'eq' or _is_special_var(p.var):
                continue
            producers: list[OperationSpec] = []
            for op in g.operations.values():
                if op.status == 'done':
                    continue
                for eff in (*op.immediate_effects, *op.passive_effects):
                    if eff.var == p.var and (eff.op != 'set' or _values_equal(eff.target, p.target)):
                        producers.append(op)
                        break
            if producers:
                min_act = min(op.active_duration_s for op in producers)
                locs = {op.location for op in producers}
                common_loc = next(iter(locs)) if len(locs) == 1 else None
                self.unique_var_producer_cost[(p.var, _state_atom(p.target))] = (
                    min_act,
                    common_loc,
                    frozenset(op.id for op in producers),
                )

    def is_goal_settled_leaf(self, eid: str, eloc: dict[str, str], vars_map: dict[str, Any], hand: str | None, unknown_vars: frozenset[str]) -> bool:
        """True when leaf entity eid is already at an unconstrained final goal location and never needs re-picking."""
        ent = self.graph.entities[eid]
        if ent.capacity:
            return False
        if eid in self.referenced_entities:
            return False
        goal_dst = self.goal_entity_map.get(eid)
        if goal_dst is None or eloc.get(eid) != goal_dst:
            return False
        if goal_dst not in self.graph.locations:
            return False
        loc_spec = self.graph.locations[goal_dst]
        if loc_spec.capacity is not None or goal_dst in self.occupancy_checked_hosts:
            return False
        for pp in loc_spec.place_preconditions:
            if not _eval_predicate(pp, vars_map, eloc, hand, unknown_vars, self.graph):
                return False
        return True

    def is_host_constrained(self, host_id: str) -> bool:
        if host_id in self.occupancy_checked_hosts:
            return True
        if host_id in self.graph.locations:
            loc = self.graph.locations[host_id]
            return loc.capacity is not None or bool(loc.place_preconditions)
        if host_id in self.graph.entities:
            ent = self.graph.entities[host_id]
            return ent.capacity is not None or bool(ent.place_preconditions)
        return True

    def discrete_key(self, world: WorldState) -> tuple[Any, ...]:
        eloc = dict(world.entity_loc)
        grouped_atoms: list[Any] = []
        grouped_members = set(self.entity_to_group)
        for members in self.symmetry_groups:
            grouped_atoms.append(tuple(sorted(eloc[m] for m in members)))
        indiv_atoms = tuple((eid, eloc[eid]) for eid in sorted(eloc) if eid not in grouped_members)
        canon_hand = (
            f'__symgroup_{self.entity_to_group[world.hand]}__'
            if world.hand is not None and world.hand in self.entity_to_group
            else world.hand
        )
        vars_atoms = tuple((k, _state_atom(v)) for k, v in world.vars)
        pas_ids = tuple((ev.op_id, tuple((e.var, e.op, _state_atom(e.target)) for e in ev.effects)) for ev in world.passive_events)
        return (
            world.agent_loc,
            canon_hand,
            indiv_atoms,
            tuple(grouped_atoms),
            vars_atoms,
            tuple(sorted(world.unknown_vars)),
            pas_ids,
            tuple(sorted(world.executed_once)),
        )

    def admissible_heuristic(self, world: WorldState) -> float:
        """100% domain-independent admissible lower bound on remaining makespan."""
        g = self.graph
        eloc = world.entity_map()
        vmap = world.vars_map()
        manip_lb = 0.0
        must_visit: set[str] = set()
        unit_s = g.manip_duration_s

        # 1. Misplaced entities manipulation & spatial visit bounds
        for eid, goal_dst in self.goal_entity_map.items():
            cur = eloc.get(eid)
            if cur == goal_dst or eid in self.explicit_entity_writers:
                continue
            if cur == 'hand':
                manip_lb += unit_s
                if goal_dst in g.locations:
                    must_visit.add(goal_dst)
                elif goal_dst in g.entities:
                    rloc = _resolve_resting_location(goal_dst, eloc, g.locations)
                    if rloc is not None:
                        must_visit.add(rloc)
            else:
                manip_lb += 2.0 * unit_s
                if cur in g.locations:
                    must_visit.add(cur)
                elif cur in g.entities:
                    if eloc.get(cur) != 'hand':
                        rloc = _resolve_resting_location(cur, eloc, g.locations)
                        if rloc is not None:
                            must_visit.add(rloc)
                if goal_dst in g.locations:
                    must_visit.add(goal_dst)

        if g.goals.require_empty_hand and world.hand is not None and world.hand not in self.goal_entity_map:
            if world.hand not in self.explicit_entity_writers:
                manip_lb += unit_s

        # 2. Unsatisfied goal state_vars active work & visit bounds (disjoint producer sets only)
        pending_passive_vars = {
            eff.var for ev in world.passive_events for eff in ev.effects
        }
        covered_producers: set[str] = set()
        for p in g.goals.predicates:
            if _eval_predicate(p, vmap, eloc, world.hand, world.unknown_vars, g):
                continue
            if p.var in pending_passive_vars:
                continue
            key = (p.var, _state_atom(p.target))
            if key in self.unique_var_producer_cost:
                min_act, common_loc, prod_ids = self.unique_var_producer_cost[key]
                if prod_ids.isdisjoint(covered_producers):
                    covered_producers.update(prod_ids)
                    manip_lb += min_act
                if common_loc is not None:
                    must_visit.add(common_loc)

        # 3. Prim's MST lower bound on {agent_loc} | must_visit
        travel_lb = 0.0
        nodes = list(must_visit - {world.agent_loc})
        if nodes:
            in_tree = [world.agent_loc]
            rem = set(nodes)
            while rem:
                best_d = math.inf
                best_v = None
                for v in rem:
                    for u in in_tree:
                        d = self.sym_travel[(u, v)]
                        if d < best_d:
                            best_d = d
                            best_v = v
                if best_v is None or not math.isfinite(best_d):
                    break
                travel_lb += best_d
                in_tree.append(best_v)
                rem.remove(best_v)

        # 4. Active passive evolution lower bound
        passive_lb = 0.0
        for ev in world.passive_events:
            # Check if any goal still depends on ev's effects
            for eff in ev.effects:
                for p in g.goals.predicates:
                    if p.var == eff.var and not _eval_predicate(p, vmap, eloc, world.hand, world.unknown_vars, g):
                        passive_lb = max(passive_lb, max(0.0, ev.ready_at_s - world.clock_s))

        return max(manip_lb + travel_lb, passive_lb)


def _initial_worlds(graph: CompiledTaskGraph) -> list[tuple[float, WorldState, dict[str, Any]]]:
    """Build initial concrete state(s); returns weighted belief worlds if finite-domain unknowns exist."""
    base_vars: dict[str, Any] = {}
    open_unknowns: set[str] = set()
    finite_unknowns: list[tuple[str, tuple[Any, ...], tuple[float, ...]]] = []

    for vname, vspec in sorted(graph.state_vars.items()):
        if vspec.known:
            base_vars[vname] = vspec.value
        elif vspec.domain:
            probs = vspec.probabilities or tuple(1.0 / len(vspec.domain) for _ in vspec.domain)
            finite_unknowns.append((vname, vspec.domain, probs))
        else:
            base_vars[vname] = None
            open_unknowns.add(vname)

    eloc_base = tuple(sorted((eid, ent.location) for eid, ent in graph.entities.items()))
    for eid, loc in eloc_base:
        if loc == 'unknown':
            open_unknowns.add(f'entity:{eid}.location')

    active_passive: list[PassiveEvent] = []
    done_once: set[str] = set()
    for oid, op in sorted(graph.operations.items()):
        if op.status == 'done':
            done_once.add(oid)
        elif op.status == 'active_passive' and op.passive_duration_s > 0:
            start_s = op.started_at_s if op.started_at_s is not None else graph.initial_clock_s
            ready_s = start_s + op.passive_duration_s
            if ready_s > graph.initial_clock_s + 1e-9:
                active_passive.append(PassiveEvent(ready_at_s=ready_s, op_id=oid, effects=op.passive_effects))
            else:
                v_tmp = dict(base_vars)
                e_tmp = dict(eloc_base)
                u_tmp = set(open_unknowns)
                _, _ = _apply_effects(op.passive_effects, v_tmp, e_tmp, graph.hand_entity, u_tmp, graph)
                base_vars = v_tmp
                eloc_base = tuple(sorted(e_tmp.items()))
                open_unknowns = u_tmp
            if not op.repeatable:
                done_once.add(oid)

    active_passive.sort(key=lambda x: x.ready_at_s)

    if not finite_unknowns:
        w0 = WorldState(
            agent_loc=graph.agent_location,
            hand=graph.hand_entity,
            entity_loc=eloc_base,
            vars=tuple(sorted(base_vars.items())),
            unknown_vars=frozenset(open_unknowns),
            passive_events=tuple(active_passive),
            executed_once=frozenset(done_once),
            clock_s=graph.initial_clock_s,
            travel_s=0.0,
        )
        return [(1.0, w0, {})]

    worlds: list[tuple[float, WorldState, dict[str, Any]]] = []
    names = [u[0] for u in finite_unknowns]
    domains = [u[1] for u in finite_unknowns]
    prob_lists = [u[2] for u in finite_unknowns]
    for combo_idx in itertools.product(*(range(len(d)) for d in domains)):
        weight = 1.0
        assignment: dict[str, Any] = {}
        vars_copy = dict(base_vars)
        for dim, val_idx in enumerate(combo_idx):
            val = domains[dim][val_idx]
            weight *= prob_lists[dim][val_idx]
            assignment[names[dim]] = val
            vars_copy[names[dim]] = val
        if weight <= 0:
            continue
        w = WorldState(
            agent_loc=graph.agent_location,
            hand=graph.hand_entity,
            entity_loc=eloc_base,
            vars=tuple(sorted(vars_copy.items())),
            unknown_vars=frozenset(open_unknowns),
            passive_events=tuple(active_passive),
            executed_once=frozenset(done_once),
            clock_s=graph.initial_clock_s,
            travel_s=0.0,
        )
        worlds.append((weight, w, assignment))
    return worlds


def _expand_world(
    world: WorldState,
    analyzer: _SearchAnalyzer,
    *,
    picked_from: dict[str, str] | None = None,
) -> list[tuple[PlanStep, WorldState, dict[str, str]]]:
    """Generate all physically valid successor transitions from `world`."""
    graph = analyzer.graph
    eloc = world.entity_map()
    vmap = world.vars_map()
    successors: list[tuple[PlanStep, WorldState, dict[str, str]]] = []
    p_from = dict(picked_from or {})

    # 1. Explicit candidate operations
    for oid, op in graph.operations.items():
        if op.status == 'done':
            continue
        if not op.repeatable and oid in world.executed_once:
            continue
        if any(ev.op_id == oid for ev in world.passive_events) and not op.repeatable:
            continue

        # Hand constraint: if occupies_hand is True, hand must be empty unless preconditions explicitly require holding an item
        explicit_hand_req = any(p.var == 'hand' for p in op.preconditions)
        if op.occupies_hand and not explicit_hand_req and world.hand is not None:
            continue

        target_loc = op.location if op.location is not None else world.agent_loc
        leg_s = graph.travel_s[(world.agent_loc, target_loc)]
        t_arrive = world.clock_s + leg_s

        v_cur = dict(vmap)
        e_cur = dict(eloc)
        u_cur = set(world.unknown_vars)
        ok_pas, h_cur, rem_pas = _advance_clock_and_fire_passive(
            v_cur, e_cur, world.hand, u_cur, world.passive_events, t_arrive, graph
        )
        if not ok_pas:
            continue

        wait_s = 0.0
        pre_ok = all(_eval_predicate(p, v_cur, e_cur, h_cur, frozenset(u_cur), graph) for p in op.preconditions)
        if not pre_ok and rem_pas:
            # Check if waiting at target_loc for an active passive event unlocks preconditions
            for idx_ev in range(len(rem_pas)):
                cand_t = rem_pas[idx_ev].ready_at_s
                v_try = dict(v_cur)
                e_try = dict(e_cur)
                u_try = set(u_cur)
                ok_w, h_try, rem_try = _advance_clock_and_fire_passive(
                    v_try, e_try, h_cur, u_try, rem_pas, cand_t, graph
                )
                if not ok_w:
                    break
                if all(_eval_predicate(p, v_try, e_try, h_try, frozenset(u_try), graph) for p in op.preconditions):
                    wait_s = max(0.0, cand_t - t_arrive)
                    v_cur, e_cur, u_cur, h_cur, rem_pas = v_try, e_try, u_try, h_try, rem_try
                    pre_ok = True
                    break
        if not pre_ok:
            continue

        t_start = t_arrive + wait_s
        t_end = t_start + op.active_duration_s
        ok_act, h_cur, rem_pas = _advance_clock_and_fire_passive(
            v_cur, e_cur, h_cur, u_cur, rem_pas, t_end, graph
        )
        if not ok_act:
            continue

        ok_eff, h_cur = _apply_effects(op.immediate_effects, v_cur, e_cur, h_cur, u_cur, graph)
        if not ok_eff:
            continue

        resolved_any = False
        for u_name in op.resolves_unknowns:
            if u_name in u_cur:
                u_cur.remove(u_name)
                if not _is_special_var(u_name) and v_cur.get(u_name) is None:
                    v_cur[u_name] = 'observed'
                resolved_any = True
            elif u_name in graph.state_vars and not graph.state_vars[u_name].known:
                resolved_any = True

        pas_ready_s: float | None = None
        new_pas_list = list(rem_pas)
        if op.passive_duration_s > 0 and op.passive_effects:
            pas_ready_s = t_end + op.passive_duration_s
            new_pas_list.append(PassiveEvent(ready_at_s=pas_ready_s, op_id=oid, effects=op.passive_effects))
            new_pas_list.sort(key=lambda x: x.ready_at_s)

        new_exec = world.executed_once | ({oid} if not op.repeatable else frozenset())
        next_world = WorldState(
            agent_loc=target_loc,
            hand=h_cur,
            entity_loc=tuple(sorted(e_cur.items())),
            vars=tuple(sorted(v_cur.items())),
            unknown_vars=frozenset(u_cur),
            passive_events=tuple(new_pas_list),
            executed_once=new_exec,
            clock_s=t_end,
            travel_s=world.travel_s + leg_s,
        )

        # Prune pure no-op transitions that neither changed state, started passive work, nor resolved unknowns
        if (
            not resolved_any
            and pas_ready_s is None
            and next_world.hand == world.hand
            and next_world.entity_loc == world.entity_loc
            and next_world.vars == world.vars
            and next_world.unknown_vars == world.unknown_vars
        ):
            continue

        reads = tuple(sorted({p.var for p in op.preconditions}))
        writes = tuple(sorted(
            {e.var for e in (*op.immediate_effects, *op.passive_effects)} | set(op.resolves_unknowns)
        ))
        step = PlanStep(
            kind='operation',
            op_id=oid,
            from_location=world.agent_loc,
            location=target_loc,
            travel_cost_s=leg_s,
            active_duration_s=op.active_duration_s,
            passive_duration_s=op.passive_duration_s if op.passive_effects else 0.0,
            wait_duration_s=wait_s,
            start_time_s=t_start,
            end_time_s=t_end,
            passive_ready_time_s=pas_ready_s,
            resolves_unknowns=op.resolves_unknowns,
            reads=reads,
            writes=writes,
        )
        successors.append((step, next_world, p_from))

    # 2. Generic physical pick / place transitions for movable entities
    unit_s = graph.manip_duration_s
    if world.hand is None:
        seen_group_host: set[tuple[int, str]] = set()
        for eid, ent in sorted(graph.entities.items()):
            if not ent.movable:
                continue
            cur_host = eloc.get(eid)
            if cur_host in {None, 'hand', 'unknown'}:
                continue
            if analyzer.is_goal_settled_leaf(eid, eloc, vmap, world.hand, world.unknown_vars):
                continue
            g_idx = analyzer.entity_to_group.get(eid)
            if g_idx is not None:
                gh = (g_idx, cur_host)
                if gh in seen_group_host:
                    continue
                seen_group_host.add(gh)

            pick_loc = _resolve_resting_location(cur_host, eloc, graph.locations)
            if pick_loc is None:
                continue

            leg_s = graph.travel_s[(world.agent_loc, pick_loc)]
            t_start = world.clock_s + leg_s
            t_end = t_start + unit_s

            v_cur = dict(vmap)
            e_cur = dict(eloc)
            u_cur = set(world.unknown_vars)
            ok_pas, _, rem_pas = _advance_clock_and_fire_passive(
                v_cur, e_cur, None, u_cur, world.passive_events, t_end, graph
            )
            if not ok_pas:
                continue

            e_cur[eid] = 'hand'
            next_world = WorldState(
                agent_loc=pick_loc,
                hand=eid,
                entity_loc=tuple(sorted(e_cur.items())),
                vars=tuple(sorted(v_cur.items())),
                unknown_vars=frozenset(u_cur),
                passive_events=rem_pas,
                executed_once=world.executed_once,
                clock_s=t_end,
                travel_s=world.travel_s + leg_s,
            )
            reads_list = [f'entity:{eid}.location', 'hand']
            writes_list = [f'entity:{eid}.location', 'hand', f'occupancy:{cur_host}']
            step = PlanStep(
                kind='pick',
                op_id=f'pick:{eid}',
                from_location=world.agent_loc,
                location=pick_loc,
                travel_cost_s=leg_s,
                active_duration_s=unit_s,
                passive_duration_s=0.0,
                wait_duration_s=0.0,
                start_time_s=t_start,
                end_time_s=t_end,
                entity_id=eid,
                target_host=cur_host,
                reads=tuple(reads_list),
                writes=tuple(writes_list),
            )
            next_p_from = dict(p_from)
            next_p_from[eid] = cur_host
            successors.append((step, next_world, next_p_from))
    else:
        held_id = world.hand
        held_ent = graph.entities[held_id]
        origin_host = p_from.get(held_id)
        is_simple_leaf = (not held_ent.capacity) and (held_id not in analyzer.referenced_entities)
        goal_dst = analyzer.goal_entity_map.get(held_id)

        # Determine non-dominated candidate destinations
        candidate_hosts: list[tuple[str, str]] = []
        for lid in graph.locations:
            if is_simple_leaf and goal_dst is not None and lid != goal_dst:
                # Only explore non-goal ground locations if picked from a constrained/checked host or goal_dst is constrained
                if (
                    origin_host is not None
                    and origin_host in graph.locations
                    and not analyzer.is_host_constrained(origin_host)
                    and not analyzer.is_host_constrained(goal_dst)
                ):
                    continue
                if (
                    origin_host is not None
                    and origin_host in graph.entities
                    and not analyzer.is_host_constrained(goal_dst)
                ):
                    continue
            candidate_hosts.append((lid, lid))

        for cid, cent in graph.entities.items():
            if cid == held_id or not cent.capacity or cent.capacity < held_ent.size:
                continue
            # Do not reload right back into the exact container just unloaded from
            if origin_host == cid:
                continue
            c_loc = _resolve_resting_location(cid, eloc, graph.locations)
            if c_loc is not None:
                candidate_hosts.append((cid, c_loc))

        for dest_host, dest_loc in candidate_hosts:
            # Never immediately place back onto the exact ground location just picked from if nothing changed
            if origin_host == dest_host and dest_loc == world.agent_loc:
                continue

            leg_s = graph.travel_s[(world.agent_loc, dest_loc)]
            t_arrive = world.clock_s + leg_s

            v_cur = dict(vmap)
            e_cur = dict(eloc)
            u_cur = set(world.unknown_vars)
            ok_pas, h_cur, rem_pas = _advance_clock_and_fire_passive(
                v_cur, e_cur, held_id, u_cur, world.passive_events, t_arrive, graph
            )
            if not ok_pas:
                continue

            wait_s = 0.0
            can_place = _can_place_into_host(held_id, dest_host, v_cur, e_cur, h_cur, frozenset(u_cur), graph)
            if not can_place and rem_pas:
                for idx_ev in range(len(rem_pas)):
                    cand_t = rem_pas[idx_ev].ready_at_s
                    v_try, e_try, u_try = dict(v_cur), dict(e_cur), set(u_cur)
                    ok_w, h_try, rem_try = _advance_clock_and_fire_passive(
                        v_try, e_try, h_cur, u_try, rem_pas, cand_t, graph
                    )
                    if ok_w and _can_place_into_host(held_id, dest_host, v_try, e_try, h_try, frozenset(u_try), graph):
                        wait_s = max(0.0, cand_t - t_arrive)
                        v_cur, e_cur, u_cur, h_cur, rem_pas = v_try, e_try, u_try, h_try, rem_try
                        can_place = True
                        break
            if not can_place:
                continue

            t_start = t_arrive + wait_s
            t_end = t_start + unit_s
            ok_end, _, rem_pas = _advance_clock_and_fire_passive(
                v_cur, e_cur, held_id, u_cur, rem_pas, t_end, graph
            )
            if not ok_end:
                continue

            e_cur[held_id] = dest_host
            next_world = WorldState(
                agent_loc=dest_loc,
                hand=None,
                entity_loc=tuple(sorted(e_cur.items())),
                vars=tuple(sorted(v_cur.items())),
                unknown_vars=frozenset(u_cur),
                passive_events=rem_pas,
                executed_once=world.executed_once,
                clock_s=t_end,
                travel_s=world.travel_s + leg_s,
            )
            host_pp = (
                graph.locations[dest_host].place_preconditions
                if dest_host in graph.locations
                else graph.entities[dest_host].place_preconditions
            )
            reads_list = ['hand', f'occupancy:{dest_host}', *(p.var for p in host_pp)]
            writes_list = [f'entity:{held_id}.location', 'hand', f'occupancy:{dest_host}']
            step = PlanStep(
                kind='place',
                op_id=f'place:{held_id}->{dest_host}',
                from_location=world.agent_loc,
                location=dest_loc,
                travel_cost_s=leg_s,
                active_duration_s=unit_s,
                passive_duration_s=0.0,
                wait_duration_s=wait_s,
                start_time_s=t_start,
                end_time_s=t_end,
                entity_id=held_id,
                target_host=dest_host,
                reads=tuple(reads_list),
                writes=tuple(writes_list),
            )
            next_p_from = dict(p_from)
            next_p_from.pop(held_id, None)
            successors.append((step, next_world, next_p_from))

    # 3. Explicit wait for next pending passive event
    if world.passive_events:
        next_ready = world.passive_events[0].ready_at_s
        wait_s = max(0.0, next_ready - world.clock_s)
        v_cur = dict(vmap)
        e_cur = dict(eloc)
        u_cur = set(world.unknown_vars)
        fired_ops = [ev.op_id for ev in world.passive_events if ev.ready_at_s <= next_ready + 1e-9]
        fired_writes = [eff.var for ev in world.passive_events if ev.ready_at_s <= next_ready + 1e-9 for eff in ev.effects]
        ok_pas, h_cur, rem_pas = _advance_clock_and_fire_passive(
            v_cur, e_cur, world.hand, u_cur, world.passive_events, next_ready, graph
        )
        if ok_pas:
            next_world = WorldState(
                agent_loc=world.agent_loc,
                hand=h_cur,
                entity_loc=tuple(sorted(e_cur.items())),
                vars=tuple(sorted(v_cur.items())),
                unknown_vars=frozenset(u_cur),
                passive_events=rem_pas,
                executed_once=world.executed_once,
                clock_s=next_ready,
                travel_s=world.travel_s,
            )
            step = PlanStep(
                kind='wait_passive',
                op_id='wait:' + '+'.join(fired_ops),
                from_location=world.agent_loc,
                location=world.agent_loc,
                travel_cost_s=0.0,
                active_duration_s=0.0,
                passive_duration_s=0.0,
                wait_duration_s=wait_s,
                start_time_s=world.clock_s,
                end_time_s=next_ready,
                reads=(),
                writes=tuple(sorted(set(fired_writes))),
            )
            successors.append((step, next_world, p_from))

    return successors


def _pareto_dominated_or_insert(
    frontier_map: dict[tuple[Any, ...], list[tuple[float, tuple[float, ...], float]]],
    dkey: tuple[Any, ...],
    clock_s: float,
    ready_times: tuple[float, ...],
    travel_s: float,
) -> bool:
    """Return True if (clock_s, ready_times, travel_s) is dominated; otherwise insert & prune dominated entries."""
    existing = frontier_map.get(dkey)
    if existing is None:
        frontier_map[dkey] = [(clock_s, ready_times, travel_s)]
        return False
    for ex_clock, ex_ready, ex_travel in existing:
        if (
            ex_clock <= clock_s + EPS
            and all(er <= nr + EPS for er, nr in zip(ex_ready, ready_times))
            and (
                ex_clock + TRAVEL_TIEBREAK_WEIGHT * ex_travel
                <= clock_s + TRAVEL_TIEBREAK_WEIGHT * travel_s + 1e-7
            )
        ):
            return True
    kept = [
        (ex_clock, ex_ready, ex_travel)
        for ex_clock, ex_ready, ex_travel in existing
        if not (
            clock_s <= ex_clock + EPS
            and all(nr <= er + EPS for er, nr in zip(ex_ready, ready_times))
            and (
                clock_s + TRAVEL_TIEBREAK_WEIGHT * travel_s
                <= ex_clock + TRAVEL_TIEBREAK_WEIGHT * ex_travel + 1e-7
            )
        )
    ]
    kept.append((clock_s, ready_times, travel_s))
    frontier_map[dkey] = kept
    return False


def _solve_deterministic(
    start_world: WorldState,
    analyzer: _SearchAnalyzer,
    *,
    max_expansions: int = 25000,
) -> tuple[bool, float, list[PlanStep], WorldState, int, bool]:
    """Unified Pareto A* / Branch-and-Bound minimizing completion time + travel tie-breaker."""
    graph = analyzer.graph
    if _goals_satisfied(start_world, graph):
        return True, start_world.clock_s, [], start_world, 0, False

    serial = itertools.count()
    frontier: list[tuple[float, float, int, WorldState, list[PlanStep], dict[str, str]]] = []
    pareto: dict[tuple[Any, ...], list[tuple[float, tuple[float, ...], float]]] = {}

    h0 = analyzer.admissible_heuristic(start_world)
    dkey0 = analyzer.discrete_key(start_world)
    _pareto_dominated_or_insert(
        pareto, dkey0, start_world.clock_s, tuple(ev.ready_at_s for ev in start_world.passive_events), start_world.travel_s
    )
    f0 = start_world.clock_s + h0 + TRAVEL_TIEBREAK_WEIGHT * start_world.travel_s
    heapq.heappush(frontier, (f0, h0, next(serial), start_world, [], {}))

    best_goal: tuple[float, float, list[PlanStep], WorldState] | None = None
    best_partial: tuple[int, float, list[PlanStep], WorldState] = (
        _satisfied_goal_count(start_world, graph),
        -start_world.clock_s,
        [],
        start_world,
    )
    expanded = 0

    while frontier and expanded < max_expansions:
        f_val, _, _, world, steps, p_from = heapq.heappop(frontier)
        if best_goal is not None and f_val > best_goal[0] + 1e-7:
            break

        if _goals_satisfied(world, graph):
            obj = world.clock_s + TRAVEL_TIEBREAK_WEIGHT * world.travel_s
            if best_goal is None or obj < best_goal[0] - 1e-9:
                best_goal = (obj, world.clock_s, steps, world)
            continue

        sat_cnt = _satisfied_goal_count(world, graph)
        if (sat_cnt, -world.clock_s) > (best_partial[0], best_partial[1]):
            best_partial = (sat_cnt, -world.clock_s, steps, world)

        expanded += 1
        for step, nxt_world, nxt_p_from in _expand_world(world, analyzer, picked_from=p_from):
            dkey = analyzer.discrete_key(nxt_world)
            ready_tup = tuple(ev.ready_at_s for ev in nxt_world.passive_events)
            if _pareto_dominated_or_insert(pareto, dkey, nxt_world.clock_s, ready_tup, nxt_world.travel_s):
                continue
            h_val = analyzer.admissible_heuristic(nxt_world)
            f_nxt = nxt_world.clock_s + h_val + TRAVEL_TIEBREAK_WEIGHT * nxt_world.travel_s
            if best_goal is not None and f_nxt > best_goal[0] + 1e-7:
                continue
            heapq.heappush(frontier, (f_nxt, h_val, next(serial), nxt_world, [*steps, step], nxt_p_from))

    truncated = bool(frontier) and best_goal is None and expanded >= max_expansions
    if best_goal is not None:
        return True, best_goal[1], best_goal[2], best_goal[3], expanded, False

    # Open-domain unknown fallback: if open unknowns block goal completion, find minimum-cost path to resolve unknowns
    if start_world.unknown_vars:
        obs_plan = _plan_to_resolve_open_unknowns(start_world, analyzer, max_expansions=max(2000, max_expansions // 4))
        if obs_plan is not None:
            obs_steps, obs_end_world, obs_exp = obs_plan
            return False, obs_end_world.clock_s, obs_steps, obs_end_world, expanded + obs_exp, truncated

    return False, best_partial[3].clock_s, best_partial[2], best_partial[3], expanded, truncated


def _plan_to_resolve_open_unknowns(
    start_world: WorldState,
    analyzer: _SearchAnalyzer,
    *,
    max_expansions: int = 4000,
) -> tuple[list[PlanStep], WorldState, int] | None:
    """Find shortest valid prerequisite + observation sequence resolving at least one open unknown variable."""
    serial = itertools.count()
    frontier: list[tuple[float, int, WorldState, list[PlanStep], dict[str, str]]] = []
    visited: dict[tuple[Any, ...], float] = {analyzer.discrete_key(start_world): start_world.clock_s}
    heapq.heappush(frontier, (start_world.clock_s, next(serial), start_world, [], {}))
    expanded = 0
    while frontier and expanded < max_expansions:
        _, _, world, steps, p_from = heapq.heappop(frontier)
        if len(world.unknown_vars) < len(start_world.unknown_vars):
            return steps, world, expanded
        expanded += 1
        for step, nxt_world, nxt_p_from in _expand_world(world, analyzer, picked_from=p_from):
            dkey = analyzer.discrete_key(nxt_world)
            obj = nxt_world.clock_s + TRAVEL_TIEBREAK_WEIGHT * nxt_world.travel_s
            if obj >= visited.get(dkey, math.inf) - EPS:
                continue
            visited[dkey] = obj
            heapq.heappush(frontier, (obj, next(serial), nxt_world, [*steps, step], nxt_p_from))
    return None


def _satisfied_goal_count(world: WorldState, graph: CompiledTaskGraph) -> int:
    eloc = world.entity_map()
    vmap = world.vars_map()
    cnt = sum(1 for eid, dst in graph.goals.entity_locations if eloc.get(eid) == dst)
    cnt += sum(1 for p in graph.goals.predicates if _eval_predicate(p, vmap, eloc, world.hand, world.unknown_vars, graph))
    if graph.goals.require_empty_hand and world.hand is None:
        cnt += 1
    return cnt


def _solve_contingent_belief(
    initial_belief: list[tuple[float, WorldState, dict[str, Any]]],
    analyzer: _SearchAnalyzer,
    *,
    max_det_expansions: int = 12000,
    max_belief_depth: int = 10,
) -> dict[str, Any]:
    """Exact Bellman / AO* contingent search over finite-domain belief states.

    Evaluates:
        V*(B) = min_{a applicable in all s in B} [ step_cost(a) + sum_o P(o|B,a) V*(B_o) ]
    without any heuristic rule forcing observation before motion. Low-cost
    observations win strictly when their Value of Information exceeds their
    execution + travel cost.
    """
    graph = analyzer.graph
    det_cache: dict[tuple[Any, ...], tuple[bool, float, list[PlanStep], WorldState]] = {}
    belief_memo: dict[tuple[Any, ...], tuple[bool, float, float, list[PlanStep], list[dict[str, Any]]]] = {}
    total_expanded = 0

    def solve_singleton(w: WorldState) -> tuple[bool, float, list[PlanStep], WorldState]:
        nonlocal total_expanded
        # Shift w to relative clock 0 for cacheability, then add w.clock_s back
        rel_pas = tuple(
            PassiveEvent(ready_at_s=ev.ready_at_s - w.clock_s, op_id=ev.op_id, effects=ev.effects)
            for ev in w.passive_events
        )
        w_rel = WorldState(
            agent_loc=w.agent_loc,
            hand=w.hand,
            entity_loc=w.entity_loc,
            vars=w.vars,
            unknown_vars=w.unknown_vars,
            passive_events=rel_pas,
            executed_once=w.executed_once,
            clock_s=0.0,
            travel_s=0.0,
        )
        ckey = (analyzer.discrete_key(w_rel), tuple(round(ev.ready_at_s, 3) for ev in rel_pas))
        if ckey not in det_cache:
            ok, fin_s, steps, end_w, exp_n, _ = _solve_deterministic(
                w_rel, analyzer, max_expansions=max_det_expansions
            )
            total_expanded += exp_n
            det_cache[ckey] = (ok, fin_s, steps, end_w)
        ok, rel_fin_s, rel_steps, rel_end = det_cache[ckey]
        shifted_steps = [
            PlanStep(
                kind=st.kind,
                op_id=st.op_id,
                from_location=st.from_location,
                location=st.location,
                travel_cost_s=st.travel_cost_s,
                active_duration_s=st.active_duration_s,
                passive_duration_s=st.passive_duration_s,
                wait_duration_s=st.wait_duration_s,
                start_time_s=st.start_time_s + w.clock_s,
                end_time_s=st.end_time_s + w.clock_s,
                passive_ready_time_s=(st.passive_ready_time_s + w.clock_s) if st.passive_ready_time_s is not None else None,
                entity_id=st.entity_id,
                target_host=st.target_host,
                resolves_unknowns=st.resolves_unknowns,
                reads=st.reads,
                writes=st.writes,
            )
            for st in rel_steps
        ]
        shifted_end = WorldState(
            agent_loc=rel_end.agent_loc,
            hand=rel_end.hand,
            entity_loc=rel_end.entity_loc,
            vars=rel_end.vars,
            unknown_vars=rel_end.unknown_vars,
            passive_events=tuple(
                PassiveEvent(ready_at_s=ev.ready_at_s + w.clock_s, op_id=ev.op_id, effects=ev.effects)
                for ev in rel_end.passive_events
            ),
            executed_once=rel_end.executed_once,
            clock_s=w.clock_s + rel_fin_s,
            travel_s=w.travel_s + rel_end.travel_s,
        )
        return ok, rel_fin_s, shifted_steps, shifted_end

    def belief_key(belief: list[tuple[float, WorldState, dict[str, Any]]]) -> tuple[Any, ...]:
        base_t = belief[0][1].clock_s
        return tuple(
            (
                round(wt, 6),
                analyzer.discrete_key(w),
                tuple(round(ev.ready_at_s - base_t, 3) for ev in w.passive_events),
            )
            for wt, w, _ in belief
        )

    def search_belief(
        belief: list[tuple[float, WorldState, dict[str, Any]]],
        depth: int,
        visiting: frozenset[tuple[Any, ...]],
        bound_remaining_s: float,
    ) -> tuple[bool, float, float, list[PlanStep], list[dict[str, Any]]]:
        """Return (complete, expected_remaining_s, worst_remaining_s, prefix_steps, branches)."""
        if all(_goals_satisfied(w, graph) for _, w, _ in belief):
            return True, 0.0, 0.0, [], []

        if len(belief) == 1:
            _, w_only, _ = belief[0]
            ok, rem_s, steps, _ = solve_singleton(w_only)
            rem_obj = rem_s + TRAVEL_TIEBREAK_WEIGHT * sum(s.travel_cost_s for s in steps)
            return ok, rem_obj if ok else math.inf, rem_s if ok else math.inf, steps, []

        bkey = belief_key(belief)
        if bkey in visiting or depth >= max_belief_depth:
            return False, math.inf, math.inf, [], []
        if bkey in belief_memo:
            return belief_memo[bkey]

        # Lower bound across worlds in belief
        lb_exp = sum(wt * analyzer.admissible_heuristic(w) for wt, w, _ in belief)
        if lb_exp > bound_remaining_s + 1e-6:
            return False, math.inf, math.inf, [], []

        # Jointly applicable actions across all worlds in `belief`
        per_world_succs: list[dict[str, tuple[PlanStep, WorldState]]] = []
        for _, w, _ in belief:
            s_map = {st.op_id: (st, nw) for st, nw, _ in _expand_world(w, analyzer)}
            per_world_succs.append(s_map)

        common_ops = set(per_world_succs[0].keys())
        for s_map in per_world_succs[1:]:
            common_ops &= set(s_map.keys())

        best_res: tuple[bool, float, float, list[PlanStep], list[dict[str, Any]]] = (
            False, math.inf, math.inf, [], []
        )
        incumbent = bound_remaining_s

        # Order candidates: quick heuristic ordering (observing ops & low step cost first speeds up branch-and-bound pruning without altering optimality!)
        ordered_candidates = sorted(
            common_ops,
            key=lambda oid: (
                0 if per_world_succs[0][oid][0].resolves_unknowns else 1,
                per_world_succs[0][oid][0].end_time_s - belief[0][1].clock_s,
            ),
        )

        next_visiting = visiting | {bkey}
        for oid in ordered_candidates:
            rep_step = per_world_succs[0][oid][0]
            step_dt = rep_step.end_time_s - belief[0][1].clock_s
            step_obj = step_dt + TRAVEL_TIEBREAK_WEIGHT * rep_step.travel_cost_s
            if step_obj >= incumbent - 1e-7:
                continue

            transitioned: list[tuple[float, WorldState, dict[str, Any]]] = []
            valid_all = True
            for idx_w, (wt, w_prev, assign) in enumerate(belief):
                st_w, nw_w = per_world_succs[idx_w][oid]
                if abs((st_w.end_time_s - w_prev.clock_s) - step_dt) > 1e-5:
                    valid_all = False
                    break
                transitioned.append((wt, nw_w, assign))
            if not valid_all:
                continue

            if rep_step.resolves_unknowns:
                # Partition belief by observed values of resolves_unknowns
                partitions: dict[tuple[Any, ...], list[tuple[float, WorldState, dict[str, Any]]]] = defaultdict(list)
                obs_labels: dict[tuple[Any, ...], dict[str, Any]] = {}
                for wt, nw_w, assign in transitioned:
                    vmap_nw = nw_w.vars_map()
                    eloc_nw = nw_w.entity_map()
                    obs_key_items = []
                    obs_dict: dict[str, Any] = {}
                    for u_var in rep_step.resolves_unknowns:
                        eid_u = _extract_entity_loc_var(u_var)
                        val_u = eloc_nw.get(eid_u) if eid_u is not None else vmap_nw.get(u_var)
                        obs_key_items.append((u_var, _state_atom(val_u)))
                        obs_dict[u_var] = val_u
                    okey = tuple(obs_key_items)
                    partitions[okey].append((wt, nw_w, assign))
                    obs_labels[okey] = obs_dict

                exp_rem_after = 0.0
                worst_rem_after = 0.0
                branch_records: list[dict[str, Any]] = []
                all_ok = True
                for okey, sub_items in partitions.items():
                    p_branch = sum(x[0] for x in sub_items)
                    norm_sub = [(x[0] / p_branch, x[1], x[2]) for x in sub_items]
                    rem_allowance = (incumbent - step_obj - exp_rem_after) / max(p_branch, 1e-9)
                    b_ok, b_exp, b_worst, b_steps, b_subbranches = search_belief(
                        norm_sub, depth + 1, next_visiting, rem_allowance
                    )
                    if not b_ok:
                        all_ok = False
                        break
                    exp_rem_after += p_branch * b_exp
                    worst_rem_after = max(worst_rem_after, b_worst)
                    branch_records.append({
                        'observed': obs_labels[okey],
                        'probability': round(p_branch, 4),
                        'branch_remaining_time_s': round(b_exp, 2),
                        'ordered_steps': [s.to_dict(i + 1) for i, s in enumerate(b_steps)],
                        **({'contingent_branches': b_subbranches} if b_subbranches else {}),
                    })
                if all_ok:
                    tot_exp = step_obj + exp_rem_after
                    tot_worst = step_dt + worst_rem_after
                    if tot_exp < incumbent - 1e-7:
                        incumbent = tot_exp
                        best_res = (True, tot_exp, tot_worst, [rep_step], branch_records)
            else:
                b_ok, b_exp, b_worst, b_steps, b_subbranches = search_belief(
                    transitioned, depth + 1, next_visiting, incumbent - step_obj
                )
                if b_ok:
                    tot_exp = step_obj + b_exp
                    tot_worst = step_dt + b_worst
                    if tot_exp < incumbent - 1e-7:
                        incumbent = tot_exp
                        best_res = (True, tot_exp, tot_worst, [rep_step, *b_steps], b_subbranches)

        if bound_remaining_s == math.inf:
            belief_memo[bkey] = best_res
        return best_res

    ok, exp_obj, worst_s, prefix_steps, branches = search_belief(initial_belief, 0, frozenset(), math.inf)
    start_clock = initial_belief[0][1].clock_s
    exp_clean_s = round(exp_obj, 2) if math.isfinite(exp_obj) else None
    causal_preds = _compute_causal_predecessors(prefix_steps)
    step_dicts = [s.to_dict(i + 1, causal_preds[i]) for i, s in enumerate(prefix_steps)]

    return {
        'status': 'contingent_optimal' if ok else 'unsolvable_or_incomplete',
        'complete': ok,
        'starting_clock_s': round(start_clock, 2),
        'expected_finish_clock_s': round(start_clock + exp_obj, 2) if exp_clean_s is not None else None,
        'expected_remaining_s': exp_clean_s,
        'worst_case_remaining_s': round(worst_s, 2) if math.isfinite(worst_s) else None,
        'total_travel_cost_s': round(sum(s.travel_cost_s for s in prefix_steps), 2),
        'total_idle_wait_s': round(sum(s.wait_duration_s for s in prefix_steps), 2),
        'expanded_states': total_expanded,
        'ordered_steps': step_dicts,
        'next_step': step_dicts[0] if step_dicts else None,
        'contingent_branches': branches,
        'replan_after_observation': any(bool(s.resolves_unknowns) for s in prefix_steps),
        'assumptions': list(graph.assumptions),
    }


def _compute_causal_predecessors(steps: list[PlanStep]) -> list[list[int]]:
    """Build transitive-reduced partial-order causal DAG predecessors (1-based step_index)."""
    writers: dict[str, int] = {}
    readers: dict[str, set[int]] = defaultdict(set)
    ancestors: dict[int, set[int]] = {}
    out: list[list[int]] = []

    for i, step in enumerate(steps):
        idx = i + 1
        deps: set[int] = set()
        for r_var in step.reads:
            if r_var in writers:
                deps.add(writers[r_var])
        for w_var in step.writes:
            if w_var in writers:
                deps.add(writers[w_var])
            deps.update(readers.get(w_var, set()))
        deps.discard(idx)
        trans_anc = set().union(*(ancestors[d] for d in deps)) if deps else set()
        reduced = sorted(deps - trans_anc)
        ancestors[idx] = deps | trans_anc
        out.append(reduced)

        for w_var in step.writes:
            writers[w_var] = idx
            readers[w_var] = set()
        for r_var in step.reads:
            readers[r_var].add(idx)

    return out


def solve_task_graph(
    spec: dict[str, Any] | CompiledTaskGraph,
    *,
    measured_travel_s: dict[tuple[str, str], float] | None = None,
    initial_clock_s: float = 0.0,
    max_expansions: int = 25000,
) -> dict[str, Any]:
    """Solve a general precondition & cost-aware task graph."""
    graph = (
        spec
        if isinstance(spec, CompiledTaskGraph)
        else compile_task_graph(spec, measured_travel_s=measured_travel_s, initial_clock_s=initial_clock_s)
    )
    analyzer = _SearchAnalyzer(graph)
    belief = _initial_worlds(graph)

    if len(belief) > 1:
        return _solve_contingent_belief(belief, analyzer, max_det_expansions=max(4000, max_expansions // 2))

    start_world = belief[0][1]
    ok, finish_clock_s, steps, end_world, expanded, truncated = _solve_deterministic(
        start_world, analyzer, max_expansions=max_expansions
    )
    causal_preds = _compute_causal_predecessors(steps)
    step_dicts = [s.to_dict(i + 1, causal_preds[i]) for i, s in enumerate(steps)]
    replan_obs = any(bool(s.resolves_unknowns) for s in steps)

    status = (
        'optimal' if ok and not truncated
        else ('observation_checkpoint' if replan_obs else 'partial_or_blocked')
    )
    return {
        'status': status,
        'complete': ok,
        'search_truncated': truncated,
        'starting_clock_s': round(start_world.clock_s, 2),
        'estimated_finish_clock_s': round(finish_clock_s, 2),
        'estimated_remaining_s': round(max(0.0, finish_clock_s - start_world.clock_s), 2),
        'total_travel_cost_s': round(end_world.travel_s, 2),
        'total_idle_wait_s': round(sum(s.wait_duration_s for s in steps), 2),
        'expanded_states': expanded,
        'ordered_steps': step_dicts,
        'next_step': step_dicts[0] if step_dicts else None,
        'contingent_branches': [],
        'replan_after_observation': replan_obs,
        'assumptions': list(graph.assumptions),
    }


class TaskGraphSession:
    """Stateful incremental delta-merge planner session + public receipt travel/clock tracker.

    Zero simulator access and zero domain vocabulary. Automatically merges caller
    deltas (`locations`, `travel_cost`, `state_vars`, `entities`, `operations`,
    `goals`, `completed_operations`) and fuses publicly measured navigation costs
    from `RobotClock` receipts or Route A's external `state_memory`.
    """

    def __init__(self) -> None:
        self.reset_graph()
        self.robot_clock_s: float = 0.0
        self.any_process_running: bool = False
        self.measured_travel_s: dict[tuple[str, str], float] = {}
        self.visited_locations: list[str] = []
        self._last_nav_from: str | None = None
        self._external_memory_synced: dict[str, Any] = {}

    def reset_graph(self) -> None:
        self.agent_location: str | None = None
        self.hand_entity: str | None = None
        self.locations: dict[str, dict[str, Any]] = {}
        self.travel_edges: dict[tuple[str, str], dict[str, Any]] = {}
        self.state_vars: dict[str, dict[str, Any]] = {}
        self.entities: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.goals: dict[str, Any] = {
            'predicates': [],
            'entity_locations': [],
            'require_empty_hand': False,
        }
        self.completed_operations: list[str] = []
        self.last_solution: dict[str, Any] | None = None

    def observe_public_action(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        result: dict[str, Any],
    ) -> None:
        if not isinstance(result, dict):
            return
        prev_clock = self.robot_clock_s
        rt = result.get('robot_time')
        if isinstance(rt, dict) and isinstance(rt.get('total_s'), (int, float)):
            self.robot_clock_s = float(rt['total_s'])
        if isinstance(result.get('state_memory'), dict):
            self.sync_from_state_memory(result['state_memory'])
        if not result.get('ok') or tool_name != 'act':
            return
        effect = result.get('effect')
        if isinstance(effect, dict):
            proc = effect.get('process')
            if isinstance(proc, dict):
                self.any_process_running = bool(proc.get('any_process_running', False))
            if effect.get('primitive') == 'navigate_to':
                this_call = rt.get('this_call') if isinstance(rt, dict) and isinstance(rt.get('this_call'), dict) else {}
                delta_s = (
                    float(this_call['travel_s'])
                    if isinstance(this_call.get('travel_s'), (int, float)) and float(this_call['travel_s']) > 0
                    else (
                        float(rt['delta_s'])
                        if isinstance(rt, dict) and isinstance(rt.get('delta_s'), (int, float))
                        else max(0.0, self.robot_clock_s - prev_clock)
                    )
                )
                # If a planned step expected movement A -> B and navigation completed, record measured edge
                if (
                    effect.get('status') == 'completed'
                    and self.last_solution
                    and isinstance(self.last_solution.get('next_step'), dict)
                ):
                    nxt = self.last_solution['next_step']
                    src = nxt.get('from_location')
                    dst = nxt.get('location')
                    if isinstance(src, str) and isinstance(dst, str) and src != dst and delta_s > 0:
                        self.record_measured_travel(src, dst, delta_s)

    def record_measured_travel(self, from_loc: str, to_loc: str, seconds: float, *, overwrite: bool = False) -> None:
        if not from_loc or not to_loc or from_loc == to_loc or not math.isfinite(seconds) or seconds <= 0:
            return
        key = (from_loc.strip(), to_loc.strip())
        if key in self.measured_travel_s and not overwrite:
            self.measured_travel_s[key] = round(0.5 * self.measured_travel_s[key] + 0.5 * float(seconds), 2)
        else:
            self.measured_travel_s[key] = round(float(seconds), 2)

    def sync_from_state_memory(self, state_memory: Any) -> None:
        """Duck-typed zero-config ingestion from Route A's public `state_memory`."""
        if state_memory is None:
            return
        exported: dict[str, Any] = dict(state_memory) if isinstance(state_memory, dict) else {}
        if not exported:
            for method_name in ('for_planner', 'export_for_planner', 'to_dict', 'snapshot', 'as_dict'):
                fn = getattr(state_memory, method_name, None)
                if callable(fn):
                    try:
                        cand = fn()
                        if isinstance(cand, dict):
                            exported = cand
                            break
                    except Exception:
                        pass

        def _get_field(*names: str) -> Any:
            for nm in names:
                if nm in exported and exported[nm] is not None:
                    return exported[nm]
                if not isinstance(state_memory, dict):
                    val = getattr(state_memory, nm, None)
                    if val is not None and not callable(val):
                        return val
            return None

        clk = _get_field('robot_clock_s', 'episode_robot_time_s', 'elapsed_s', 'total_robot_time_s')
        if isinstance(clk, (int, float)) and float(clk) > self.robot_clock_s:
            self.robot_clock_s = float(clk)

        proc_state = _get_field('process_state')
        if isinstance(proc_state, dict) and 'any_running' in proc_state:
            self.any_process_running = bool(proc_state.get('any_running'))

        hint_aliases: dict[str, str] = {}
        hint_counts: dict[str, int] = defaultdict(int)
        vlocs = _get_field('visited_locations', 'locations')
        if isinstance(vlocs, (list, tuple, set)):
            for loc in vlocs:
                if isinstance(loc, str) and loc.strip():
                    clean = loc.strip()
                    if clean not in self.visited_locations:
                        self.visited_locations.append(clean)
        elif isinstance(vlocs, dict):
            for loc, info in vlocs.items():
                if isinstance(loc, str) and loc.strip():
                    clean = loc.strip()
                    if clean not in self.visited_locations:
                        self.visited_locations.append(clean)
                    if isinstance(info, dict) and isinstance(info.get('hint'), str) and info['hint'].strip():
                        h_str = info['hint'].strip()
                        hint_aliases[clean] = h_str
                        hint_counts[h_str] += 1
        unique_hints = {k: h for k, h in hint_aliases.items() if hint_counts[h] == 1}

        cur_loc = _get_field('current_location', 'agent_location')
        if isinstance(cur_loc, str) and cur_loc.strip() and self.agent_location is None:
            self.agent_location = cur_loc.strip()

        def _sync_edge(u_raw: str, v_raw: str, sec_val: float) -> int:
            self.record_measured_travel(u_raw, v_raw, sec_val, overwrite=True)
            cnt = 1
            hu = unique_hints.get(u_raw.strip())
            hv = unique_hints.get(v_raw.strip())
            if hu and hv and hu != hv:
                self.record_measured_travel(hu, hv, sec_val, overwrite=True)
                cnt += 1
            return cnt

        tmat = _get_field(
            'travel_edges', 'travel_cost', 'travel_matrix', 'travel_costs',
            'travel_cost_matrix', 'measured_travel_costs', 'travel_records',
            'travel_times', 'travel_seconds',
        )
        synced_edges = 0
        if isinstance(tmat, dict):
            for k, v in tmat.items():
                if isinstance(k, tuple) and len(k) == 2 and isinstance(v, (int, float)):
                    synced_edges += _sync_edge(str(k[0]), str(k[1]), float(v))
                elif isinstance(k, str) and ('->' in k or '|' in k) and isinstance(v, (int, float)):
                    sep = '->' if '->' in k else '|'
                    u_s, v_s = k.split(sep, 1)
                    synced_edges += _sync_edge(u_s, v_s, float(v))
                elif isinstance(k, str) and isinstance(v, dict):
                    if 'from_location' in v and 'to_location' in v:
                        sec_v = v.get('cost_s') if v.get('cost_s') is not None else v.get('seconds')
                        if isinstance(sec_v, (int, float)) and float(sec_v) > 0:
                            synced_edges += _sync_edge(str(v['from_location']), str(v['to_location']), float(sec_v))
                    else:
                        for dst_s, sec_v in v.items():
                            if isinstance(sec_v, (int, float)) and str(dst_s) != k and float(sec_v) > 0:
                                synced_edges += _sync_edge(k, str(dst_s), float(sec_v))
        elif isinstance(tmat, list):
            for row in tmat:
                if isinstance(row, dict):
                    u_s = row.get('from_location') or row.get('from')
                    v_s = row.get('to_location') or row.get('to')
                    sec_v = row.get('cost_s') if row.get('cost_s') is not None else row.get('seconds')
                    if isinstance(u_s, str) and isinstance(v_s, str) and isinstance(sec_v, (int, float)):
                        synced_edges += _sync_edge(u_s, v_s, float(sec_v))
                        if row.get('bidirectional', True):
                            synced_edges += _sync_edge(v_s, u_s, float(sec_v))

        vclosed = _get_field('verified_closed_containers')
        self._external_memory_synced = {
            'visited_locations_count': len(self.visited_locations),
            'measured_travel_edges_count': len(self.measured_travel_s),
            'newly_ingested_edges': synced_edges,
            'verified_closed_enclosures_count': len(vclosed) if isinstance(vclosed, dict) else 0,
        }

    def update_and_solve(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise SkillError('invalid_task_graph_input', 'Arguments must be an object.')
        if arguments.get('reset') is True:
            self.reset_graph()

        graph_delta = arguments.get('graph')
        if graph_delta is not None and not isinstance(graph_delta, dict):
            raise SkillError('invalid_task_graph_input', 'graph must be an object or null.')

        if isinstance(graph_delta, dict):
            self._merge_graph_delta(graph_delta)

        # Removals
        for rid in arguments.get('remove_operations') or []:
            cid = _clean_id(rid, 'remove_operations[]')
            self.operations.pop(cid, None)
        for eid in arguments.get('remove_entities') or []:
            cid = _clean_id(eid, 'remove_entities[]')
            self.entities.pop(cid, None)
            if self.hand_entity == cid:
                self.hand_entity = None

        # Mark started passive operations (if any) with current robot_clock_s
        for sid in arguments.get('started_passive_operations') or []:
            cid = _clean_id(sid, 'started_passive_operations[]')
            if cid in self.operations:
                self.operations[cid]['status'] = 'active_passive'
                if self.operations[cid].get('started_at_s') is None:
                    self.operations[cid]['started_at_s'] = self.robot_clock_s

        # Mark completed operations & apply their declared effects if not already overridden in this delta
        explicit_var_names = set()
        if isinstance(graph_delta, dict) and graph_delta.get('state_vars'):
            rv = graph_delta['state_vars']
            if isinstance(rv, dict):
                explicit_var_names = set(rv.keys())
            elif isinstance(rv, list):
                explicit_var_names = {x.get('name') for x in rv if isinstance(x, dict) and x.get('name')}

        for cid_raw in arguments.get('completed_operations') or []:
            cid = _clean_id(cid_raw, 'completed_operations[]')
            if cid not in self.completed_operations:
                self.completed_operations.append(cid)
            if cid in self.operations:
                op_row = self.operations[cid]
                was_done = op_row.get('status') == 'done'
                op_row['status'] = 'done'
                if not was_done:
                    self._apply_completed_op_effects_to_session(op_row, skip_vars=explicit_var_names)

        # Ensure any location referenced in measured_travel_s between already-declared locations is preserved
        merged_spec = self._materialize_spec()
        if not merged_spec['locations'] and not merged_spec['operations'] and not merged_spec['entities']:
            return {
                'public_status': self.public_status(),
                'plan': None,
            }

        compiled = compile_task_graph(
            merged_spec,
            measured_travel_s=self.measured_travel_s,
            initial_clock_s=self.robot_clock_s,
        )
        self.last_solution = solve_task_graph(compiled)
        return {
            'public_status': self.public_status(),
            'plan': self.last_solution,
        }

    def _merge_graph_delta(self, delta: dict[str, Any]) -> None:
        if delta.get('agent_location') is not None:
            loc = _clean_id(delta['agent_location'], 'graph.agent_location')
            self.agent_location = loc
            self.locations.setdefault(loc, {'id': loc})

        if 'hand_entity' in delta and delta['hand_entity'] is not None:
            he = _clean_id(delta['hand_entity'], 'graph.hand_entity')
            self.hand_entity = None if he == 'empty' else he

        for i, loc_entry in enumerate(delta.get('locations') or []):
            if isinstance(loc_entry, str):
                lid = _clean_id(loc_entry, f'locations[{i}]')
                self.locations.setdefault(lid, {'id': lid})
            elif isinstance(loc_entry, dict):
                lid = _clean_id(loc_entry.get('id'), f'locations[{i}].id')
                cur = dict(self.locations.get(lid, {'id': lid}))
                for k, v in loc_entry.items():
                    if v is not None or k not in cur:
                        cur[k] = copy.deepcopy(v)
                self.locations[lid] = cur

        raw_tc = delta.get('travel_cost')
        if isinstance(raw_tc, dict):
            for k, v in raw_tc.items():
                if isinstance(k, tuple) and len(k) == 2:
                    u, w = _clean_id(k[0], 'travel_cost'), _clean_id(k[1], 'travel_cost')
                    self.locations.setdefault(u, {'id': u})
                    self.locations.setdefault(w, {'id': w})
                    self.travel_edges[(u, w)] = {'from_location': u, 'to_location': w, 'cost_s': v, 'bidirectional': True}
        elif isinstance(raw_tc, list):
            for i, edge in enumerate(raw_tc):
                if not isinstance(edge, dict):
                    raise SkillError('invalid_task_graph_input', f'travel_cost[{i}] must be an object.')
                u = _clean_id(edge.get('from_location'), f'travel_cost[{i}].from_location')
                w = _clean_id(edge.get('to_location'), f'travel_cost[{i}].to_location')
                self.locations.setdefault(u, {'id': u})
                self.locations.setdefault(w, {'id': w})
                self.travel_edges[(u, w)] = copy.deepcopy(edge)

        raw_sv = delta.get('state_vars')
        if isinstance(raw_sv, dict):
            for k, v in raw_sv.items():
                vname = _clean_id(k, 'state_vars.key')
                if isinstance(v, dict):
                    self.state_vars[vname] = {'name': vname, **copy.deepcopy(v)}
                elif v == 'unknown':
                    self.state_vars[vname] = {'name': vname, 'unknown': True}
                elif isinstance(v, bool):
                    self.state_vars[vname] = {'name': vname, 'flag': v, 'unknown': False}
                elif isinstance(v, (int, float)):
                    self.state_vars[vname] = {'name': vname, 'value': v, 'unknown': False}
                else:
                    self.state_vars[vname] = {'name': vname, 'label': str(v), 'unknown': False}
        elif isinstance(raw_sv, list):
            for i, sv in enumerate(raw_sv):
                if not isinstance(sv, dict):
                    raise SkillError('invalid_task_graph_input', f'state_vars[{i}] must be an object.')
                vname = _clean_id(sv.get('name'), f'state_vars[{i}].name')
                self.state_vars[vname] = copy.deepcopy(sv)

        incoming_entity_ids = {
            ent.get('id').strip()
            for ent in (delta.get('entities') or [])
            if isinstance(ent, dict) and isinstance(ent.get('id'), str) and ent.get('id').strip()
        }
        for i, ent in enumerate(delta.get('entities') or []):
            if not isinstance(ent, dict):
                raise SkillError('invalid_task_graph_input', f'entities[{i}] must be an object.')
            eid = _clean_id(ent.get('id'), f'entities[{i}].id')
            cur_e = dict(self.entities.get(eid, {'id': eid}))
            for k, v in ent.items():
                if v is not None or k not in cur_e:
                    cur_e[k] = copy.deepcopy(v)
            loc_ref = cur_e.get('location')
            if (
                isinstance(loc_ref, str)
                and loc_ref not in {'hand', 'unknown'}
                and loc_ref not in self.entities
                and loc_ref not in incoming_entity_ids
            ):
                self.locations.setdefault(loc_ref, {'id': loc_ref})
            self.entities[eid] = cur_e

        for i, op in enumerate(delta.get('operations') or []):
            if not isinstance(op, dict):
                raise SkillError('invalid_task_graph_input', f'operations[{i}] must be an object.')
            oid = _clean_id(op.get('id'), f'operations[{i}].id')
            cur_op = dict(self.operations.get(oid, {'id': oid}))
            for k, v in op.items():
                if v is not None or k not in cur_op:
                    cur_op[k] = copy.deepcopy(v)
            if oid in self.completed_operations and op.get('status') is None:
                cur_op['status'] = 'done'
            oloc = cur_op.get('location')
            if isinstance(oloc, str) and oloc.strip():
                self.locations.setdefault(oloc.strip(), {'id': oloc.strip()})
            self.operations[oid] = cur_op

        raw_goals = delta.get('goals')
        if isinstance(raw_goals, dict):
            if 'predicates' in raw_goals or 'entity_locations' in raw_goals or 'require_empty_hand' in raw_goals:
                if raw_goals.get('predicates') is not None:
                    # Merge predicates by var
                    existing_p = {p['var']: p for p in self.goals.get('predicates', []) if isinstance(p, dict) and 'var' in p}
                    for p in _parse_predicate_list(raw_goals['predicates'], 'goals.predicates'):
                        existing_p[p.var] = p.to_dict()
                    self.goals['predicates'] = list(existing_p.values())
                if raw_goals.get('entity_locations') is not None:
                    existing_el = {
                        r['entity_id']: r
                        for r in self.goals.get('entity_locations', [])
                        if isinstance(r, dict) and 'entity_id' in r
                    }
                    raw_el = raw_goals['entity_locations']
                    if isinstance(raw_el, dict):
                        for eid, dst in raw_el.items():
                            existing_el[str(eid)] = {'entity_id': str(eid), 'location': str(dst)}
                            if str(dst) != 'hand' and str(dst) not in self.entities:
                                self.locations.setdefault(str(dst), {'id': str(dst)})
                    elif isinstance(raw_el, list):
                        for row in raw_el:
                            if isinstance(row, dict) and row.get('entity_id') and row.get('location'):
                                eid_s, dst_s = str(row['entity_id']), str(row['location'])
                                existing_el[eid_s] = {'entity_id': eid_s, 'location': dst_s}
                                if dst_s != 'hand' and dst_s not in self.entities:
                                    self.locations.setdefault(dst_s, {'id': dst_s})
                    self.goals['entity_locations'] = list(existing_el.values())
                if raw_goals.get('require_empty_hand') is not None:
                    self.goals['require_empty_hand'] = bool(raw_goals['require_empty_hand'])
            else:
                existing_p = {p['var']: p for p in self.goals.get('predicates', []) if isinstance(p, dict) and 'var' in p}
                for p in _parse_predicate_list(raw_goals, 'goals'):
                    existing_p[p.var] = p.to_dict()
                self.goals['predicates'] = list(existing_p.values())

    def _apply_completed_op_effects_to_session(self, op_row: dict[str, Any], *, skip_vars: set[str]) -> None:
        if isinstance(op_row.get('location'), str):
            self.agent_location = op_row['location']
        for eff_key in ('effects', 'passive_effects'):
            for eff in _parse_effect_list(op_row.get(eff_key), eff_key):
                if eff.var in skip_vars:
                    continue
                eid = _extract_entity_loc_var(eff.var)
                if eid is not None and eid in self.entities and eff.target is not None:
                    self.entities[eid]['location'] = str(eff.target)
                elif eff.var == 'hand':
                    self.hand_entity = None if eff.target in (None, 'empty', False) else str(eff.target)
                elif eff.op == 'set':
                    self.state_vars[eff.var] = {'name': eff.var, **eff.to_dict(), 'unknown': False}
                    self.state_vars[eff.var].pop('var', None)
                    self.state_vars[eff.var].pop('op', None)
                elif eff.var in self.state_vars and isinstance(self.state_vars[eff.var].get('value'), (int, float)):
                    cur_v = float(self.state_vars[eff.var]['value'])
                    tgt_v = float(eff.target)
                    if eff.op == 'add':
                        nv = cur_v + tgt_v
                    elif eff.op == 'max':
                        nv = max(cur_v, tgt_v)
                    else:
                        nv = min(cur_v, tgt_v)
                    self.state_vars[eff.var] = {
                        'name': eff.var,
                        'value': int(round(nv)) if abs(nv - round(nv)) <= 1e-9 else round(nv, 6),
                        'unknown': False,
                    }

    def _materialize_spec(self) -> dict[str, Any]:
        return {
            'agent_location': self.agent_location,
            'hand_entity': self.hand_entity,
            'locations': list(self.locations.values()),
            'travel_cost': list(self.travel_edges.values()),
            'state_vars': list(self.state_vars.values()),
            'entities': list(self.entities.values()),
            'operations': list(self.operations.values()),
            'goals': copy.deepcopy(self.goals),
        }

    def public_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            'episode_robot_time_s': round(self.robot_clock_s, 1),
            'any_process_reported_running': self.any_process_running,
            'agent_location': self.agent_location,
            'completed_operations': list(self.completed_operations),
            'measured_travel_edges': [
                {'from_location': u, 'to_location': v, 'cost_s': s}
                for (u, v), s in sorted(self.measured_travel_s.items())
            ],
        }
        if self._external_memory_synced:
            status['external_memory_synced'] = dict(self._external_memory_synced)
        if self.last_solution and self.last_solution.get('next_step') is not None:
            status['next_planned_step'] = self.last_solution['next_step']
        return status
