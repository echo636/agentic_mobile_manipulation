"""Compare ready transport groups using caller-supplied, observed task beliefs.

This CPU-only planner never reads simulator state or executes actions. It compares
direct delivery and carrier delivery for each ready source/destination group;
it does not claim a globally optimal schedule across unfinished task branches.
All costs are seconds, including travel estimates supplied by the caller.
"""
from collections import defaultdict
from dataclasses import dataclass
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


def transport_advice(content):
    """Optional note advice; incomplete beliefs never block notes or actions."""
    try:
        notes = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(notes, dict) or 'transport' not in notes:
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
        comparisons = compare_transport_batches(dependencies, completed, transfers, carriers,
            robot_location=plan['robot_location'], handling=HandlingCosts(**plan['handling_seconds']),
            travel_seconds={(edge['source'], edge['destination']): edge['seconds'] for edge in plan['travel']})
        return dict(status='estimated', source='model_authored_beliefs_and_costs',
            scope='ready transport groups; not a global optimum or physical guarantee',
            groups=[dict(goals=row['goals'], recommended=row['recommended'],
                estimated_saving_vs_direct=row['estimated_saving_vs_direct']) for row in comparisons])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return dict(status='needs_revision', reason=str(exc))
