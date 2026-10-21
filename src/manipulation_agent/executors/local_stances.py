"""Geometry-guided stance selection in the selected target's reach disk.

The cheap radial proposals remain first. If they fail, enumerate free map cells
in the target reach disk, regardless of distance from the action start. Geometry supplies
positions rather than discarding the whole neighbourhood after eight spokes.
Every proposal still needs visibility, departure, operation-specific checks and
a swept, forward route before it may move the robot.
"""
from collections import deque
import math

from .local_manipulation import stance_candidates


def local_stance_proposals(actual, origin, point, limits, collision, visited=(), *,
                           check_cancelled=lambda: None, diagnostics=None):
    """Yield (pose, source) within the operation reach of the selected target.

    Heading samples are a candidate generator, not a completeness claim. Include
    the measured continuous heading as well as finer world-heading proposals;
    the route planner retains continuous poses and its full collision checks.
    """
    diagnostics = diagnostics if diagnostics is not None else {}
    seen = set()

    def key(pose):
        return tuple(round(v, 6) for v in (*pose[:2], math.atan2(math.sin(pose[2]), math.cos(pose[2]))))

    radial=stance_candidates(actual, origin, point, limits, visited)
    # Give map-derived positions/headings a chance before distant ring poses
    # fill the shared route pool with alternatives behind the same obstacle.
    for pose in radial[:48]:
        check_cancelled()
        seen.add(key(pose))
        yield pose, 'radial'

    import numpy as np
    diagnostics['geometry_fallback_started'] = True
    grid = collision.grid
    row, col = grid.cell(point[:2])
    half = math.ceil(limits.reach_m / grid.resolution) + 1
    rr, cc = np.mgrid[max(0, row-half):min(grid.height, row+half+1),
                     max(0, col-half):min(grid.width, col+half+1)]
    xs = grid.origin[0] + cc * grid.resolution
    ys = grid.origin[1] + rr * grid.resolution
    reach = np.hypot(xs-float(point[0]), ys-float(point[1]))
    domain = reach <= limits.reach_m-.005
    travel = np.hypot(xs-actual[0], ys-actual[1])
    bearing = np.arctan2(ys-actual[1], xs-actual[0])
    turn = np.abs(np.arctan2(np.sin(bearing-actual[2]), np.cos(bearing-actual[2])))
    scores = travel/.5 + turn/(math.pi/3) + np.maximum(0., reach-limits.preferred_reach_m)
    diagnostics['geometry_domain_cells'] = int(domain.sum())
    diagnostics['geometry_domain'] = 'target_reach_disk'
    diagnostics['geometry_free_poses'] = 0
    buckets = {}
    # The target window accelerates endpoint checks. Route planning uses the
    # full collision map and may travel outside this candidate window.
    checker = getattr(collision, 'collision', collision)
    headings = [actual[2], *(i*math.pi/12 for i in range(24))]
    for yaw in headings:
        check_cancelled()
        oriented = checker.grid_at_heading(yaw)
        free = np.frombuffer(oriented.free, dtype='uint8').reshape(grid.height, grid.width)
        rows, cols = np.nonzero(domain & (free[rr, cc] != 0))
        diagnostics['geometry_free_poses'] += len(rows)
        for index, (r, c) in enumerate(zip(rows, cols)):
            if index % 128 == 0:
                check_cancelled()
            pose = (float(xs[r, c]), float(ys[r, c]), yaw)
            if key(pose) in seen:
                continue
            if any(math.dist(pose[:2], p[:2]) < .05 and
                   abs(math.atan2(math.sin(yaw-p[2]), math.cos(yaw-p[2]))) < math.radians(10)
                   for p in visited):
                continue
            seen.add(key(pose))
            final_turn = abs(math.atan2(math.sin(yaw-bearing[r, c]), math.cos(yaw-bearing[r, c])))
            score = float(scores[r, c]) + final_turn/(math.pi/3)
            # Round-robin nearby position/heading buckets, so an occluded patch
            # cannot consume all raycasts before another approach is considered.
            bucket = (math.floor((pose[0]-origin[0])/.2),
                      math.floor((pose[1]-origin[1])/.2),
                      round(yaw/(math.pi/4)) % 8)
            buckets.setdefault(bucket, []).append((score, pose))
    ordered = [deque(sorted(group)) for group in buckets.values()]
    ordered.sort(key=lambda group: group[0])
    while ordered:
        following = []
        for group in ordered:
            check_cancelled()
            _, pose = group.popleft()
            yield pose, 'free_space'
            if group:
                following.append(group)
        ordered = following

    for pose in radial[48:]:
        check_cancelled()
        if key(pose) not in seen:
            seen.add(key(pose))
            yield pose, 'radial'
