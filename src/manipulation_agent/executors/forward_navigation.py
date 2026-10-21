"""CPU-only forward driving: certify a polyline, or search XY + heading.

The collision adapter owns geometry. Search never moves the robot. A route is
made of checked in-place turns and forward translations, including the final
orientation required by the visual approach planner.
"""
from dataclasses import dataclass, replace
import heapq
import math
import time

from .gt_navigation import NavigationError, NavigationPlan, PlanningBudget, SearchBudgetExhausted


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


@dataclass(frozen=True)
class ForwardRoute:
    phases: tuple[NavigationPlan, ...]
    distance_m: float
    expanded_states: int
    method: str
    shortcuts: int = 0
    turn_radians: float = 0.
    estimated_motion_seconds: float = 0.
    start: tuple | None = None


def motion_seconds(poses):
    """Same linear/angular rate limits as the execution follower."""
    return sum(math.dist(a[:2], b[:2])/.5 + abs(angle(b[2]-a[2]))/(math.pi/3)
               for a, b in zip(poses, poses[1:]))


def arrival_heading_options(plan, actual, *, approach=False):
    """Bounded terminal headings; callers must certify geometry and visibility.

    Four outward cameras can have gaps after applying their image-edge margin.
    Nearby headings let a visible object enter a camera without restoring the
    departure yaw. A target-facing heading supplies a wider fallback.
    """
    last=next((xy for xy in reversed(plan.points) if math.dist(xy,plan.goal)>.002),actual[:2])
    bearing=(math.atan2(plan.goal[1]-last[1],plan.goal[0]-last[0])
             if math.dist(last,plan.goal)>.002 else actual[2])
    proposals=[(bearing,'arrival_aligned')]
    if approach:
        proposals.extend((bearing+math.radians(offset),'near_arrival') for offset in (-15,15,-30,30))
        if math.dist(plan.target,plan.goal)>.002:
            proposals.append((math.atan2(plan.target[1]-plan.goal[1],plan.target[0]-plan.goal[0]),'target_facing'))
    result=[]
    for yaw,kind in proposals:
        yaw=angle(yaw)
        if abs(angle(yaw-plan.final_yaw))<1e-8 or any(abs(angle(yaw-a))<1e-8 for a,_ in result):continue
        result.append((yaw,kind))
    return result


def checked_connection(start, xy, final_yaw, collision, check_cancelled=lambda: None):
    """Checked turn / forward / turn, with bounded numeric pose correction."""
    poses = [start]
    distance = math.dist(start[:2], xy)
    if distance > .002:
        heading = math.atan2(xy[1]-start[1], xy[0]-start[0])
        poses += [(*start[:2], heading), (*xy, heading)]
    elif distance > 1e-10:
        # Match the follower's 2 mm pose-feedback tolerance. A sub-micrometre
        # grid snap must not require a large turn toward its numerical bearing.
        poses.append((*xy, start[2]))
    poses.append((*xy, final_yaw))
    checked = [poses[0]]
    for a, b in zip(poses, poses[1:]):
        check_cancelled()
        if collision.motion_free(a, b):
            checked.append(b)
            continue
        if math.dist(a[:2], b[:2]) > 1e-10:
            return None
        # The shorter rotation may sweep a payload into a nearby shelf even
        # though the other direction is clear. Split the longer turn into
        # two sub-turns, each strictly shorter than pi, so the follower and
        # collision adapter execute the same certified direction. Never
        # replace a blocked translation or a colliding stationary pose.
        delta = angle(b[2]-a[2])
        if abs(delta) < 1e-8:
            return None
        opposite = delta-math.copysign(2*math.pi, delta)
        middle = (*a[:2], a[2]+opposite/2)
        check_cancelled()
        if not collision.motion_free(a, middle) or not collision.motion_free(middle, b):
            return None
        checked.extend((middle, b))
    return checked


def local_exit_certificate(pose, collision, *, distance=.3, incoming=False, check_cancelled=lambda: None):
    """Certify a bounded forward departure from a candidate terminal pose.

    This proves a local exit in current geometry, not reachability of every
    future target. With incoming=True, certify an arrival ending at this pose
    instead. Both certificates contain only forward translations and checked
    turns. No reverse/sideways movement is assumed. Small forward
    staging legs also permit leaving a narrow stance before turning.
    """
    pose = tuple(map(float, pose))
    if not collision.motion_free(pose, pose):
        return None
    direction = -1 if incoming else 1
    for staging in (0., .1, .2):
        check_cancelled()
        start = (pose[0]+direction*staging*math.cos(pose[2]), pose[1]+direction*staging*math.sin(pose[2]), pose[2])
        if not collision.motion_free(pose, start):
            break
        for offset in (0., math.pi/4, -math.pi/4, math.pi/2, -math.pi/2,
                       3*math.pi/4, -3*math.pi/4, math.pi, -math.pi):
            check_cancelled()
            heading = pose[2]+offset
            turned = (*start[:2], heading)
            # A pi turn has two different swept volumes. Tiny feedback yaw
            # residuals can make atan2(sin(delta), cos(delta)) choose the
            # opposite sign for BOTH nominal +/-pi proposals. Certify each
            # intended direction as two unambiguous quarter turns, and keep
            # that intermediate pose in the certificate for execution.
            turns = ([start, (*start[:2], pose[2]+offset/2), turned]
                     if abs(offset) == math.pi else [start, turned])
            if not all(collision.motion_free(a,b) for a,b in zip(turns,turns[1:])):
                continue
            end = (start[0]+direction*(distance+staging)*math.cos(heading),
                   start[1]+direction*(distance+staging)*math.sin(heading), heading)
            if not collision.motion_free(turned, end):
                continue
            poses = [pose, *turns, end]
            if incoming: poses.reverse()
            if incoming and not all(collision.motion_free(a,b) for a,b in zip(poses,poses[1:])):
                continue
            return {'poses': poses,
                    'displacement_m': math.dist(pose[:2], end[:2]),
                    'distance_m': distance+2*staging, 'motion_mode': 'forward',
                    'scope': ('local_arrival' if incoming else 'local_departure')+'_in_current_collision_geometry'}
    return None


def simplify_forward_poses(poses, collision, *, check_cancelled=lambda: None, max_checks=128):
    """Remove checked detours only when full motion time decreases.

    This is bounded shortcutting of an existing valid path, not a continuous
    globally optimal planner. All retained/new turns preserve swept clearance.
    """
    compact = []
    for p in poses:
        if compact and math.dist(p[:2], compact[-1][:2]) < 1e-10 and abs(angle(p[2]-compact[-1][2])) < 1e-10:
            continue
        if len(compact) >= 2:
            a, b = compact[-2:]
            if (abs(angle(a[2]-b[2])) < 1e-8 and abs(angle(b[2]-p[2])) < 1e-8
                    and abs(math.dist(a[:2], b[:2])+math.dist(b[:2], p[:2])-math.dist(a[:2], p[:2])) < 1e-8):
                compact.pop()
        compact.append(p)
    result = [compact[0]]; index = checks = shortcuts = 0
    while index < len(compact)-1:
        best = None; saving = 1e-5
        for end in range(min(len(compact)-1, index+32), index+1, -1):
            if checks >= max_checks:
                break
            checks += 1
            candidate = checked_connection(compact[index], compact[end][:2], compact[end][2],
                                           collision, check_cancelled)
            if candidate is None:
                continue
            gain = motion_seconds(compact[index:end+1])-motion_seconds(candidate)
            if gain > saving:
                saving = gain; best = (end, candidate)
        if best is None:
            index += 1; result.append(compact[index])
        else:
            index, candidate = best; result.extend(candidate[1:]); shortcuts += 1
    return result, shortcuts


def route_from_poses(plan, poses, collision, *, expanded=0, method="checked_pose_graph", check_cancelled=lambda: None):
    """Build follower phases from a fully certified pose chain."""
    actual=poses[0]
    # A certified route must survive expiration during optional smoothing.
    try:poses, shortcuts = simplify_forward_poses(poses, collision, check_cancelled=check_cancelled)
    except SearchBudgetExhausted:shortcuts=0
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
    turns = sum(abs(angle(b[2]-a[2])) for a, b in zip(poses, poses[1:]))
    return ForwardRoute(tuple(phases), distance, expanded, method,
                        shortcuts, turns, motion_seconds(poses),actual)


def select_forward_route(options, actual, collision, *, target_slack=.1,
                         check_cancelled=lambda: None, max_candidates=6, project_point=False,
                         planning_seconds=10.):
    """Search acceptable endpoints together, allowing explicit endpoint slack.

    Candidates already have local entry/exit and optional visibility proofs.
    Rank cheap connections by travel/turn cost, then share one bidirectional
    search across the remaining endpoints. Feasibility precedes shortcutting;
    no continuous/global shortest-route guarantee is made.
    """
    if not math.isfinite(planning_seconds) or planning_seconds<=0:
        raise ValueError('Forward route planning budget must be positive and finite')
    closest=min(p.score for p in options)
    candidates={}
    for p in options:
        if p.score>closest+target_slack+1e-9:continue
        yaw=angle(p.final_yaw)
        # Equivalent 90 / -270 degree endpoints must not restart a search.
        key=(*p.goal,round(math.cos(yaw),9),round(math.sin(yaw),9))
        candidates.setdefault(key,replace(p,final_yaw=yaw))
    def estimate(p):
        bearing=math.atan2(p.goal[1]-actual[1],p.goal[0]-actual[0])
        # The admitted guide already measures obstacle detours. Euclidean
        # ranking can fill the bounded pool with headings at a nearby endpoint
        # whose only guide goes around the house, dropping a short approach on
        # the other side. This is ordering only; every route is still checked.
        travel=max(math.dist(actual[:2],p.goal),p.geodesic_m)
        return (travel/.5+
                (abs(angle(bearing-actual[2]))+abs(angle(p.final_yaw-bearing)))/(math.pi/3))
    ordered=sorted(candidates.values(),key=lambda p:(estimate(p),p.score))[:max_candidates]
    budget=PlanningBudget(check_cancelled,seconds=planning_seconds)
    attempts=[{'goal':p.goal,'final_yaw':p.final_yaw,'target_distance_m':p.score,
               'status':'shared_search'} for p in ordered]
    collision.check_cancelled=budget.check
    try:
        if project_point and closest>collision.grid.resolution/math.sqrt(2)+1e-9:
            from .point_route_projection import project_forward_point
            projection_budget=PlanningBudget(budget.check,seconds=3.)
            collision.check_cancelled=projection_budget.check
            try:
                projected=project_forward_point(options,actual,collision,projection_budget.check)
            except (SearchBudgetExhausted,ImportError):
                projected=None
            finally:
                collision.check_cancelled=budget.check
            if projected is not None:
                selected,poses,details=projected
                route=route_from_poses(selected,poses,collision,method='checked_forward_point_projection',
                                       check_cancelled=budget.check)
                details.update(closest_candidate_m=closest,target_slack_m=None,total_seconds=planning_seconds,
                               objective='nearest_checked_forward_reachable_grid_pose_then_motion_cost')
                return selected,route,details
        route=plan_forward_route(ordered[0],actual,collision,check_cancelled=budget.check,
                                 max_expansions=20000,alternatives=ordered[1:],
                                 roadmap_seconds=min(10.,planning_seconds/2))
    except NavigationError as exc:
        exc.attempts=attempts
        raise
    finally:
        collision.check_cancelled=check_cancelled
    final=route.phases[-1]
    selected=next(p for p in ordered if math.dist(p.goal,final.goal)<1e-6
                  and abs(angle(p.final_yaw-final.final_yaw))<1e-6)
    for candidate,row in zip(ordered,attempts):
        row['status']='planned' if candidate is selected else 'not_selected'
        if candidate is selected:
            row.update(motion_seconds=route.estimated_motion_seconds,
                       turn_degrees=math.degrees(route.turn_radians),distance_m=route.distance_m)
    return selected,route,{'target_slack_m':target_slack,'closest_candidate_m':closest,
                          'max_candidates':max_candidates,'candidate_seconds':None,'total_seconds':planning_seconds,'attempts':attempts,
                          'search':'shared_multi_goal_continuous_pose','unique_endpoints':len(ordered),
                          'objective':'first_checked_route_then_motion_shortcuts','globally_optimal':False}


def plan_forward_route(plan, actual, collision, *, check_cancelled=lambda: None,
                       max_expansions=20000, alternatives=(), roadmap_seconds=5.):
    """Preserve the selected goal and final yaw; never fall back to strafing.

First certify the existing pruned path. If its corners cannot be turned, a
continuous-pose A* searches for turning space. It can traverse a narrow corridor
without requiring enough clearance to rotate at every point in that corridor.
    """
    grid = collision.grid
    actual = tuple(map(float, actual))
    choices=(plan,*alternatives)
    goal_poses=[(*p.goal,p.final_yaw) for p in choices]
    if not collision.motion_free(actual, actual):
        raise NavigationError('Forward driving starts without collision clearance',
                              'navigation_invalid_start')
    if not all(collision.motion_free(p,p) for p in goal_poses):
        raise NavigationError('Selected endpoint lacks clearance at its required final heading')

    def connector(start, xy, final_yaw):
        """Return checked turn/forward/turn poses, or None."""
        return checked_connection(start, xy, final_yaw, collision, check_cancelled)

    def finish(poses, expanded, method):
        chosen=next(p for p in choices if math.dist(p.goal,poses[-1][:2])<1e-6
                    and abs(angle(p.final_yaw-poses[-1][2]))<1e-6)
        return route_from_poses(chosen,poses,collision,expanded=expanded,method=method,
                                check_cancelled=check_cancelled)

    # Replace the stale starting sample with the measured position.
    for candidate in choices:
        poses = [actual]
        for xy in (*candidate.points[1:], candidate.goal):
            if math.dist(poses[-1][:2], xy) < 1e-8:continue
            yaw = math.atan2(xy[1]-poses[-1][1], xy[0]-poses[-1][0])
            segment = connector(poses[-1], xy, yaw)
            if segment is None:
                poses = None
                break
            poses.extend(segment[1:])
        if poses is not None:
            final = connector(poses[-1], candidate.goal, candidate.final_yaw)
            if final is not None:return finish(poses+final[1:], 0, 'certified_polyline')

    # A holonomic shortest guide can choose a corridor whose corner cannot
    # be turned by the loaded robot. Reuse the checked heading roadmap to
    # find an alternate aisle before the continuous search expands that dead
    # end. Preserve the exact visibility-admitted endpoint and final heading.
    # The optional roadmap shares the caller's budget; sampled headings are
    # proposals, and failure still falls back to continuous-pose search.
    if hasattr(collision,'grid_at_heading'):
        from .point_route_projection import project_forward_point
        roadmap_budget=PlanningBudget(check_cancelled,seconds=roadmap_seconds)
        try:
            roadmap=project_forward_point(choices,actual,collision,roadmap_budget.check,exact_endpoints=True)
        except (SearchBudgetExhausted,ImportError):
            roadmap=None
        if roadmap is not None:
            _,poses,_=roadmap
            return finish(poses,0,'checked_roadmap_to_admitted_stance')

    # Bidirectional continuous-pose search. Reverse edges are checked in their
    # original forward direction; reversing the resulting poses restores a
    # forward-only executable path. Neither side is restricted to grid angles.
    class ReverseEdges:
        grid = collision.grid
        def motion_free(self,a,b):
            return collision.motion_free((*b[:2],angle(b[2]-math.pi)),
                                         (*a[:2],angle(a[2]-math.pi)))
        def optimistic_grid(self):
            return collision.optimistic_grid() if hasattr(collision,'optimistic_grid') else None
        def guidance_grid(self):
            return collision.guidance_grid() if hasattr(collision,'guidance_grid') else self.optimistic_grid()
        def guidance_components(self):
            return collision.guidance_components() if hasattr(collision,'guidance_components') else None
        def guidance_distances(self,seeds):
            return collision.guidance_distances(seeds) if hasattr(collision,'guidance_distances') else None
    reverse_plan = replace(plan,points=tuple(reversed((actual[:2],*plan.points[1:],plan.goal))),
                           goal=actual[:2],final_yaw=angle(actual[2]+math.pi))
    reverse_starts=[(*p.goal,angle(p.final_yaw+math.pi)) for p in choices]
    searches = [_continuous_pose_search(plan,actual,collision,check_cancelled,max_expansions,alternatives=alternatives),
                _continuous_pose_search(reverse_plan,reverse_starts[0],ReverseEdges(),check_cancelled,
                                        max_expansions,starts=reverse_starts)]
    indices = [{},{}]
    active = [True,True]
    expanded = 0
    side = 1
    def physical(p,side):
        return (*p[:2],angle(p[2]-math.pi)) if side else p
    def restore(entry,side):
        _,node_id,nodes = entry
        segments = []
        while node_id is not None:
            root_pose,node_id,_,segment = nodes[node_id]
            segments.append(segment)
        poses = [root_pose]
        for segment in reversed(segments):poses.extend(segment)
        return [physical(p,side) for p in reversed(poses)] if side else poses
    while any(active):
        check_cancelled()
        side = 1-side
        if not active[side]:continue
        try:
            node_id,nodes = next(searches[side])
        except StopIteration as stopped:
            active[side] = False
            if stopped.value is not None:
                poses,_ = stopped.value
                if side:poses = [physical(p,side) for p in reversed(poses)]
                return finish(poses,expanded,'continuous_pose_astar')
            continue
        except NavigationError as exc:
            if exc.code == 'navigation_search_exhausted':raise
            active[side] = False
            continue
        if expanded >= max_expansions:
            raise NavigationError('Forward route exhausted its continuous-pose search budget',
                                  'navigation_search_exhausted')
        expanded += 1
        pose = physical(nodes[node_id][0],side)
        entry = (pose,node_id,nodes)
        bucket = (math.floor(pose[0]/.4),math.floor(pose[1]/.4))
        nearby = [other for dx in range(-3,4) for dy in range(-3,4)
                  for other in indices[1-side].get((bucket[0]+dx,bucket[1]+dy),())
                  if math.dist(pose[:2],other[0][:2]) <= 1.2]
        nearby.sort(key=lambda other:math.dist(pose[:2],other[0][:2])+.1*abs(angle(pose[2]-other[0][2])))
        for other in nearby[:4]:
            left,right = (entry,other) if side == 0 else (other,entry)
            link = checked_connection(left[0],right[0][:2],right[0][2],collision,check_cancelled)
            if link is not None:
                poses = restore(left,0)+link[1:]+restore(right,1)[1:]
                return finish(poses,expanded,'continuous_pose_astar')
        indices[side].setdefault(bucket,[]).append(entry)
    raise NavigationError('No collision-free forward route to the selected approach')


def _continuous_pose_search(plan,actual,collision,check_cancelled,max_expansions,*,alternatives=(),starts=None):
    grid = collision.grid
    choices=(plan,*alternatives)
    goal_poses=[(*p.goal,p.final_yaw) for p in choices]
    guides=tuple(dict.fromkeys(xy for p in choices for xy in p.points[1:]))
    def connector(start,xy,final_yaw):
        return checked_connection(start,xy,final_yaw,collision,check_cancelled)
    # Hybrid A*: retain continuous XY/yaw poses. Quantization only limits the
    # closed set; neither the stored trajectory nor an edge is snapped to it.
    # Preferred headings come from the actual goal/path, with relative angular
    # samples to explore alternatives. Every edge has the same swept checks as
    # execution. This is a bounded sampled search, not continuous completeness.
    xy_bin = grid.resolution
    yaw_bins = 48
    def key(p):
        return (round((p[0]-grid.origin[0])/xy_bin),
                round((p[1]-grid.origin[1])/xy_bin),
                round(angle(p[2])*yaw_bins/(2*math.pi)) % yaw_bins)
    def edge_cost(a, b):
        return math.dist(a[:2], b[:2]) + .5*abs(angle(b[2]-a[2]))/(math.pi/3)

    relaxed = (collision.guidance_grid() if hasattr(collision,'guidance_grid') else
               collision.optimistic_grid() if hasattr(collision,'optimistic_grid') else None)
    reverse_queue, reverse_costs, reverse_closed = [], {}, set()
    arrivals=[]
    for goal_pose in goal_poses:
        arrivals.append((goal_pose,goal_pose))
        for distance in (.1, .2, .4, .6, .8, 1.2):
            check_cancelled()
            entry = (goal_pose[0]-distance*math.cos(goal_pose[2]),
                     goal_pose[1]-distance*math.sin(goal_pose[2]), goal_pose[2])
            if not collision.motion_free(entry, goal_pose):break
            arrivals.append((entry,goal_pose))
    components=None
    goal_components=set()
    if relaxed is not None:
        for entry,goal_pose in arrivals:
            row, col = grid.cell(entry[:2])
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    cell = (row+dr, col+dc)
                    if relaxed.navigable(cell):
                        cost = math.dist(grid.world(cell), entry[:2])+math.dist(entry[:2], goal_pose[:2])
                        if cost < reverse_costs.get(cell, math.inf):
                            reverse_costs[cell] = cost
                            heapq.heappush(reverse_queue, (cost, cell))
    field=None
    if relaxed is not None and hasattr(collision,'guidance_distances'):
        try:field=collision.guidance_distances(reverse_costs)
        except ImportError:pass
    if field is None and relaxed is not None and hasattr(collision,'guidance_components'):
        components=collision.guidance_components()
        if components is not None:goal_components={int(components[cell]) for cell in reverse_costs}
    def heuristic(p):
        fallback=min(math.dist(p[:2],goal[:2]) for goal in goal_poses)
        if relaxed is None:
            return fallback
        cell = grid.cell(p[:2])
        if field is not None:
            distance=float(field[cell]) if grid.contains(cell) else math.inf
            return distance if math.isfinite(distance) else fallback
        # A sampled-heading guide can omit a physically legal pose or aisle.
        # Never flood the whole floor trying to settle such an absent cell.
        if (not relaxed.navigable(cell) or
                components is not None and int(components[cell]) not in goal_components):
            return fallback
        work=0
        while reverse_queue and cell not in reverse_closed and work<256:
            distance, current = heapq.heappop(reverse_queue)
            if current in reverse_closed:
                continue
            if len(reverse_closed) % 256 == 0:
                check_cancelled()
            reverse_closed.add(current)
            work+=1
            for neighbor, cost in relaxed.neighbors(current):
                updated = distance+cost
                if updated < reverse_costs.get(neighbor, math.inf):
                    reverse_costs[neighbor] = updated
                    heapq.heappush(reverse_queue, (updated, neighbor))
        # A raster relaxation may omit a continuous entry at a cell boundary.
        # It guides the queue only; it must never reject a collision-free pose.
        return reverse_costs.get(cell, fallback)

    # Immutable node IDs keep parent geometry intact when a cheaper continuous
    # pose supersedes another pose in the same duplicate-detection bucket.
    nodes=[];best={};queue=[]
    for start in starts or (actual,):
        state=key(start)
        if state in best:continue
        node_id=len(nodes)
        nodes.append((start,None,0.,()))
        best[state]=(0.,node_id)
        heapq.heappush(queue,(heuristic(start),0.,node_id))
    expanded = 0
    lengths = tuple(sorted({grid.resolution, 3*grid.resolution, 6*grid.resolution}, reverse=True))
    while queue:
        check_cancelled()
        _, cost, node_id = heapq.heappop(queue)
        current, parent, _, _ = nodes[node_id]
        if best.get(key(current)) != (cost, node_id):
            continue
        if expanded >= max_expansions:
            raise NavigationError('Forward route exhausted its continuous-pose search budget',
                                  'navigation_search_exhausted')
        expanded += 1
        yield node_id,nodes
        # Connect using exact headings, including a straight final aisle that
        # has no turning room. Limit long speculative connections per node.
        for entry,goal_pose in sorted(arrivals, key=lambda pair: math.dist(current[:2], pair[0][:2]))[:max(2,len(choices))]:
            if math.dist(current[:2], entry[:2]) > .65:
                continue
            terminal = connector(current, entry[:2], goal_pose[2])
            if terminal is None:
                continue
            segments = []
            ancestor = node_id
            while ancestor is not None:
                root_pose, ancestor, _, segment = nodes[ancestor]
                segments.append(segment)
            poses = [root_pose]
            for segment in reversed(segments):
                poses.extend(segment)
            return poses+terminal[1:]+[goal_pose], expanded

        def offer(destination, turned=None, checked=False):
            if not grid.navigable(grid.cell(destination[:2])):
                return
            state = key(destination)
            updated = cost+edge_cost(current, destination)
            if updated >= best.get(state, (math.inf, None))[0]-1e-9:
                return
            begin = current if turned is None else turned
            if not checked and not collision.motion_free(begin, destination):
                return
            segment = (destination,) if turned is None else (turned, destination)
            index = len(nodes)
            nodes.append((destination, node_id, updated, segment))
            best[state] = (updated, index)
            # Mild heuristic weighting prioritizes a feasible route within the
            # work budget. No globally shortest path guarantee is advertised.
            heapq.heappush(queue, (updated+1.25*heuristic(destination), updated, index))

        def offer_connection(xy,yaw):
            destination = (*xy,yaw)
            state = key(destination)
            lower = cost+edge_cost(current,destination)
            if lower >= best.get(state,(math.inf,None))[0]-1e-9:
                return
            segment = connector(current,xy,yaw)
            if segment is None:
                return
            updated = cost+sum(edge_cost(a,b) for a,b in zip(segment,segment[1:]))
            if updated >= best.get(state,(math.inf,None))[0]-1e-9:
                return
            index = len(nodes)
            nodes.append((destination,node_id,updated,tuple(segment[1:])))
            best[state] = (updated,index)
            heapq.heappush(queue,(updated+1.25*heuristic(destination),updated,index))

        # Coarse proposals are an acceleration, not an orientation constraint.
        # They are relative to the current REAL heading, including arbitrary
        # non-grid headings admitted by the refined connections below.
        step = grid.resolution/max(abs(math.cos(current[2])),abs(math.sin(current[2])))
        neighbors = [(*current[:2],angle(current[2]+offset)) for offset in (-math.pi/4,math.pi/4)]
        neighbors.append((current[0]+step*math.cos(current[2]),
                          current[1]+step*math.sin(current[2]),current[2]))
        allowed = [collision.motion_free(current,p) for p in neighbors]
        for p,free in zip(neighbors,allowed):
            if free:offer(p,checked=True)
        # Cross open stretches in one swept, forward-only edge. Retain the
        # one-cell edge so a narrow turn or alignment site is not skipped.
        if allowed[2]:
            for distance in (.3,.6,1.2):
                destination=(current[0]+distance*math.cos(current[2]),
                             current[1]+distance*math.sin(current[2]),current[2])
                if not collision.motion_free(current,destination):break
                offer(destination,checked=True)
        if parent is None:
            # These cheap initial suggestions reuse map-aligned kernels, while
            # retaining the measured continuous heading when none fits.
            for yaw in (*[p.final_yaw for p in choices],*(i*math.pi/4 for i in range(8))):
                offer((*current[:2],angle(yaw)))
        if allowed[2] and any(allowed[:2]):
            continue

        # Refine before entering a throat, not only after reaching a dead end.
        # Keep exact guide points and full turn/forward/turn connectors.
        for xy in guides:
            distance = math.dist(current[:2],xy)
            if .002 < distance <= 1.2:
                offer_connection(xy,math.atan2(xy[1]-current[1],xy[0]-current[0]))
        for length in (3,4,6,9,12):
            for side in (-1,1):
                xy=(current[0]+grid.resolution*(length*math.cos(current[2])-side*math.sin(current[2])),
                    current[1]+grid.resolution*(length*math.sin(current[2])+side*math.cos(current[2])))
                if math.dist(current[:2],xy) <= .65:
                    offer_connection(xy,current[2])
        nearby_arrivals=sorted(arrivals,key=lambda pair:math.dist(current[:2],pair[0][:2]))[:2]
        headings = [current[2]]+[math.atan2(p[1]-current[1],p[0]-current[0])
                    for p in (*[g[:2] for g in goal_poses],*[a[0][:2] for a in nearby_arrivals])
                    if math.dist(current[:2],p) > .002]
        headings.extend(current[2]+math.radians(offset)
                        for offset in (-3.75,3.75,-7.5,7.5,-15,15,-30,30))
        seen = set()
        for yaw in headings:
            yaw=angle(yaw)
            heading_key=round(yaw,8)
            if heading_key in seen:continue
            seen.add(heading_key)
            turned=(*current[:2],yaw)
            if abs(angle(yaw-current[2])) > 1e-8 and not collision.motion_free(current,turned):
                continue
            for length in lengths:
                offer((current[0]+length*math.cos(yaw),current[1]+length*math.sin(yaw),yaw),turned)
    raise NavigationError('No collision-free forward route to the selected approach')
