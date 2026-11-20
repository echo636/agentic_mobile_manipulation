"""Harness option --place-return (2026-11-21, default off): drive back to a pose the robot itself recorded, along
places it has been (coordinator 2026-11-21 05:39, design sec20/place_memory/DESIGN_place_return_20261120.md sec. 5).

New file only; no existing action path changes. Everything here is executor-private: odometry poses, the visited-area
grid, the planned route and its length never reach the model. The public outcome is the ordinary navigation status.

Visited area = the union of discs of VISIT_RADIUS_M around every waypoint of every executed navigation route
(navigation_plans.jsonl, status 'planned'), around every recorded base endpoint (base_motion.jsonl) and around the
start pose. A base move without a route record adds only its endpoint disc (counted privately). Assumption (stated in the
design and the paper limitations): intersecting this area with the walkable map supposes that a real robot, while
driving, saw with its own sensors which cells within VISIT_RADIUS_M around it are walkable. The walkable map is the one
navigation uses at this moment, so a door closed since is not crossed (no door is opened for the model).
"""
from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

from ..contracts import SkillError

VISIT_RADIUS_M = 0.6
NEAR_GOAL_M = 0.6
UNREACHABLE_MESSAGE = ('No known route back to that place through places the robot has been. '
                       'Use navigate_to on what you see to go there step by step.')


def _rows(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def visited_points(output: Path, start_xy=None):
    """World xy points the robot has occupied, and how many base moves had no route record."""
    output = Path(output)
    points = [] if start_xy is None else [list(start_xy)]
    routed_ends = set()
    for row in _rows(output / 'navigation_plans.jsonl'):
        if row.get('status') == 'planned' and isinstance(row.get('path'), list):
            for p in row['path']:
                points.append([float(p[0]), float(p[1])])
            routed_ends.add(tuple(round(float(v), 2) for v in row['path'][-1][:2]))
    unrouted = 0
    for row in _rows(output / 'base_motion.jsonl'):
        p = row.get('actual_position')
        if not p:
            continue
        points.append([float(p[0]), float(p[1])])
        commanded = row.get('commanded_position') or p
        if tuple(round(float(v), 2) for v in commanded[:2]) not in routed_ends:
            unrouted += 1
    return points, unrouted


def astar(free, start, goal):
    """8-connected A* on a boolean grid; returns the list of cells or None."""
    rows, cols = free.shape
    if not (free[start] and free[goal]):
        return None
    sqrt2 = math.sqrt(2.0)
    def h(c):
        dr, dc = abs(c[0] - goal[0]), abs(c[1] - goal[1])
        return (dr + dc) + (sqrt2 - 2) * min(dr, dc)
    openq = [(h(start), 0.0, start)]
    came, cost = {start: None}, {start: 0.0}
    while openq:
        _, g, cur = heapq.heappop(openq)
        if cur == goal:
            path = [cur]
            while came[path[-1]] is not None:
                path.append(came[path[-1]])
            return path[::-1]
        if g > cost.get(cur, math.inf):
            continue
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nxt = (cur[0] + dr, cur[1] + dc)
                if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or not free[nxt]:
                    continue
                if dr and dc and not (free[cur[0] + dr, cur[1]] and free[cur[0], cur[1] + dc]):
                    continue   # no corner cutting through a blocked cell
                ng = g + (sqrt2 if dr and dc else 1.0)
                if ng < cost.get(nxt, math.inf):
                    cost[nxt], came[nxt] = ng, cur
                    heapq.heappush(openq, (ng + h(nxt), ng, nxt))
    return None


def restricted_grid(occupancy_free, cells, radius_cells):
    """Walkable cells within radius_cells of any visited cell."""
    import numpy as np
    import cv2
    visited = np.zeros(occupancy_free.shape, dtype=np.uint8)
    for r, c in cells:
        if 0 <= r < visited.shape[0] and 0 <= c < visited.shape[1]:
            cv2.circle(visited, (int(c), int(r)), int(radius_cells), 1, -1)
    return occupancy_free & (visited != 0)


def nearest_free(free, cell, max_cells):
    import numpy as np
    if free[cell]:
        return cell
    r0, c0 = cell
    rr, cc = np.nonzero(free[max(0, r0 - max_cells):r0 + max_cells + 1, max(0, c0 - max_cells):c0 + max_cells + 1])
    if not len(rr):
        return None
    rr = rr + max(0, r0 - max_cells); cc = cc + max(0, c0 - max_cells)
    d = (rr - r0) ** 2 + (cc - c0) ** 2
    i = int(d.argmin())
    return (int(rr[i]), int(cc[i])) if d[i] <= max_cells ** 2 else None


def return_to_pose(backend, goal_xy, goal_yaw, max_steps, *, start_xy=None, visited_only=True):
    """Plan inside the visited area (or the whole walkable map when visited_only is False) and drive there."""
    import numpy as np
    from .compat.native_astar import sync_added_fixture_map, sync_open_door_map
    sync_open_door_map(backend)
    sync_added_fixture_map(backend)
    torch = backend.torch
    trav = backend.env.scene.trav_map
    position = backend.robot.get_position_orientation()[0]
    floor = min(range(len(trav.floor_heights)), key=lambda i: abs(float(position[2]) - trav.floor_heights[i]))
    free = trav._erode_trav_map(trav.floor_map[floor].clone(), robot=backend.robot).cpu().numpy() != 0
    res = float(trav.map_resolution)
    to_cell = lambda xy: tuple(int(v) for v in trav.world_to_map(torch.tensor([float(xy[0]), float(xy[1])])).tolist())
    start = to_cell(position[:2].cpu().tolist())
    record = {'audience': 'executor_private', 'visited_only': bool(visited_only), 'radius_m': VISIT_RADIUS_M,
              'resolution_m': res}
    if visited_only:
        pts, unrouted = visited_points(backend.output, start_xy)
        grid = restricted_grid(free, [to_cell(p) for p in pts], math.ceil(VISIT_RADIUS_M / res))
        record.update(visited_points=len(pts), unrouted_base_moves=unrouted)
    else:
        grid = free.copy()
    if 0 <= start[0] < grid.shape[0] and 0 <= start[1] < grid.shape[1]:
        grid[start] = True    # the robot stands here
    record['restricted_free_cells'] = int(grid.sum())
    goal = to_cell(goal_xy)
    reach = nearest_free(grid, goal, math.ceil(NEAR_GOAL_M / res)) if 0 <= goal[0] < grid.shape[0] and 0 <= goal[1] < grid.shape[1] else None
    cells = astar(grid, start, reach) if reach is not None else None
    full = None
    try:
        full_path, full_len = trav.get_shortest_path(floor, position[:2].cpu(), torch.tensor(list(goal_xy[:2])),
                                                     entire_path=True, robot=backend.robot)
        full = None if full_path is None else float(full_len)
    except Exception:
        full = None
    record['whole_map_route_m'] = full
    if cells is None:
        record.update(status='unreachable', reason='goal_outside_visited_area' if reach is None else 'visited_area_disconnected')
        _log(backend, record)
        raise SkillError('navigation_unreachable', UNREACHABLE_MESSAGE)
    world = trav.map_to_world(torch.tensor(cells, dtype=torch.float32)).cpu().tolist()
    route = [position[:2].cpu().tolist(), *world[1:], world[-1]]
    route_len = sum(math.dist(a, b) for a, b in zip(route, route[1:]))
    record.update(status='planned', route_m=route_len, route_points=len(route), exact=reach == goal)
    result = backend._execute_base_path(route, goal_yaw, max_steps)
    backend.navigation_distance += result['actual_path_distance_m']
    _log(backend, record)
    status = 'reached' if reach == goal else 'approached'
    return {**result, 'nav_status': status, 'strategy': 'place_return_visited_area' if visited_only else 'place_return_whole_map'}


def _log(backend, record):
    import time
    with (Path(backend.output) / 'place_return.jsonl').open('a') as stream:
        stream.write(json.dumps({'at_unix': time.time(), **record}) + '\n')
