"""Jinkai visual-point GT navigation strategy on an OmniGibson grid substrate.

Candidate sampling and ranking are adapted from habitat-gs jinkai/harness,
commit 0815cf234ee591bacd8017e9b1def4fac13e649b, visual_point.py. See
THIRD_PARTY_NOTICES.md. Habitat's native navmesh/follower is replaced by a
bounded grid pathfinder and feedback follower; this module needs only Python.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math

SOURCE_COMMIT = '0815cf234ee591bacd8017e9b1def4fac13e649b'
STRATEGY = 'jinkai_visual_point_gt_grid_v1'


class NavigationError(Exception):
    def __init__(self, message, code='navigation_unreachable'):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GridMap:
    """Free-cell samples use the same world locations as OG map_to_world.

    Round to the nearest sample consistently, rather than truncating a float
    infinitesimally below an integer into the neighboring obstacle cell.
    """
    width: int
    height: int
    resolution: float
    origin: tuple[float, float]
    free: bytes

    def __post_init__(self):
        if self.width < 1 or self.height < 1 or len(self.free) != self.width*self.height:
            raise ValueError('Grid dimensions do not match occupancy bytes')
        if not math.isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError('Finite positive grid resolution required')
        if len(self.origin) != 2 or not all(math.isfinite(v) for v in self.origin):
            raise ValueError('Finite XY map origin required')

    def cell(self, xy):
        if len(xy) != 2 or not all(math.isfinite(v) for v in xy):
            raise NavigationError('Non-finite navigation position')
        col, row = (math.floor((v-o)/self.resolution + .5) for v,o in zip(xy,self.origin))
        return row, col

    def world(self, cell):
        row,col=cell
        return self.origin[0]+col*self.resolution, self.origin[1]+row*self.resolution

    def contains(self, cell):
        r,c=cell
        return 0 <= r < self.height and 0 <= c < self.width

    def navigable(self, cell):
        return self.contains(cell) and bool(self.free[cell[0]*self.width+cell[1]])

    def snap(self, xy, max_distance, cell_filter=None):
        """Bounded analogue of PathFinder.snap_point, without crossing a floor."""
        row,col=self.cell(xy)
        radius=math.ceil(max_distance/self.resolution)+1
        candidates=[]
        for r in range(max(0,row-radius),min(self.height,row+radius+1)):
            for c in range(max(0,col-radius),min(self.width,col+radius+1)):
                if not self.navigable((r,c)):continue
                distance=math.dist(xy,self.world((r,c)))
                if distance <= max_distance+1e-9:candidates.append((distance,r,c))
        # Rejecting the nearest cell does not reject every other cell inside
        # the same bounded projection neighborhood (e.g. beside a cabinet).
        for _,r,c in sorted(candidates):
            if cell_filter is None or cell_filter((r,c)):return r,c
        return None

    def segment_free(self, a, b):
        """Exact grid supercover, including both sides of edges and corners.

        Check every crossed cell interval rather than distance-spaced samples.
        Planning, pruning and follower subsegments therefore use the same
        collision definition, independent of how a segment is subdivided.
        """
        self.cell(a);self.cell(b)  # Reject non-finite positions before traversal.
        start=tuple((v-o)/self.resolution+.5 for v,o in zip(a,self.origin))
        end=tuple((v-o)/self.resolution+.5 for v,o in zip(b,self.origin))
        times={0.,1.}
        for x,y in zip(start,end):
            if abs(y-x)<1e-14:continue
            for edge in range(math.ceil(min(x,y)),math.floor(max(x,y))+1):
                t=(edge-x)/(y-x)
                if 0.<t<1.:times.add(t)
        times=sorted(times)
        probes=times+[(x+y)/2 for x,y in zip(times,times[1:])]
        for t in probes:
            axes=[]
            for x,y in zip(start,end):
                value=x+(y-x)*t;nearest=round(value)
                axes.append((nearest-1,nearest) if abs(value-nearest)<=1e-9 else (math.floor(value),))
            if any(not self.navigable((row,col)) for col in axes[0] for row in axes[1]):return False
        return True

    def neighbors(self, cell):
        r,c=cell
        for dr,dc in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
            nxt=(r+dr,c+dc)
            if not self.navigable(nxt):continue
            if dr and dc and (not self.navigable((r+dr,c)) or not self.navigable((r,c+dc))):continue
            yield nxt,self.resolution*(math.sqrt(2) if dr and dc else 1)


def candidate_points(hint, current, standoff=.7):
    """Port of jinkai _candidate_points_for_hint, converting XZ to OG XY."""
    points=[tuple(hint)]
    delta=tuple(a-b for a,b in zip(hint,current));norm=math.hypot(*delta)
    if norm>1e-6:points.append(tuple(h-d/norm*standoff for h,d in zip(hint,delta)))
    for radius in (.3,.6,.9,1.2):
        for i in range(12):
            theta=2*math.pi*i/12
            points.append((hint[0]+math.cos(theta)*radius,hint[1]+math.sin(theta)*radius))
    return points


def candidate_score(target_distance, snap_distance, geodesic, standoff=.7, goal_radius=.3):
    """Same point-mode ranking as jinkai: standoff, snapping, no-progress penalty."""
    return round(2*abs(target_distance-standoff)+snap_distance+(1 if geodesic<goal_radius+.15 else 0),4)


def visual_approach_settings(camera_height, base_z, target_z, *, pitch_degrees=20,
                             radius=0., vertical_fov_degrees=90):
    """Navigation standoff and image margin for a selected target point."""
    if target_z-base_z >= .35:
        # The camera rig deliberately keeps the robot below the lower fifth
        # of the image. A point admitted at y=.96 can still be visually tiny
        # or clipped by the robot at the next observation.
        return .7, .18
    # A ground-level handheld object almost vanishes below a 1.83 m head rig
    # at the usual 0.7 m navigation standoff. Keep it above image y=0.82 so
    # the model can select the *body* rather than its clipped tip or the floor.
    pitch=math.radians(pitch_degrees)
    max_downward_angle=pitch+math.atan(2*(.82-.5)*math.tan(math.radians(vertical_fov_degrees)/2))
    height_delta=max(0.,camera_height+base_z-target_z)
    # Projection gives the distance from the forward camera to the target.
    # Candidate positions refer to the base, so include the mount offset.
    # Keep the existing distance policy; the actual frustum/occlusion filter
    # still decides visibility if the preferred distance reaches its 2 m cap.
    standoff=min(2.,max(1.4,radius+height_delta/math.tan(max_downward_angle)+.15))
    return standoff, .18


@dataclass(frozen=True)
class NavigationPlan:
    points: tuple[tuple[float,float], ...]
    goal: tuple[float,float]
    target: tuple[float,float]
    final_yaw: float
    geodesic_m: float
    score: float
    candidates_considered: int
    candidates_reachable: int
    start_grid_offset_m: float
    expanded_cells: int


def plan_navigation(grid, current, hint, *, standoff=.7, max_snap=.75,
                    horizon=10000., max_expansions=250000, candidate_filter=None):
    """Project candidate goals, validate reachability, rank and plan one GT hop.

    One multi-goal Dijkstra search supplies exact grid geodesics for all
    candidates. Occupied/disconnected starts fail; rounding is not permission
    to teleport to another connected region.
    """
    current=tuple(map(float,current));hint=tuple(map(float,hint))
    start=grid.cell(current);grid.cell(hint)
    if not grid.navigable(start):raise NavigationError('Start is outside the traversable grid; no unvalidated recovery teleport','navigation_invalid_start')
    visibility={}
    def acceptable(cell):
        if cell not in visibility:
            visibility[cell]=candidate_filter is None or bool(candidate_filter(grid.world(cell)))
        return visibility[cell]
    def project(reachable=None):
        projected=[]
        for raw in candidate_points(hint,current,standoff):
            cell=grid.snap(raw,max_snap,lambda c:(reachable is None or c in reachable) and acceptable(c))
            if cell is not None:
                goal=grid.world(cell)
                projected.append((cell,math.dist(goal,raw),math.dist(goal,hint)))
        return projected
    candidates=project()
    if not candidates:raise NavigationError('No bounded projection of the selected visual target is navigable')
    pending={c[0] for c in candidates};distances={start:0.};parents={};closed=set();queue=[(0.,start)]
    while queue and pending:
        distance,cell=heapq.heappop(queue)
        if cell in closed:continue
        if distance>horizon:break
        if len(closed)>=max_expansions:raise NavigationError('GT path search exhausted its expansion budget')
        closed.add(cell);pending.discard(cell)
        for neighbor,cost in grid.neighbors(cell):
            updated=distance+cost
            if updated<distances.get(neighbor,math.inf):
                distances[neighbor]=updated;parents[neighbor]=cell;heapq.heappush(queue,(updated,neighbor))
    viable=[]
    for cell,snap_distance,target_distance in candidates:
        if cell not in closed or distances[cell]>horizon:continue
        score=candidate_score(target_distance,snap_distance,distances[cell],standoff)
        viable.append((score,distances[cell],cell))
    if not viable:
        # If the nearest projections fell on another connected region, the
        # exhausted search already tells us which cells are actually reachable.
        # Reproject within the same distance bound; never cross walls or move
        # the start pose to a different component.
        candidates=project(closed)
        for cell,snap_distance,target_distance in candidates:
            viable.append((candidate_score(target_distance,snap_distance,distances[cell],standoff),distances[cell],cell))
    if not viable:raise NavigationError('No candidate near the visual target is reachable from this start')
    score,distance,goal_cell=min(viable)
    cells=[goal_cell]
    while cells[-1]!=start:cells.append(parents[cells[-1]])
    cells.reverse()
    points=[current,*[grid.world(c) for c in cells]]
    # Greedy line-of-sight pruning preserves all intervening occupied cells;
    # it avoids the old fixed-stride waypoint sampling across obstacle corners.
    simplified=[points[0]];index=0
    while index<len(points)-1:
        far=index+1
        for end in range(index+2,len(points)):
            if not grid.segment_free(points[index],points[end]):break
            far=end
        if math.dist(simplified[-1],points[far])>1e-9:simplified.append(points[far])
        index=far
    goal=grid.world(goal_cell)
    if not all(grid.segment_free(a,b) for a,b in zip(simplified,simplified[1:])):
        raise NavigationError('Planned segment failed static collision validation')
    return NavigationPlan(tuple(simplified),goal,hint,math.atan2(hint[1]-goal[1],hint[0]-goal[0]),
                          distance,score,len(candidates),len(viable),math.dist(current,grid.world(start)),len(closed))


class GreedyGridFollower:
    """Feedback turn/advance follower replacing Habitat's native follower.

    The adapter supplies the actual pose after every simulation step. Commands
    are bounded; stalled or displaced robots fail explicitly instead of
    consuming repeated model calls. GT geometry stays private to the executor.
    """
    def __init__(self, grid, plan, dt, speed=.5, turn_speed=math.pi/3, stall_steps=20):
        if min(dt,speed,turn_speed)<=0 or not all(math.isfinite(x) for x in (dt,speed,turn_speed)):
            raise ValueError('Positive finite follower rates required')
        self.grid,self.plan=grid,plan
        self.distance_step,self.angle_step=speed*dt,turn_speed*dt
        self.index=1;self.last_actual=None;self.last_command=None;self.stall=0;self.stall_steps=stall_steps
        self.position_tolerance=.002
        # Recorded OG pose feedback has ~1e-4 rad yaw residual after a setter.
        # Demanding a smaller error before any translation causes endless tiny
        # turns. Translation still follows the exact checked path segment and
        # the command's yaw change remains bounded by angle_step.
        self.heading_tolerance=min(math.radians(.5),self.angle_step)
        self.final_yaw_tolerance=1e-3
        self.distance_progress=min(self.distance_step*.25,.002)
        self.angle_progress=min(self.angle_step*.25,1e-3)
        self.last_phase=None;self.last_index=None
        self.advance_index=None;self.advance_best=None;self.advance_stall=0
        self.turn_target=None;self.turn_best=None;self.turn_stall=0
        self.turn_progress=0.

    @staticmethod
    def angle(delta):return math.atan2(math.sin(delta),math.cos(delta))

    def _remaining_on_segment(self, xy, index):
        """Directed route progress, rather than arbitrary pose displacement."""
        start,end=self.plan.points[index-1:index+1]
        length=math.dist(start,end)
        if length<=1e-12:return 0.
        return length-sum((v-s)*(e-s)/length for v,s,e in zip(xy,start,end))

    def _check_progress(self, actual):
        if self.last_phase=='advance':
            remaining=self._remaining_on_segment(actual[:2],self.last_index)
            if self.advance_best-remaining>=self.distance_progress:
                self.advance_best=remaining;self.advance_stall=0
            else:self.advance_stall+=1
            self.stall=self.advance_stall
        else:
            # Measure the turn that was actually commanded. A waypoint's
            # bearing can change as XY feedback drifts; an old frozen bearing
            # is no longer the target of subsequent commands.
            requested=abs(self.angle(self.last_command[2]-self.last_actual[2]))
            remaining=abs(self.angle(self.last_command[2]-actual[2]))
            self.turn_progress+=requested-remaining
            if self.turn_progress>=self.angle_progress:
                self.turn_progress=0.;self.turn_stall=0
            else:self.turn_stall+=1
            self.stall=self.turn_stall
        if self.stall>=self.stall_steps:
            raise NavigationError('Navigation follower made no progress toward its path or heading','navigation_stalled')

    def next_pose(self, actual):
        x,y,yaw=map(float,actual)
        if not all(math.isfinite(v) for v in (x,y,yaw)):raise NavigationError('Non-finite actual robot pose')
        if not self.grid.navigable(self.grid.cell((x,y))):raise NavigationError('Robot left the traversable grid during execution','navigation_invalid_start')
        if self.last_actual is not None:
            error=math.dist((x,y),self.last_command[:2])
            if error>max(.10,self.distance_step*3):raise NavigationError('Robot diverged from the commanded path')
            self._check_progress((x,y,yaw))
        while self.index<len(self.plan.points) and math.dist((x,y),self.plan.points[self.index])<=self.position_tolerance:
            self.index+=1
        if self.index>=len(self.plan.points):
            distance=math.dist((x,y),self.plan.goal)
            delta=self.angle(self.plan.final_yaw-yaw)
            if distance<=self.position_tolerance and abs(delta)<=self.final_yaw_tolerance:return None
            phase='turn';heading=self.plan.final_yaw
            # Hold the fixed endpoint during final orientation. Reusing actual
            # XY accumulates a ~61um setter residual every turn on archived055.
            amount=min(distance,self.distance_step)
            xy=tuple(a+(g-a)*amount/distance for a,g in zip((x,y),self.plan.goal)) if distance else (x,y)
            command=(*xy,yaw+max(-self.angle_step,min(self.angle_step,delta)))
        else:
            goal=self.plan.points[self.index];distance=math.dist((x,y),goal)
            heading=math.atan2(goal[1]-y,goal[0]-x);delta=self.angle(heading-yaw)
            if abs(delta)>self.heading_tolerance and distance>self.distance_step:
                phase='turn'
                command=(x,y,yaw+max(-self.angle_step,min(self.angle_step,delta)))
            else:
                phase='advance'
                amount=min(distance,self.distance_step)
                # At sub-step range, converge XY while bounding yaw as usual.
                # Turning toward a waypoint only2–4mm away otherwise chases
                # bearing changes from tiny simulator position residuals.
                command=(x+(goal[0]-x)*amount/distance,y+(goal[1]-y)*amount/distance,
                         yaw+max(-self.angle_step,min(self.angle_step,delta)))
        if math.dist((x,y),command[:2])>1e-12 and not self.grid.segment_free((x,y),command[:2]):
            raise NavigationError('Next movement crosses an occupied grid cell')
        if phase=='advance' and self.advance_index!=self.index:
            self.advance_index=self.index;self.advance_best=self._remaining_on_segment((x,y),self.index)
            self.advance_stall=0
        if phase=='turn' and (self.last_phase!='turn' or self.last_index!=self.index):
            self.turn_target=heading;self.turn_best=abs(self.angle(heading-yaw));self.turn_stall=0;self.turn_progress=0.
        self.last_phase=phase;self.last_index=self.index
        self.last_actual=(x,y,yaw);self.last_command=command
        return command
