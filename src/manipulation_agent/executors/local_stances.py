"""Geometry-guided stance selection in the selected target's reach disk.

The cheap radial proposals remain first. If they fail, enumerate free map cells
in the target reach disk, regardless of distance from the action start. Geometry supplies
positions rather than discarding the whole neighbourhood after eight spokes.
Every proposal still needs visibility, departure, operation-specific checks and
a swept, forward route before it may move the robot.
"""
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
    chunks = []
    # Keep dense geometry in arrays. Building a Python pose/key/list entry for
    # every cell and heading used most of the preparation budget before the
    # caller could inspect even one candidate on a fine map.
    checker = getattr(collision, 'collision', collision)
    headings = [actual[2], *(i*math.pi/12 for i in range(24))]
    for yaw in headings:
        check_cancelled()
        oriented = checker.grid_at_heading(yaw)
        free = np.frombuffer(oriented.free, dtype='uint8').reshape(grid.height, grid.width)
        rows, cols = np.nonzero(domain & (free[rr, cc] != 0))
        diagnostics['geometry_free_poses'] += len(rows)
        x, y = xs[rows, cols], ys[rows, cols]
        keep = np.ones(len(rows), dtype=bool)
        for previous in visited:
            if abs(math.atan2(math.sin(yaw-previous[2]), math.cos(yaw-previous[2]))) < math.radians(10):
                keep &= np.hypot(x-previous[0], y-previous[1]) >= .05
        # Exclude the already emitted radial poses before grouping, preserving
        # their effect on bucket ordering without rounding every free pose.
        yaw_key = round(math.atan2(math.sin(yaw), math.cos(yaw)), 6)
        for previous in seen:
            if previous[2] == yaw_key:
                keep &= ~((np.round(x, 6) == previous[0]) & (np.round(y, 6) == previous[1]))
        rows, cols, x, y = rows[keep], cols[keep], x[keep], y[keep]
        final_turn = np.abs(np.arctan2(np.sin(yaw-bearing[rows, cols]),
                                       np.cos(yaw-bearing[rows, cols])))
        score = scores[rows, cols] + final_turn/(math.pi/3)
        chunks.append(np.column_stack((score, x, y, np.full(len(x), yaw),
            np.floor((x-origin[0])/.2), np.floor((y-origin[1])/.2),
            np.full(len(x), round(yaw/(math.pi/4)) % 8))))
    check_cancelled()
    candidates = np.concatenate(chunks)
    if len(candidates):
        # Lexicographic bucket/score/pose ordering matches the original list
        # sorting. Yield one pose per bucket per round to retain spatial and
        # heading diversity, converting only poses the caller actually sees.
        order = np.lexsort(tuple(candidates[:, i] for i in (3, 2, 1, 0, 6, 5, 4)))
        candidates = candidates[order]
        edges = np.flatnonzero(np.any(np.diff(candidates[:, 4:], axis=0) != 0, axis=1)) + 1
        starts = np.r_[0, edges]
        ends = np.r_[edges, len(candidates)]
        groups = sorted(zip(starts, ends), key=lambda pair: tuple(candidates[pair[0], :4]))
        while groups:
            following = []
            for index, end in groups:
                check_cancelled()
                while index < end:
                    pose = tuple(map(float, candidates[index, 1:4]))
                    index += 1
                    pose_key = key(pose)
                    if pose_key not in seen:
                        seen.add(pose_key)
                        yield pose, 'free_space'
                        break
                if index < end:
                    following.append((index, end))
            groups = following

    for pose in radial[48:]:
        check_cancelled()
        if key(pose) not in seen:
            seen.add(key(pose))
            yield pose, 'radial'
