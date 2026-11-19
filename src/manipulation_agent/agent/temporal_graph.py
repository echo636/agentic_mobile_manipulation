"""Public, model-authored temporal work graph and single-robot dispatch.

Durations and loads are planning estimates. A schedule can never attest an
observation or mark work complete. Time constraints are relative to the end of
a confirmed start operation, in seconds on the robot's clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from graphlib import TopologicalSorter
from itertools import combinations
from math import ceil, floor, isfinite
from time import monotonic


def word(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 800:
        raise ValueError("Expected a nonempty, bounded description")
    return value.strip()


def number(value, *, minimum=0):
    if (
        type(value) not in (float, int)
        or not isfinite(value)
        or not minimum <= value <= 86400
    ):
        raise ValueError("Expected finite seconds or units in the supported range")
    return ceil(value)


@dataclass(frozen=True)
class Work:
    id: str
    work: str
    origin: str
    destination: str
    seconds: int
    after: tuple[str, ...]
    assertion: str
    skill: str
    load: int

    @classmethod
    def read(cls, row):
        return cls(
            word(row["id"]),
            word(row["work"]),
            word(row["origin"]),
            word(row["destination"]),
            number(row["seconds"], minimum=1),
            tuple(word(k) for k in row["after"]),
            word(row["assertion"]),
            word(row["skill"]),
            number(row["load"]),
        )


@dataclass(frozen=True)
class Window:
    start: str
    check: str
    resource: str
    earliest: int
    latest: int
    margin: int
    earliest_source: str = "estimate"
    latest_source: str = "estimate"

    @classmethod
    def read(cls, r):
        w = cls(
            word(r["start"]),
            word(r["check"]),
            word(r["resource"]),
            number(r["earliest"]),
            number(r["latest"], minimum=1),
            number(r["margin"]),
            r.get("earliest_source", "estimate"),
            r.get("latest_source", "estimate"),
        )
        if {w.earliest_source, w.latest_source} - {"instruction", "estimate"}:
            raise ValueError("Unknown time-bound source")
        if w.start == w.check or w.latest - w.margin < w.earliest:
            raise ValueError("Inconsistent process window")
        return w


@dataclass(frozen=True)
class Carrier:
    name: str
    location: str
    capacity: int
    handling: int

    @classmethod
    def read(cls, r):
        return cls(
            word(r["name"]),
            word(r["location"]),
            number(r["capacity"], minimum=1),
            number(r["handling"]),
        )


def unique(rows, key):
    result = {}
    for item in rows:
        k = key(item)
        if k in result:
            raise ValueError("Duplicate key: " + str(k))
        result[k] = item
    return result


class InvalidOrderReferences(ValueError):
    def __init__(self, positions):
        self.positions = tuple(positions)
        super().__init__(
            "Explicit precedence endpoints must name existing node ids at entries "
            + ", ".join(map(str, positions))
        )


def correct_order_references(raw, positions, fixes):
    """Only repair invalid references. Every original relation keeps its known endpoints."""
    nodes = {n["id"] for n in raw["nodes"]}
    changes = unique(fixes, lambda r: r["entry"])
    if set(changes) != set(positions):
        raise ValueError("Each invalid precedence entry needs one correction")
    retained = {
        (r["first"], r["then"])
        for r in raw["required_order"]
        if r["first"] in nodes and r["then"] in nodes
    }
    orders = []
    for index, original in enumerate(raw["required_order"]):
        if index not in changes:
            orders.append(original)
            continue
        represented = False
        for pair in changes[index]["relations"]:
            if pair["first"] not in nodes or pair["then"] not in nodes:
                raise ValueError(
                    "Unknown endpoint in an explicit precedence correction"
                )
            if any(
                original[side] in nodes and pair[side] != original[side]
                for side in ("first", "then")
            ):
                # A compound phrase may repeat a different edge already kept in
                # the graph. It neither alters nor satisfies this obligation.
                if (pair["first"], pair["then"]) in retained:
                    continue
                raise ValueError(
                    "Correct only unknown endpoints of the original precedence"
                )
            orders.append(pair)
            represented = True
        if not represented:
            raise ValueError("An explicit precedence cannot be discarded")
    return {**raw, "required_order": orders}


@dataclass
class TemporalGraph:
    region: str
    nodes: dict[str, Work]
    windows: dict[str, Window]
    carriers: dict[str, Carrier]
    travel: dict[tuple[str, str], int]
    unknown_travel: int
    required_outcomes: frozenset[str]
    route_metres: dict[tuple[str, str], float]
    unknown_metres: float | None
    required_order: tuple[tuple[str, str], ...]

    @classmethod
    def read(cls, raw):
        graph = cls(
            word(raw["region"]),
            unique((Work.read(x) for x in raw["nodes"]), lambda n: n.id),
            unique((Window.read(x) for x in raw["windows"]), lambda w: w.check),
            unique((Carrier.read(x) for x in raw["carriers"]), lambda c: c.name),
            unique(
                (
                    (word(r["origin"]), word(r["destination"]), number(r["seconds"]))
                    for r in raw["travel"]
                ),
                lambda r: r[:2],
            ),
            number(raw["unknown_travel"], minimum=1),
            frozenset(word(k) for k in raw.get("required_outcomes", ())),
            {
                (word(t["origin"]), word(t["destination"])): t["metres"]
                for t in raw["travel"]
                if "metres" in t
            },
            raw.get("unknown_metres"),
            tuple(
                (word(pair["first"]), word(pair["then"]))
                for pair in raw.get("required_order", ())
            ),
        )
        graph.travel = {k: v[2] for k, v in graph.travel.items()}
        unresolved = [
            i
            for i, (a, b) in enumerate(graph.required_order)
            if a not in graph.nodes or b not in graph.nodes
        ]
        if unresolved:
            raise InvalidOrderReferences(unresolved)
        # Explicit order is itself a constraint. Do not require the compiler to
        # redundantly spell every edge in the successor's regular prerequisites.
        for predecessor, successor in graph.required_order:
            node = graph.nodes[successor]
            if predecessor not in node.after:
                graph.nodes[successor] = replace(node, after=(*node.after, predecessor))
        graph.validate()
        return graph

    def dependencies(self, key):
        deps = set(self.nodes[key].after)
        if key in self.windows:
            deps.add(self.windows[key].start)
        return deps

    def validate(self):
        if not self.nodes:
            raise ValueError("A task requires at least one observable outcome")
        for metres in [
            *self.route_metres.values(),
            *([self.unknown_metres] if self.unknown_metres is not None else []),
        ]:
            number(metres)
        if self.required_outcomes - self.nodes.keys():
            raise ValueError("Unknown required outcome")
        for w in self.windows.values():
            if w.check not in self.nodes or w.start not in self.nodes:
                raise ValueError("Unknown process endpoint")
        deps = {key: self.dependencies(key) for key in self.nodes}
        if any(d - self.nodes.keys() for d in deps.values()):
            raise ValueError("Unknown prerequisite")
        tuple(TopologicalSorter(deps).static_order())
        for a, b in self.required_order:
            if a == b or a not in self.nodes or b not in self.affected({a}):
                raise ValueError("Explicit user precedence was lost")

    def distance(self, a, b):
        return 0 if a == b else self.travel.get((a, b), self.unknown_travel)

    def route_decimetres(self, a, b):
        if a == b:
            return 0
        estimate = self.route_metres.get((a, b), self.unknown_metres)
        return round(10 * estimate) if estimate is not None else 0

    def affected(self, keys):
        result = set(keys)
        for key in TopologicalSorter(
            {k: self.dependencies(k) for k in self.nodes}
        ).static_order():
            if self.dependencies(key) & result:
                result.add(key)
        return result

    def repair(self, patch, affected, completed):
        """Apply an atomic edit. Preserve requirements, evidence and live timers."""
        scope = self.affected(affected) - completed.keys()
        updated = unique((Work.read(r) for r in patch["upsert"]), lambda n: n.id)
        removed = set(map(word, patch["remove"]))
        if (
            removed - scope
            or removed & updated.keys()
            or (updated.keys() & self.nodes.keys()) - scope
        ):
            raise ValueError("Repair outside the affected unfinished subgraph")
        goals = self.required_outcomes | (
            self.nodes.keys() - {k for n in self.nodes for k in self.dependencies(n)}
        )
        if removed & goals:
            raise ValueError("A terminal obligation cannot be deleted")
        for key in goals & updated.keys():
            if updated[key].assertion != self.nodes[key].assertion:
                raise ValueError("Preserve terminal assertions")
        windows = dict(self.windows)
        for w in map(Window.read, patch["windows"]):
            if w.check not in scope | (updated.keys() - self.nodes.keys()):
                raise ValueError("Unrelated window update")
            if (
                w.check in windows
                and windows[w.check].start in completed
                and (
                    w.start != windows[w.check].start
                    or w.resource != windows[w.check].resource
                )
            ):
                raise ValueError("A running process must keep its origin and resource")
            previous = windows.get(w.check)
            if previous:
                for bound in ("earliest", "latest"):
                    if getattr(previous, bound + "_source") == "instruction" and (
                        getattr(w, bound) != getattr(previous, bound)
                        or getattr(w, bound + "_source") != "instruction"
                        or w.start != previous.start
                    ):
                        raise ValueError("Explicit user time bounds must be preserved")
            windows[w.check] = w
        nodes = {k: v for k, v in self.nodes.items() if k not in removed}
        nodes.update(updated)
        graph = replace(self, nodes=nodes, windows=windows)
        graph.validate()
        for key in updated.keys() - self.nodes.keys():
            if not (graph.affected({key}) & (scope - removed)):
                raise ValueError(
                    "Inserted work must contribute to an affected obligation"
                )
        return graph


@dataclass(frozen=True)
class Job:
    members: tuple[str, ...]
    origin: str
    destination: str
    duration: int
    carrier: str = ""


@dataclass(frozen=True)
class Slot:
    job: Job
    depart: int
    start: int
    end: int


@dataclass
class Schedule:
    status: str
    slots: list[Slot]
    seconds: float


def candidates(graph, done, locations, grouping):
    nodes = [n for n in graph.nodes.values() if n.id not in done]
    jobs = [
        Job(
            (n.id,),
            n.origin,
            n.destination,
            n.seconds + graph.distance(n.origin, n.destination),
        )
        for n in nodes
    ]
    if not grouping:
        return jobs
    endpoints = set(graph.windows) | {w.start for w in graph.windows.values()}
    routes = {}
    for n in nodes:
        if n.load and n.id not in endpoints and n.origin != n.destination:
            routes.setdefault((n.origin, n.destination), []).append(n)
    downstream = {n.id: graph.affected({n.id}) - {n.id} for n in nodes}
    for (a, b), items in routes.items():
        for carrier in graph.carriers.values():
            if not locations.get(carrier.name, carrier.location):
                continue
            alternatives = []
            for size in range(2, min(carrier.capacity, len(items)) + 1):
                # For large groups restrict candidate generation to contiguous loads.
                groups = (
                    combinations(items, size)
                    if len(items) <= 8
                    else (items[i : i + size] for i in range(len(items) - size + 1))
                )
                for group in groups:
                    if sum(n.load for n in group) > carrier.capacity:
                        continue
                    keys = tuple(n.id for n in group)
                    if any(downstream[k] & set(keys) for k in keys):
                        continue
                    alternatives.append(
                        Job(
                            keys,
                            a,
                            b,
                            sum(n.seconds for n in group)
                            + carrier.handling
                            + graph.distance(a, b),
                            carrier.name,
                        )
                    )
            # Bound the optional circuit expansion, preserving all singleton alternatives.
            alternatives.sort(key=lambda j: -len(j.members))
            jobs.extend(alternatives[:16])
    return jobs


def feasible_incumbent(graph, jobs, completed, region, now, locations, serial):
    """Construct a full, timed route to retain if exact search exhausts its solve budget."""
    done = dict(completed)
    position = region
    clock = now
    carriers = {k: locations.get(k, c.location) for k, c in graph.carriers.items()}
    slots = []
    while len(done) < len(graph.nodes):
        active = [
            w for w in graph.windows.values() if w.start in done and w.check not in done
        ]
        origins = {}
        for w in active:
            if w.resource in origins and origins[w.resource] != w.start:
                return []
            origins[w.resource] = w.start
        checks = {w.check: w for w in active}
        choices = []
        for job in jobs:
            if any(
                k in done or not graph.dependencies(k) <= done.keys()
                for k in job.members
            ):
                continue
            if job.carrier and carriers.get(job.carrier) != job.origin:
                continue
            if serial and active and not all(k in checks for k in job.members):
                continue
            starting = [w for w in graph.windows.values() if w.start in job.members]
            if any(w.resource == other.resource for w in starting for other in active):
                continue
            trip = graph.distance(position, job.origin)
            arrival = clock + trip
            start = max(
                [arrival]
                + [
                    ceil(done[checks[k].start]) + checks[k].earliest
                    for k in job.members
                    if k in checks
                ]
            )
            end = start + job.duration

            for w in active:
                node = graph.nodes[w.check]
                return_time = (
                    0
                    if w.check in job.members
                    else graph.distance(job.destination, node.origin)
                    + node.seconds
                    + graph.distance(node.origin, node.destination)
                )
                if end + return_time > floor(done[w.start]) + w.latest - w.margin:
                    break
            else:
                # Prefer work that can start now and, among those, lower active time per item.
                # Tie-breaks are deterministic; the CP solver remains free to change the route.
                rank = (
                    start - arrival,
                    0 if starting or any(k in checks for k in job.members) else 1,
                    (trip + job.duration) / len(job.members),
                    job.members,
                )
                choices.append((rank, Slot(job, start - trip, start, end)))
        if not choices:
            return []
        _, slot = min(choices, key=lambda c: c[0])
        slots.append(slot)
        done.update(dict.fromkeys(slot.job.members, slot.end))
        clock, position = slot.end, slot.job.destination
        if slot.job.carrier:
            carriers[slot.job.carrier] = position
    return slots


def schedule(
    graph,
    completed,
    *,
    region,
    now,
    carrier_locations=None,
    grouping=True,
    serial=False,
    solver_seconds=1.0,
):
    """Return a feasible schedule or an explicit failure; never execute a partial proof."""
    from ortools.sat.python import cp_model

    t0 = monotonic()
    now = ceil(now)
    if not completed.keys() <= graph.nodes.keys():
        raise ValueError("Unknown completion")
    for k, t in completed.items():
        if (
            not isfinite(t)
            or t < 0
            or t > now
            or not graph.dependencies(k) <= completed.keys()
            or any(completed[dep] > t for dep in graph.dependencies(k))
        ):
            raise ValueError("Inconsistent completion history")
    locations = carrier_locations or {}
    jobs = candidates(graph, completed, locations, grouping and not serial)
    if not jobs:
        return Schedule("complete", [], monotonic() - t0)
    incumbent = feasible_incumbent(
        graph, jobs, completed, region, now, locations, serial
    )
    max_travel = max([graph.unknown_travel, *graph.travel.values()])
    horizon = (
        now
        + sum(j.duration + max_travel for j in jobs if len(j.members) == 1)
        + sum(w.latest for w in graph.windows.values())
        + 1
    )
    model = cp_model.CpModel()
    remaining = [k for k in graph.nodes if k not in completed]
    begin = {k: model.NewIntVar(now, horizon, "begin_" + k) for k in remaining}
    end = {k: model.NewIntVar(now, horizon, "end_" + k) for k in remaining}
    on = [model.NewBoolVar(f"use_{i}") for i in range(len(jobs))]
    starts = [begin[j.members[0]] for j in jobs]
    ends = [end[j.members[0]] for j in jobs]
    for k in remaining:
        model.AddExactlyOne(on[i] for i, j in enumerate(jobs) if k in j.members)
        for dep in graph.dependencies(k) - completed.keys():
            model.Add(begin[k] >= end[dep])
    for i, j in enumerate(jobs):
        model.Add(ends[i] == starts[i] + j.duration).OnlyEnforceIf(on[i])
        for k in j.members[1:]:
            model.Add(begin[k] == starts[i]).OnlyEnforceIf(on[i])
            model.Add(end[k] == ends[i]).OnlyEnforceIf(on[i])
    resource_uses = {}
    intervals = {}
    for w in graph.windows.values():
        if w.check in completed:
            continue
        anchor = completed.get(w.start)
        lower = (
            ceil(anchor) + w.earliest
            if anchor is not None
            else end[w.start] + w.earliest
        )
        upper = (
            floor(anchor) + w.latest - w.margin
            if anchor is not None
            else end[w.start] + w.latest - w.margin
        )
        model.Add(begin[w.check] >= lower)
        model.Add(end[w.check] <= upper)
        # Operation at the machine and subsequent background phase share one device.
        resource_uses.setdefault((w.resource, w.start), []).append(end[w.check])
        if serial:
            for k in remaining:
                if k not in (w.start, w.check) and not (
                    k in graph.windows and graph.windows[k].start == w.start
                ):
                    before = model.NewBoolVar("serial_before")
                    if w.start in completed:
                        model.Add(begin[k] >= end[w.check])
                    else:
                        model.Add(end[k] <= begin[w.start]).OnlyEnforceIf(before)
                        model.Add(begin[k] >= end[w.check]).OnlyEnforceIf(before.Not())
    for (resource, start_key), checks in resource_uses.items():
        left = (
            floor(completed[start_key]) if start_key in completed else begin[start_key]
        )
        right = model.NewIntVar(now, horizon, "resource_release")
        model.AddMaxEquality(right, checks)
        size = model.NewIntVar(0, horizon, "resource_duration")
        intervals.setdefault(resource, []).append(
            model.NewIntervalVar(left, size, right, "resource_use")
        )
    for group in intervals.values():
        model.AddNoOverlap(group)
    # One mobile manipulator. Transit depends on both jobs in the selected sequence.
    arcs = []
    route_terms = [
        graph.route_decimetres(j.origin, j.destination) * on[i]
        for i, j in enumerate(jobs)
    ]
    for i, j in enumerate(jobs):
        ix = i + 1
        arcs.append((ix, ix, on[i].Not()))
        first = model.NewBoolVar(f"first_{ix}")
        last = model.NewBoolVar(f"last_{ix}")
        arcs.extend(((0, ix, first), (ix, 0, last)))
        route_terms.append(graph.route_decimetres(region, j.origin) * first)
        model.Add(starts[i] >= now + graph.distance(region, j.origin)).OnlyEnforceIf(
            first
        )
        for m, other in enumerate(jobs):
            if i == m or set(j.members) & set(other.members):
                continue
            edge = model.NewBoolVar(f"path_{ix}_{m + 1}")
            arcs.append((ix, m + 1, edge))
            route_terms.append(
                graph.route_decimetres(j.destination, other.origin) * edge
            )
            model.Add(
                starts[m] >= ends[i] + graph.distance(j.destination, other.origin)
            ).OnlyEnforceIf(edge)
    model.AddCircuit(arcs)
    # A carrier remains where its previous confirmed trip ends.
    for name, c in graph.carriers.items():
        indices = [i for i, j in enumerate(jobs) if j.carrier == name]
        if not indices:
            continue
        unused = model.NewBoolVar("unused_" + name)
        model.Add(sum(on[i] for i in indices) == 0).OnlyEnforceIf(unused)
        model.Add(sum(on[i] for i in indices) >= 1).OnlyEnforceIf(unused.Not())
        edges = [(0, 0, unused)]
        for index, i in enumerate(indices, 1):
            j = jobs[i]
            edges.append((index, index, on[i].Not()))
            if j.origin == locations.get(name, c.location):
                edges.append((0, index, model.NewBoolVar("carrier_first")))
            edges.append((index, 0, model.NewBoolVar("carrier_last")))
            for next_index, m in enumerate(indices, 1):
                if i != m and j.destination == jobs[m].origin:
                    edge = model.NewBoolVar("carrier_route")
                    edges.append((index, next_index, edge))
                    model.Add(starts[m] >= ends[i]).OnlyEnforceIf(edge)
        model.AddCircuit(edges)
    makespan = model.NewIntVar(now, horizon, "makespan")
    model.AddMaxEquality(makespan, list(end.values()))
    if graph.unknown_metres is not None:
        # Each selected job contributes at most an approach and a loaded leg.
        largest_leg = ceil(
            10 * max([graph.unknown_metres, *graph.route_metres.values()])
        )
        distance_bound = 2 * len(remaining) * largest_leg
        model.Minimize((distance_bound + 1) * (makespan - now) + sum(route_terms))
    else:
        # Compatible with older graphs that supplied transit seconds alone.
        model.Minimize(
            (len(remaining) * (horizon - now) + 1) * (makespan - now)
            + sum(begin.values())
        )
    if incumbent:
        chosen = {slot.job: slot for slot in incumbent}
        for i, job in enumerate(jobs):
            model.AddHint(on[i], int(job in chosen))
        for slot in incumbent:
            for key in slot.job.members:
                model.AddHint(begin[key], slot.start)
                model.AddHint(end[key], slot.end)
        model.Add(makespan <= incumbent[-1].end)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = solver_seconds
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status == cp_model.UNKNOWN and incumbent:
        return Schedule("heuristic_feasible", incumbent, monotonic() - t0)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return Schedule(solver.StatusName(status).lower(), [], monotonic() - t0)
    slots = []
    prev = region
    for i in sorted(
        (i for i in range(len(jobs)) if solver.Value(on[i])),
        key=lambda i: solver.Value(starts[i]),
    ):
        job = jobs[i]
        start = solver.Value(starts[i])
        slots.append(
            Slot(
                job,
                start - graph.distance(prev, job.origin),
                start,
                solver.Value(ends[i]),
            )
        )
        prev = job.destination
    return Schedule(solver.StatusName(status).lower(), slots, monotonic() - t0)
