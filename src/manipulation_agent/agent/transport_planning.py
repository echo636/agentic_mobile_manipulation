"""Plan preparation and transport using caller-supplied, observed task beliefs.

This CPU-only planner never reads simulator state or executes actions. It compares
direct and carrier delivery, including preparing more items before departure.
Bounded search is advisory, not a claim of a globally optimal physical plan.
All costs are seconds, including travel estimates supplied by the caller.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
import json
import math


@dataclass(frozen=True)
class Transfer:
    goal_id: str
    source: str
    destination: str
    # One identifiable item, with space consumption estimated from observations.
    units: float = 1.
    compatibility: str | None = None


@dataclass(frozen=True)
class Carrier:
    label: str
    location: str
    capacity: float
    compatible_groups: frozenset[str]
    return_required: bool = False
    recovery_seconds_per_trip: float = 0.


@dataclass(frozen=True)
class HandlingCosts:
    direct_item: float
    load_item: float
    unload_item: float
    carrier_trip: float
    carrier_return: float


@dataclass(frozen=True)
class Preparation:
    goal_id: str
    location: str
    seconds: float


def ready_goals(dependencies, completed):
    """Only completed prerequisites unlock nodes; siblings remain unordered."""
    missing = {p for parents in dependencies.values() for p in parents} - dependencies.keys()
    if missing:
        raise ValueError(f'Unknown prerequisite goals: {sorted(missing)}')
    remaining = {key: set(parents) for key, parents in dependencies.items()}
    while remaining:
        roots = {key for key, parents in remaining.items() if not parents}
        if not roots:
            raise ValueError('Task dependencies contain a cycle')
        remaining = {key: parents-roots for key, parents in remaining.items() if key not in roots}
    return {key for key, parents in dependencies.items()
            if key not in completed and set(parents) <= completed}


def compare_transport_batches(dependencies, completed, transfers, carriers, *,
                              robot_location, travel_seconds, handling):
    """Return cost-ranked alternatives for each compatible, ready transport group.

    Missing travel estimates exclude an option; they are not zero-cost edges.
    Capacity packing uses first-fit decreasing, a feasible heuristic rather than
    an optimal packing claim. Carrier estimates include collection, every return
    trip, loading/unloading, and requested carrier return. Both methods finish
    with the robot at the delivery destination, making their costs comparable.
    """
    transfers, carriers = list(transfers), list(carriers)
    if len({t.goal_id for t in transfers}) != len(transfers):
        raise ValueError('Each transfer goal must occur once')
    if any(t.goal_id not in dependencies for t in transfers):
        raise ValueError('Every transfer must reference a DAG goal')
    costs = [*vars(handling).values(), *travel_seconds.values(),
             *(c.recovery_seconds_per_trip for c in carriers)]
    if any(not math.isfinite(v) or v < 0 for v in costs):
        raise ValueError('Cost estimates must be finite and nonnegative')
    if any(not math.isfinite(v) or v <= 0 for v in
           [*(t.units for t in transfers), *(c.capacity for c in carriers)]):
        raise ValueError('Item sizes and carrier capacities must be positive')
    ready = ready_goals(dependencies, set(completed))
    groups = defaultdict(list)
    for item in transfers:
        if item.goal_id in ready:
            # Unknown compatibility must not silently mix unrelated items.
            group = ('known', item.compatibility) if item.compatibility else ('unknown', item.goal_id)
            groups[item.source, item.destination, group].append(item)

    def option(method, items, batches, route, handling_seconds, carrier=None):
        route = [place for i, place in enumerate(route) if i == 0 or place != route[i-1]]
        edges = list(zip(route, route[1:]))
        if any(edge not in travel_seconds for edge in edges):
            return None
        travel = sum(travel_seconds[edge] for edge in edges)
        recovery = len(batches)*carrier.recovery_seconds_per_trip if carrier else 0.
        return dict(method=method, carrier=carrier.label if carrier else None,
                    goals=[t.goal_id for t in items], batches=batches, route=route,
                    travel_seconds=travel, handling_seconds=handling_seconds,
                    expected_recovery_seconds=recovery,
                    estimated_seconds=travel+handling_seconds+recovery)

    comparisons = []
    for (source, destination, _), items in groups.items():
        count = len(items)
        direct = option('direct', items, [[t.goal_id] for t in items],
                        [robot_location, *[p for _ in items for p in (source, destination)]],
                        count*handling.direct_item)
        alternatives = [direct] if direct else []
        for carrier in carriers:
            if (not items[0].compatibility or items[0].compatibility not in carrier.compatible_groups
                    or any(t.units > carrier.capacity for t in items)):
                continue
            loads = []
            for item in sorted(items, key=lambda t: -t.units):
                load = next((load for load in loads if sum(t.units for t in load)+item.units
                             <= carrier.capacity), None)
                if load is None:
                    load = []
                    loads.append(load)
                load.append(item)
            route = [robot_location, carrier.location,
                     *[p for _ in loads for p in (source, destination)]]
            cost = count*(handling.load_item+handling.unload_item)+len(loads)*handling.carrier_trip
            if carrier.return_required and carrier.location != destination:
                route.extend((carrier.location, destination))
                cost += handling.carrier_return
            carried = option('carrier', items, [[t.goal_id for t in load] for load in loads],
                             route, cost, carrier)
            if carried:
                alternatives.append(carried)
        alternatives.sort(key=lambda candidate: candidate['estimated_seconds'])
        comparisons.append(dict(source=source, destination=destination,
            goals=[t.goal_id for t in items], alternatives=alternatives,
            recommended=alternatives[0] if alternatives else None,
            estimated_saving_vs_direct=(direct['estimated_seconds']-alternatives[0]['estimated_seconds']
                if direct and alternatives else None)))
    return comparisons


def schedule_transport(dependencies, completed, transfers, carriers, preparations, *,
                       robot_location, travel_seconds, handling, beam_width=32):
    """Compare complete plans for the supplied horizon, not just the next trip.

    Each operation ends with a free hand. Preparations include local handling;
    deliveries include loading/unloading. Carrier locations follow each trip.
    Keep the cheapest states per completed-goal count, merging equivalent states.
    Packing remains heuristic; unmodelled prerequisites are reported, never waived.
    """
    completed = frozenset(completed)
    transfers = [t for t in transfers if t.goal_id not in completed]
    preps = {p.goal_id: p for p in preparations if p.goal_id not in completed}
    if len({p.goal_id for p in preparations}) != len(preparations):
        raise ValueError('Each preparation goal must occur once')
    if any(p.goal_id not in dependencies or not math.isfinite(p.seconds) or p.seconds < 0
           for p in preparations):
        raise ValueError('Preparations need known goals and finite nonnegative seconds')
    if set(preps) & {t.goal_id for t in transfers}:
        raise ValueError('Preparation and delivery must use distinct goal IDs')
    if len({c.label for c in carriers}) != len(carriers):
        raise ValueError('Carrier labels must be unique')
    # Also validate the inputs when every delivery is initially waiting.
    compare_transport_batches(dependencies, completed, transfers, carriers,
        robot_location=robot_location, travel_seconds=travel_seconds, handling=handling)
    required = {t.goal_id for t in transfers}
    pending = list(required)
    while pending:
        for parent in dependencies[pending.pop()]:
            if parent not in completed and parent not in required:
                required.add(parent)
                pending.append(parent)
    missing = required - preps.keys() - {t.goal_id for t in transfers}
    if missing:
        return dict(status='needs_revision', missing_preparations=sorted(missing),
                    reason='Describe unfinished prerequisites with location and seconds.')
    preps = {key: value for key, value in preps.items() if key in required}
    required = frozenset(required)
    # A state includes carrier locations: a used tray is no longer at its origin.
    buckets = [dict() for _ in range(len(required)+1)]
    start = (frozenset(), robot_location, tuple(c.location for c in carriers))
    buckets[0][start] = (0., [])
    expanded = pruned = 0
    for level, bucket in enumerate(buckets):
        ranked = sorted(bucket.items(), key=lambda row: row[1][0])
        pruned += max(0, len(ranked)-beam_width)
        if level == len(required):
            if ranked:
                (_, location, _), (cost, steps) = ranked[0]
                return dict(status='estimated', estimated_seconds=cost, steps=steps,
                    final_location=location, expanded_states=expanded, pruned_states=pruned,
                    search='beam_over_completed_goals', beam_width=beam_width,
                    scope='supplied preparation/delivery horizon; heuristic, not global optimum')
            break
        for (done, location, positions), (cost, steps) in ranked[:beam_width]:
            expanded += 1
            ready = ready_goals(dependencies, completed | done)
            options = []
            for goal_id, prep in preps.items():
                if goal_id not in ready:
                    continue
                travel = 0. if location == prep.location else travel_seconds.get((location, prep.location))
                if travel is not None:
                    options.append((dict(method='prepare', goals=[goal_id],
                        route=[location, prep.location] if location != prep.location else [prep.location],
                        travel_seconds=travel, handling_seconds=prep.seconds,
                        estimated_seconds=travel+prep.seconds), prep.location, positions))
            current_carriers = [replace(c, location=pos) for c, pos in zip(carriers, positions)]
            groups = compare_transport_batches(dependencies, completed | done,
                [t for t in transfers if t.goal_id not in done], current_carriers,
                robot_location=location, travel_seconds=travel_seconds, handling=handling)
            for group in groups:
                for option in group['alternatives']:
                    after = list(positions)
                    if option['carrier'] is not None:
                        index = next(i for i, c in enumerate(carriers) if c.label == option['carrier'])
                        if not carriers[index].return_required:
                            after[index] = group['destination']
                    options.append((option, group['destination'], tuple(after)))
            for option, destination, after in options:
                reached = done | frozenset(option['goals'])
                total = cost + option['estimated_seconds']
                key = (reached, destination, after)
                target = buckets[len(reached)]
                if key not in target or total < target[key][0]:
                    operation = dict(option, depends_on_goals=sorted({p
                        for goal in option['goals'] for p in dependencies[goal]}))
                    target[key] = (total, [*steps, operation])
    return dict(status='needs_revision', reason='No complete plan within supplied routes and search beam.',
                expanded_states=expanded, pruned_states=pruned)


def transport_advice(content):
    """Optional note advice; incomplete beliefs never block notes or actions."""
    try:
        notes = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(notes, dict) or notes.get('transport') is None or 'planning' in notes:
        return None
    try:
        plan = notes['transport']
        goals = {goal['id']: goal for goal in notes['goals']}
        dependencies = {}
        for key, goal in goals.items():
            parents, visited, current = set(), set(), key
            while current is not None:
                if current in visited:
                    raise ValueError('Goal parentage contains a cycle')
                visited.add(current)
                parents.update(goals[current].get('depends_on', []))
                current = goals[current].get('parent_id')
            dependencies[key] = parents
        completed = {key for key, goal in goals.items() if goal['status'] == 'done'}
        transfers = [Transfer(**item) for item in plan['items']
                     if goals[item['goal_id']]['status'] in ('pending', 'active')]
        carriers = [Carrier(**{**item, 'compatible_groups': frozenset(item['compatible_groups'])})
                    for item in plan['carriers']]
        kwargs = dict(robot_location=plan['robot_location'],
            handling=HandlingCosts(**plan['handling_seconds']),
            travel_seconds={(edge['source'], edge['destination']): edge['seconds'] for edge in plan['travel']})
        comparisons = compare_transport_batches(dependencies, completed, transfers, carriers, **kwargs)
        schedule = schedule_transport(dependencies, completed, transfers, carriers,
            [Preparation(**item) for item in plan.get('preparations', [])], **kwargs)
        return dict(status=schedule['status'], source='model_authored_beliefs_and_costs',
            scope='modelled preparation and transport; not a physical guarantee',
            plan=schedule,
            groups=[dict(goals=row['goals'], recommended=row['recommended'],
                estimated_saving_vs_direct=row['estimated_saving_vs_direct']) for row in comparisons])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return dict(status='needs_revision', reason=str(exc))
