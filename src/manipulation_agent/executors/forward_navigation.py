"""CPU-only forward driving: certify a polyline, or search XY + heading.

The collision adapter owns geometry. Search never moves the robot. A route is
made of checked in-place turns and forward translations, including the final
orientation required by the visual approach planner.
"""
from dataclasses import dataclass, replace
import heapq
import math

from .gt_navigation import NavigationError, NavigationPlan


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


@dataclass(frozen=True)
class ForwardRoute:
    phases: tuple[NavigationPlan, ...]
    distance_m: float
    expanded_states: int
    method: str


def plan_forward_route(plan, actual, collision, *, check_cancelled=lambda: None,
                       max_expansions=20000):
    """Preserve the selected goal and final yaw; never fall back to strafing.

First certify the existing pruned path. If its corners cannot be turned, an
eight-heading A* searches for turning space. It can traverse a narrow corridor
without requiring enough clearance to rotate at every point in that corridor.
    """
    grid = collision.grid
    actual = tuple(map(float, actual))
    goal_pose = (*plan.goal, plan.final_yaw)
    if not collision.motion_free(actual, actual):
        raise NavigationError('Forward driving starts without collision clearance',
                              'navigation_invalid_start')
    if not collision.motion_free(goal_pose, goal_pose):
        raise NavigationError('Selected endpoint lacks clearance at its required final heading')

    def connector(start, xy, final_yaw):
        """Return checked turn/forward/turn poses, or None."""
        check_cancelled()
        poses = [start]
        if math.dist(start[:2], xy) > 1e-8:
            heading = math.atan2(xy[1]-start[1], xy[0]-start[0])
            poses += [(*start[:2], heading), (*xy, heading)]
        poses.append((*xy, final_yaw))
        for a, b in zip(poses, poses[1:]):
            check_cancelled()
            if not collision.motion_free(a, b):
                return None
        return poses

    def finish(poses, expanded, method):
        phases = []
        distance = 0.
        for a, b in zip(poses, poses[1:]):
            length = math.dist(a[:2], b[:2])
            if length < 1e-8 and abs(angle(b[2]-a[2])) < 1e-8:
                continue
            # Keep distinct turns: a safe 270-degree turn must not be changed
            # to the obstructed shortest 90-degree turn during reconstruction.
            points = (a[:2], b[:2]) if length > 1e-8 else (b[:2],)
            phase = NavigationPlan(points, b[:2], plan.target, b[2], length,
                                   plan.score, plan.candidates_considered,
                                   plan.candidates_reachable, 0., expanded, b[2])
            # Merge collinear forward cells, never merge across a turn.
            if (length > 1e-8 and phases and phases[-1].geodesic_m > 0
                    and abs(angle(phases[-1].final_yaw-b[2])) < 1e-8):
                previous = phases.pop()
                phase = replace(phase, points=(previous.points[0], b[:2]),
                                geodesic_m=previous.geodesic_m+length)
            phases.append(phase)
            distance += length
        if not phases:
            phases.append(replace(plan, points=(plan.goal,), travel_yaw=plan.final_yaw))
        return ForwardRoute(tuple(phases), distance, expanded, method)

    # Replace the stale starting sample with the measured position.
    poses = [actual]
    for xy in (*plan.points[1:], plan.goal):
        if math.dist(poses[-1][:2], xy) < 1e-8:
            continue
        yaw = math.atan2(xy[1]-poses[-1][1], xy[0]-poses[-1][0])
        segment = connector(poses[-1], xy, yaw)
        if segment is None:
            poses = None
            break
        poses.extend(segment[1:])
    if poses is not None:
        final = connector(poses[-1], plan.goal, plan.final_yaw)
        if final is not None:
            return finish(poses+final[1:], 0, 'certified_polyline')

    # A* actions: rotate +/-45 degrees in place, or drive forward one cell.
    directions = ((0, 1), (1, 1), (1, 0), (1, -1),
                  (0, -1), (-1, -1), (-1, 0), (-1, 1))
    def pose(state):
        row, col, heading = state
        return (*grid.world((row, col)), heading*math.pi/4)
    def edge_cost(a, b):
        # Metres plus turn time at the same speed limits as the follower.
        return math.dist(a[:2], b[:2]) + .5*abs(angle(b[2]-a[2]))/(math.pi/3)
    # Euclidean distance expands huge regions on the wrong side of walls.
    # Lazily solve a 2D relaxation of the heading graph instead. It ignores
    # turn feasibility, so a narrow corridor is not discarded for lacking
    # room to turn at every cell.
    relaxed = collision.optimistic_grid() if hasattr(collision,'optimistic_grid') else None
    reverse_queue = []
    reverse_costs = {}
    reverse_closed = set()
    def heuristic(state):
        if relaxed is None:
            return math.dist(pose(state)[:2], plan.goal)
        cell = state[:2]
        while reverse_queue and cell not in reverse_closed:
            distance, current = heapq.heappop(reverse_queue)
            if current in reverse_closed:
                continue
            if len(reverse_closed)%256 == 0:
                check_cancelled()
            reverse_closed.add(current)
            for neighbor, cost in relaxed.neighbors(current):
                updated = distance+cost
                if updated < reverse_costs.get(neighbor,math.inf):
                    reverse_costs[neighbor] = updated
                    heapq.heappush(reverse_queue,(updated,neighbor))
        return reverse_costs.get(cell,math.inf)

    queue, costs, parents, seeds = [], {}, {}, {}
    # The measured heading is not generally a multiple of 45 degrees. In a
    # narrow aisle the robot may have to drive along that exact heading before
    # it can turn onto the lattice; a nearest-cell snap would miss that route.
    departures=[actual]
    for distance in (.1,.2,.4,.6,.8,1.2):
        check_cancelled()
        end=(actual[0]+distance*math.cos(actual[2]),actual[1]+distance*math.sin(actual[2]),actual[2])
        if not collision.motion_free(actual,end):
            break
        departures.append(end)
    # Likewise, rotate before the final narrow approach, then drive in along
    # its required viewing heading. Never assume the endpoint has turning room.
    arrivals=[goal_pose]
    for distance in (.1,.2,.4,.6,.8,1.2):
        check_cancelled()
        entry=(goal_pose[0]-distance*math.cos(goal_pose[2]),
               goal_pose[1]-distance*math.sin(goal_pose[2]),goal_pose[2])
        if not collision.motion_free(entry,goal_pose):
            break
        arrivals.append(entry)
    if relaxed is not None:
        # The final continuous-heading aisle need not admit any lattice
        # heading at its endpoint. Seed the relaxation at its entry cells,
        # rather than incorrectly assigning infinite distance to the route.
        for entry in arrivals:
            row,col=grid.cell(entry[:2])
            for dr in (-1,0,1):
                for dc in (-1,0,1):
                    cell=(row+dr,col+dc)
                    if not relaxed.navigable(cell):
                        continue
                    cost=math.dist(grid.world(cell),entry[:2])+math.dist(entry[:2],plan.goal)
                    if cost<reverse_costs.get(cell,math.inf):
                        reverse_costs[cell]=cost
                        heapq.heappush(reverse_queue,(cost,cell))
    # Adjacent seeds avoid an unsafe tiny turn merely to reach the nearest
    # grid centre. Every connector is forward and collision checked.
    for departure in departures:
        row,col=grid.cell(departure[:2])
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                cell = (row+dr, col+dc)
                if not grid.navigable(cell):
                    continue
                for heading in range(8):
                    state = (*cell, heading)
                    initial = connector(departure, grid.world(cell), heading*math.pi/4)
                    if initial is None:
                        continue
                    initial=[actual]+initial
                    cost = sum(edge_cost(a, b) for a, b in zip(initial, initial[1:]))
                    if cost>=costs.get(state,math.inf):
                        continue
                    costs[state] = cost
                    seeds[state] = initial
                    heapq.heappush(queue, (cost+heuristic(state), cost, state))

    closed = set()
    while queue:
        check_cancelled()
        _, cost, state = heapq.heappop(queue)
        if state in closed or cost != costs[state]:
            continue
        if len(closed) >= max_expansions:
            raise NavigationError('Forward route exhausted its heading-search budget',
                                  'navigation_search_exhausted')
        closed.add(state)
        current = pose(state)
        for entry in arrivals:
            if math.dist(current[:2],entry[:2])>grid.resolution*1.5:
                continue
            terminal = connector(current, entry[:2], plan.final_yaw)
            if terminal is not None:
                terminal.append(goal_pose)
                states = [state]
                while states[-1] in parents:
                    states.append(parents[states[-1]])
                states.reverse()
                route = seeds[states[0]] + [pose(s) for s in states[1:]] + terminal[1:]
                return finish(route, len(closed), 'heading_lattice')
        r, c, h = state
        dr, dc = directions[h]
        for nxt in ((r, c, (h+1)%8), (r, c, (h-1)%8), (r+dr, c+dc, h)):
            if nxt in closed or not grid.navigable(nxt[:2]):
                continue
            destination = pose(nxt)
            updated = cost+edge_cost(current, destination)
            if updated >= costs.get(nxt, math.inf):
                continue
            if not collision.motion_free(current, destination):
                continue
            costs[nxt] = updated
            parents[nxt] = state
            heapq.heappush(queue, (updated+heuristic(nxt), updated, nxt))
    raise NavigationError('No collision-free forward route to the selected approach')
