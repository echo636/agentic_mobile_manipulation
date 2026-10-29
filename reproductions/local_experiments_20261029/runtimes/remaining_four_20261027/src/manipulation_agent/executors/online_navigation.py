"""Navigation over a current sensor-built occupancy snapshot, without GT maps.

The occupancy -> inflated costmap -> octile A* workflow follows habitat-gs
jiarui/memory-slam, 136950b2fb419b90d2607d4cfc6832f3957eeaa5,
tools/habitat_agent/slam/occupancy_navigation.py. Unlike its optional start
clearing, this implementation never turns unknown or occupied cells into free
space. Candidate semantics reuse the project's visual-point adapter. GridMap
is a geometry container only; importing it does not read OmniGibson maps.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

from .gt_navigation import (
    GridMap, NavigationError, NavigationPlan, candidate_points, candidate_score,
)

SOURCE_COMMIT = '136950b2fb419b90d2607d4cfc6832f3957eeaa5'
STRATEGY = 'sensor_occupancy_astar_v1'


def observed_grid(snapshot, *, robot_radius, clearance=0.0):
    """Inflate occupied, unknown and out-of-map cells by a circular footprint.

    Snapshot fields are width, height, resolution, origin and row-major
    occupancy bytes: 0=observed free, 100=occupied, 255=unknown. Origin is the
    world XY centre of row 0 / column 0. A footprint must be entirely inside
    observed free cells; its current location is not an exception. Source
    bytes are never mutated. Both mapping updates and dynamic obstacles are
    incorporated by supplying a new immutable snapshot on the next call.
    """
    radius = float(robot_radius) + float(clearance)
    if not all(math.isfinite(float(x)) and float(x) >= 0
               for x in (robot_radius, clearance)):
        raise ValueError('Footprint radius and clearance must be finite and nonnegative')
    # Validate dimensions/origin before NumPy reshaping or indexing.
    grid = GridMap(snapshot.width, snapshot.height, float(snapshot.resolution),
                   tuple(snapshot.origin), bytes(snapshot.width * snapshot.height))
    values = np.frombuffer(snapshot.occupancy, dtype=np.uint8)
    if values.size != grid.width * grid.height or not np.isin(values, [0, 100, 255]).all():
        raise ValueError('Occupancy must contain exactly width*height cells of 0, 100 or 255')
    observed = values.reshape(grid.height, grid.width) == 0
    free = observed.copy()
    if radius > 0:
        reach = math.ceil(radius / grid.resolution + .5)
        padded = np.pad(observed, reach, constant_values=False)
        # A neighbouring cell is blocked if its closed square intersects the
        # disk. This accounts for cell extent, not just cell-centre distance.
        for dr in range(-reach, reach + 1):
            for dc in range(-reach, reach + 1):
                dx = max(abs(dc) * grid.resolution - grid.resolution / 2, 0.)
                dy = max(abs(dr) * grid.resolution - grid.resolution / 2, 0.)
                if math.hypot(dx, dy) <= radius + 1e-12:
                    free &= padded[reach + dr:reach + dr + grid.height,
                                   reach + dc:reach + dc + grid.width]
    return GridMap(grid.width, grid.height, grid.resolution, grid.origin,
                   free.astype(np.uint8).tobytes())


def _astar_goals(grid, start, goals, check_cancel=None):
    """One A* search gives shortest distances to all supplied approach goals.

    Minimum octile distance to the *fixed* goal set is consistent. Keeping it
    fixed when a goal closes preserves A* correctness for the remaining goals.
    No diagonal may cut across an unknown/blocked corner (GridMap.neighbors).
    """
    pending = set(goals)
    goal_cells = tuple(pending)
    def heuristic(cell):
        best = math.inf
        for other in goal_cells:
            dr, dc = abs(cell[0] - other[0]), abs(cell[1] - other[1])
            best = min(best, max(dr, dc) + (math.sqrt(2) - 1) * min(dr, dc))
        return best * grid.resolution
    costs, parents, closed = {start: 0.}, {}, set()
    queue = [(heuristic(start), 0., start)]
    while queue and pending:
        _, cost, cell = heapq.heappop(queue)
        if cell in closed:
            continue
        if check_cancel is not None and len(closed) % 256 == 0:
            check_cancel()
        closed.add(cell)
        pending.discard(cell)
        for neighbor, edge_cost in grid.neighbors(cell):
            if neighbor in closed:
                continue
            updated = cost + edge_cost
            if updated < costs.get(neighbor, math.inf):
                costs[neighbor] = updated
                parents[neighbor] = cell
                heapq.heappush(queue, (updated + heuristic(neighbor), updated, neighbor))
    return costs, parents, closed


def plan_online_navigation(grid, current, target, *, standoff=1.15,
                           min_target_distance=1.0, max_target_distance=1.4, max_snap=.75,
                           candidate_filter=None, fixed_goal=None,
                           check_cancel=None):
    """Select a visible approach or replan to a previously selected endpoint.

    Only observed free space is traversed. This planner does not explore
    randomly, teleport the start, load a traversability map or use a NavMesh.
    A missing observed route is returned as an actionable NavigationError.
    ``fixed_goal`` keeps the destination stable as new sensor maps arrive;
    newly blocked fixed goals fail instead of silently snapping somewhere else.
    The returned NavigationPlan works with the existing feedback follower.
    """
    if not all(math.isfinite(float(x)) and float(x) >= 0
               for x in (standoff, min_target_distance, max_snap, max_target_distance)):
        raise ValueError('Finite nonnegative approach distances required')
    standoff = min(standoff, max_target_distance)
    min_target_distance = min(min_target_distance, standoff)
    current, target = tuple(map(float, current)), tuple(map(float, target))
    start = grid.cell(current)
    grid.cell(target)
    if not grid.navigable(start) or not grid.segment_free(current, grid.world(start)):
        raise NavigationError(
            'The current footprint is not wholly in observed free space; '
            'collect valid nearby range observations before navigating.',
            'navigation_invalid_start')
    if check_cancel is not None:
        check_cancel()
    visibility = {}
    def acceptable(cell):
        xy = grid.world(cell)
        distance = math.dist(xy, target)
        if distance < min_target_distance - 1e-9 or distance > max_target_distance + 1e-9:
            return False
        if cell not in visibility:
            visibility[cell] = candidate_filter is None or bool(candidate_filter(xy))
        return visibility[cell]
    def project(reachable=None):
        candidates = {}
        for raw in candidate_points(target, current, standoff):
            cell = grid.snap(raw, max_snap, lambda c:
                             (reachable is None or c in reachable) and acceptable(c))
            if cell is None:
                continue
            snap_distance = math.dist(grid.world(cell), raw)
            candidates[cell] = min(candidates.get(cell, math.inf), snap_distance)
        return candidates
    if fixed_goal is not None:
        fixed_goal = tuple(map(float, fixed_goal))
        cell = grid.cell(fixed_goal)
        # The destination is a world point, not a particular cropped map's
        # cell centre. Preserve it exactly and validate its short connection
        # to the current cell centre with the same collision geometry used
        # by A*. This also handles float32 map-origin roundoff during growth.
        if (not grid.navigable(cell)
                or not grid.segment_free(grid.world(cell), fixed_goal)
                or math.dist(fixed_goal, target) > max_target_distance + 1e-9
                or (candidate_filter is not None and not candidate_filter(fixed_goal))):
            raise NavigationError('The selected endpoint is blocked or unobserved in the new map.',
                                  'navigation_path_blocked')
        candidates = {cell: 0.}
    else:
        candidates = project()
    if not candidates:
        raise NavigationError(
            'No observed free approach within the target range; obtain more '
            'range observations or select a reachable visible target.',
            'navigation_unobserved_route')
    costs, parents, closed = _astar_goals(grid, start, candidates, check_cancel)
    viable = [(candidate_score(math.dist(grid.world(c), target), snap, costs[c], standoff),
               costs[c], c) for c, snap in candidates.items() if c in closed]
    if not viable and fixed_goal is None:
        # A nearest geometric projection can be on an observed but disconnected
        # island. Search exhaustion gives the actual connected component; try
        # bounded projections there without changing the start or clearing cells.
        candidates = project(closed)
        viable = [(candidate_score(math.dist(grid.world(c), target), snap, costs[c], standoff),
                   costs[c], c) for c, snap in candidates.items()]
    if not viable:
        raise NavigationError(
            'The observed map has no connected route to this approach. '
            'Unknown space is blocked; refresh observations or choose another target.',
            'navigation_unobserved_route')
    score, distance, goal_cell = min(viable)
    goal = fixed_goal if fixed_goal is not None else grid.world(goal_cell)
    distance += math.dist(grid.world(goal_cell), goal)
    cells = [goal_cell]
    while cells[-1] != start:
        cells.append(parents[cells[-1]])
    points = [current, *[grid.world(c) for c in reversed(cells)]]
    if points[-1] != goal:
        points.append(goal)
    simplified, index = [current], 0
    while index < len(points) - 1:
        far = index + 1
        for end in range(index + 2, len(points)):
            if not grid.segment_free(points[index], points[end]):
                break
            far = end
        if math.dist(simplified[-1], points[far]) > 1e-9:
            simplified.append(points[far])
        index = far
    if simplified[-1] != goal:
        simplified.append(goal)
    if not all(grid.segment_free(a, b) for a, b in zip(simplified, simplified[1:])):
        raise NavigationError('Route crossed an unobserved or occupied cell.', 'navigation_path_blocked')
    return NavigationPlan(tuple(simplified), goal, target,
                          math.atan2(target[1] - goal[1], target[0] - goal[0]),
                          distance, score, len(candidates), len(viable),
                          math.dist(current, grid.world(start)), len(closed))
