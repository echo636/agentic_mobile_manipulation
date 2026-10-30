"""Local native resampling for coarse-map detours and rejected in-place turns."""
from dataclasses import replace
import math

from .gt_navigation import NavigationError, PlanningBudget, plan_navigation
from .heading_collision import HeadingCollisionChecker
from .forward_navigation import local_exit_certificate, select_forward_route


def approach_refinement_window(collision, actual, point, reach_m, check_cancelled):
    """Keep the full-floor detour and its collision halo in a finer query.

    The sampled-heading guide only chooses query bounds. A projected start or
    guide path never authorizes motion; the finer native map checks the actual
    start, admitted stance and complete swept route afterward.
    """
    margin = reach_m + .7
    window = [min(actual[i], float(point[i]))-margin for i in (0, 1)] + [
        max(actual[i], float(point[i]))+margin for i in (0, 1)]
    budget = PlanningBudget(check_cancelled, seconds=3.)
    previous_check = collision.check_cancelled
    collision.check_cancelled = budget.check
    try:
        guide = collision.guidance_grid()
        start = guide.snap(actual[:2], .75)
        if start is None:
            return window
        plan = plan_navigation(guide, guide.world(start), point[:2],
            goal_mode='point', max_snap=reach_m, check_cancelled=budget.check)
        halo = max(math.hypot(*p) for _, points in collision.layers for p in points) + 2*guide.resolution
        for i in (0, 1):
            window[i] = min(window[i], min(p[i] for p in plan.points)-halo)
            window[i+2] = max(window[i+2], max(p[i] for p in plan.points)+halo)
    except NavigationError:
        pass
    finally:
        collision.check_cancelled = previous_check
    return window


def refine_in_place_turn(backend, floor, grid, start, end):
    """Recheck the same full turn against finer live collision geometry.

    A rejected coarse raster is not a collision certificate. The local window
    covers the complete rotating body and payload, including the raster halo.
    Neither the requested angle nor the robot's position is changed.
    """
    from .live_gt_map import build_navigation_grid
    attributes = ('_navigation_layers', '_navigation_support', '_navigation_clearance',
                  '_navigation_layer_yaw')
    original = {key: getattr(backend, key) for key in attributes}
    radius = max((math.hypot(*point) for _, points in backend._navigation_layers
                  for point in points), default=0.) + 4*grid.resolution
    window = [start[0]-radius, start[1]-radius, start[0]+radius, start[1]+radius]
    record = {'reason': 'coarse_map_rejected_in_place_turn',
              'coarse_resolution_m': grid.resolution, 'attempts': [], 'status': 'rejected'}
    accepted = False
    try:
        for resolution in (.025, .0125):
            if resolution >= grid.resolution-1e-6:
                continue
            backend.deadline.check()
            sampling = {'resolution': resolution, 'window': window}
            row = dict(sampling, status='checking')
            record['attempts'].append(row)
            if math.prod(math.ceil((window[i+2]-window[i])/resolution)+2
                         for i in (0, 1)) > 250000:
                row.update(status='rejected', reason='local_window_too_large')
                continue
            finer, detail = build_navigation_grid(backend, floor, sampling=sampling)
            backend._navigation_layer_yaw = start[2]
            budget = PlanningBudget(backend.deadline.check, seconds=8.)
            checker = HeadingCollisionChecker(finer, backend._navigation_layers, start[2],
                backend._navigation_support, check_cancelled=budget.check)
            try:
                clear = checker.motion_free(start, end)
                row.update(status='accepted' if clear else 'rejected',
                           query_seconds=detail['query_seconds'])
                if clear:
                    accepted = True
                    record['status'] = 'refined_turn_selected'
                    return finer, record
            except NavigationError as exc:
                row.update(status='rejected', reason=exc.code)
    finally:
        if not accepted:
            for key, value in original.items():
                setattr(backend, key, value)
    return None, record


def detour_sampling(grid, actual, goal):
    """Bound native queries spatially; never interpolate coarse obstacles."""
    margin = 2.
    window = [min(actual[i], goal[i])-margin for i in (0, 1)] + [
        max(actual[i], goal[i])+margin for i in (0, 1)]
    def cells(resolution):
        return math.prod(math.ceil((window[i+2]-window[i])/resolution)+2 for i in (0, 1))
    previous = grid.resolution
    for desired in (.025, .0125):
        if desired >= previous-1e-6:
            continue
        resolution = desired
        if cells(resolution) > 250000:
            low, high = desired, previous
            if cells(high) > 250000:
                return
            for _ in range(40):
                mid = (low+high)/2
                if cells(mid) > 250000:
                    low = mid
                else:
                    high = mid
            resolution = high
        if resolution >= previous-1e-6:
            return
        yield {'resolution': resolution, 'window': window}
        previous = resolution


def refine_navigation_detour(backend, floor, grid, map_details, actual,
                             options, plan, route, collision, selection):
    """Replace only with a faster certified route to an already admitted pose.

    Refinement is optional: the old geometry and certified route survive any
    bounded planning failure. Endpoint identity, visibility, entry/exit and
    swept body/payload checks retain their normal semantics.
    """
    direct = math.dist(actual[:2], plan.goal)
    coarse_distance = route.distance_m if route is not None else plan.geodesic_m
    if coarse_distance <= max(3*direct, direct+4.) or grid.resolution <= .0125:
        return grid, map_details, plan, route, collision, selection, None
    from .live_gt_map import build_navigation_grid
    attributes = ('_navigation_layers', '_navigation_support', '_navigation_clearance',
                  '_navigation_layer_yaw')
    original = {key: getattr(backend, key) for key in attributes}
    accepted = False
    record = {'reason': 'long_coarse_grid_detour' if route is not None else 'long_coarse_grid_guide',
              'coarse_distance_m': coarse_distance,
              'direct_distance_m': direct, 'coarse_resolution_m': grid.resolution,
              'attempts': [], 'status': 'kept_coarse_route'}
    allow_lateral = bool(getattr(backend.robot, 'is_holonomic_base', False))
    candidates = [p for p in options if p.score <= plan.score+1e-9]
    try:
        for sampling in detour_sampling(grid, actual, plan.goal):
            backend.deadline.check()
            row = dict(sampling, status='checking')
            record['attempts'].append(row)
            finer, detail = build_navigation_grid(backend, floor, sampling=sampling)
            backend._navigation_layer_yaw = actual[2]
            budget = PlanningBudget(backend.deadline.check, seconds=10.)
            checker = HeadingCollisionChecker(finer, backend._navigation_layers, actual[2],
                backend._navigation_support, check_cancelled=budget.check)
            proposals = []
            certificates = {}
            try:
                for candidate in candidates:
                    budget.check()
                    pose = (*candidate.goal, candidate.final_yaw)
                    if pose in certificates:
                        continue
                    if not checker.motion_free(pose, pose):
                        continue
                    entry = local_exit_certificate(pose, checker, incoming=True,
                        check_cancelled=budget.check, allow_lateral=allow_lateral)
                    departure = local_exit_certificate(pose, checker,
                        check_cancelled=budget.check, allow_lateral=allow_lateral)
                    if entry is None or departure is None:
                        continue
                    certificates[pose] = (entry, departure)
                    # The endpoint is unchanged. A finer fixed-heading guide
                    # helps the full pose planner but need not exist itself.
                    points = (actual[:2], candidate.goal)
                    distance = math.dist(actual[:2], candidate.goal)
                    try:
                        guide = plan_navigation(checker.grid_at_heading(candidate.final_yaw),
                            actual[:2], candidate.goal, goal_mode='point', max_snap=None,
                            candidate_filter=lambda xy: math.dist(xy, candidate.goal) <= finer.resolution/math.sqrt(2)+1e-8,
                            check_cancelled=budget.check)
                        points = (*guide.points[:-1], candidate.goal)
                        distance = guide.geodesic_m
                    except NavigationError:
                        pass
                    proposals.append(replace(candidate, points=points, geodesic_m=distance))
                row['endpoints'] = len(proposals)
                if not proposals:
                    row.update(status='rejected', reason='no_admitted_endpoint')
                    continue
                selected, refined_route, refined_selection = select_forward_route(
                    proposals, actual, checker, target_slack=0.,
                    check_cancelled=budget.check, planning_seconds=10., allow_lateral=allow_lateral)
                row.update(distance_m=refined_route.distance_m,
                           motion_seconds=refined_route.estimated_motion_seconds)
                if (refined_route.distance_m >= coarse_distance-.5 or
                        (route is not None and
                         refined_route.estimated_motion_seconds >= route.estimated_motion_seconds)):
                    row.update(status='rejected', reason='no_motion_improvement')
                    continue
                entry, departure = next(value for pose, value in certificates.items()
                    if math.dist(pose[:2], selected.goal) < 1e-8 and
                    abs(math.atan2(math.sin(pose[2]-selected.final_yaw),
                                   math.cos(pose[2]-selected.final_yaw))) < 1e-8)
                refined_selection.update(local_entry_certificate=entry, local_exit_certificate=departure)
                checker.check_cancelled = backend.deadline.check
                row['status'] = 'accepted'
                record['status'] = 'refined_route_selected'
                import json
                import numpy as np
                folder = backend.output / 'navigation_refinements'
                folder.mkdir(exist_ok=True)
                backend._navigation_refinement_index = getattr(backend, '_navigation_refinement_index', 0)+1
                artifact = folder / f'{backend._navigation_refinement_index:04d}.npz'
                np.savez_compressed(artifact, raw_layers=np.stack([raw for raw, _ in checker.layers]),
                    support=backend._navigation_support, origin=finer.origin, resolution=finer.resolution,
                    start=actual, target=selected.target, initial_layer_yaw=actual[2],
                    layer_footprints_json=json.dumps([{'footprint':shape.tolist()} for _, shape in checker.layers]))
                record['grid_artifact'] = str(artifact.relative_to(backend.output))
                accepted = True
                return (checker.grid_at_heading(selected.final_yaw), detail, selected,
                        refined_route, checker, refined_selection, record)
            except NavigationError as exc:
                row.update(status='rejected', reason=exc.code, detail=str(exc))
            finally:
                checker.check_cancelled = backend.deadline.check
    finally:
        if not accepted:
            for key, value in original.items():
                setattr(backend, key, value)
    return grid, map_details, plan, route, collision, selection, record
