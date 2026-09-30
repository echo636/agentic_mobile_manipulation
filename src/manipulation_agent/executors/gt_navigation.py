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
    pass


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

    def snap(self, xy, max_distance):
        """Bounded analogue of PathFinder.snap_point, without crossing a floor."""
        row,col=self.cell(xy)
        radius=math.ceil(max_distance/self.resolution)+1
        best=None
        for r in range(max(0,row-radius),min(self.height,row+radius+1)):
            for c in range(max(0,col-radius),min(self.width,col+radius+1)):
                if not self.navigable((r,c)):continue
                distance=math.dist(xy,self.world((r,c)))
                key=(distance,r,c)
                if distance <= max_distance+1e-9 and (best is None or key<best):best=key
        return None if best is None else (best[1],best[2])

    def segment_free(self, a, b):
        """Check dense samples and diagonal corner crossings on the static grid."""
        count=max(1,math.ceil(math.dist(a,b)/(self.resolution/4)))
        previous=None
        for i in range(count+1):
            cell=self.cell(tuple(x+(y-x)*i/count for x,y in zip(a,b)))
            if not self.navigable(cell):return False
            if previous and cell[0]!=previous[0] and cell[1]!=previous[1]:
                if not self.navigable((previous[0],cell[1])) or not self.navigable((cell[0],previous[1])):return False
            previous=cell
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
                    horizon=10000., max_expansions=250000):
    """Project candidate goals, validate reachability, rank and plan one GT hop.

    One multi-goal Dijkstra search supplies exact grid geodesics for all
    candidates. Occupied/disconnected starts fail; rounding is not permission
    to teleport to another connected region.
    """
    current=tuple(map(float,current));hint=tuple(map(float,hint))
    start=grid.cell(current);grid.cell(hint)
    if not grid.navigable(start):raise NavigationError('Start is outside the traversable grid; no unvalidated recovery teleport')
    candidates=[]
    for raw in candidate_points(hint,current,standoff):
        cell=grid.snap(raw,max_snap)
        if cell is not None:
            goal=grid.world(cell)
            candidates.append((cell,math.dist(goal,raw),math.dist(goal,hint)))
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

    @staticmethod
    def angle(delta):return math.atan2(math.sin(delta),math.cos(delta))

    def next_pose(self, actual):
        x,y,yaw=map(float,actual)
        if not all(math.isfinite(v) for v in (x,y,yaw)):raise NavigationError('Non-finite actual robot pose')
        if not self.grid.navigable(self.grid.cell((x,y))):raise NavigationError('Robot left the traversable grid during execution')
        if self.last_actual is not None:
            error=math.dist((x,y),self.last_command[:2])
            if error>max(.10,self.distance_step*3):raise NavigationError('Robot diverged from the commanded path')
            progressed=math.dist((x,y),self.last_actual[:2])>1e-5 or abs(self.angle(yaw-self.last_actual[2]))>1e-5
            self.stall=0 if progressed else self.stall+1
            if self.stall>=self.stall_steps:raise NavigationError('Navigation follower made no progress')
        while self.index<len(self.plan.points) and math.dist((x,y),self.plan.points[self.index])<=self.position_tolerance:
            self.index+=1
        if self.index>=len(self.plan.points):
            if math.dist((x,y),self.plan.goal)>self.position_tolerance:
                raise NavigationError('Path ended before the selected goal was reached')
            delta=self.angle(self.plan.final_yaw-yaw)
            if abs(delta)<=1e-4:return None
            command=(x,y,yaw+max(-self.angle_step,min(self.angle_step,delta)))
        else:
            goal=self.plan.points[self.index];distance=math.dist((x,y),goal)
            heading=math.atan2(goal[1]-y,goal[0]-x);delta=self.angle(heading-yaw)
            if abs(delta)>1e-4:
                command=(x,y,yaw+max(-self.angle_step,min(self.angle_step,delta)))
            else:
                amount=min(distance,self.distance_step)
                command=(x+(goal[0]-x)*amount/distance,y+(goal[1]-y)*amount/distance,heading)
                if not self.grid.segment_free((x,y),command[:2]):raise NavigationError('Next movement crosses an occupied grid cell')
        self.last_actual=(x,y,yaw);self.last_command=command
        return command
