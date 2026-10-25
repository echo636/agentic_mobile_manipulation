"""Transactional door-joint preview; no env.step, metrics or video frames."""
import copy
import math
import random
import numpy as np

from ..contracts import SkillError


def swept_link_bounds(before, after):
    """Conservative short-interval bounds, including angular interpolation."""
    result=[]
    for key,points in before.items():
        other=after.get(key)
        if other is None or points.shape!=other.shape:
            raise SkillError('target_changed','Articulation collision geometry changed')
        displacement=float(np.linalg.norm(other-points,axis=1).max())
        if displacement<1e-5:continue
        # Maximum endpoint displacement reserves the unsampled curved motion;
        # finely spaced joint samples keep this conservative reserve small.
        result.append((np.minimum(points.min(axis=0),other.min(axis=0))-displacement,
                       np.maximum(points.max(axis=0),other.max(axis=0))+displacement))
    return result


class ArticulationSweep:
    def __init__(self, backend, boxes, collision):
        self.boxes=boxes;self.collision=collision;self.check_cancelled=lambda:None
        self.origin=backend._local_pose();self.bodies=[]
        held=backend._get_held()
        payload=[] if held is None else [held,*[o for o,_ in backend._carry_contents]]
        for obj in [backend.robot,*payload]:
            for link in obj.links.values():
                if link.is_meta_link:continue
                points=link.collision_boundary_points_world
                if points is not None:self.bodies.append(points.cpu().numpy())

    def motion_free(self,start,end):
        self.check_cancelled()
        if start!=end:raise ValueError('Door sweep checks an anchored stance only')
        self.collision.check_cancelled=self.check_cancelled
        if not self.collision.motion_free(start,end):return False
        yaw=start[2]-self.origin[2];c,s=math.cos(yaw),math.sin(yaw)
        for points in self.bodies:
            self.check_cancelled()
            moved=points.copy()
            moved[:,:2]=(moved[:,:2]-self.origin[:2])@np.array([[c,s],[-s,c]])+start[:2]
            lo,hi=moved.min(axis=0)-.002,moved.max(axis=0)+.002
            for a,b in self.boxes:
                if np.all(hi>a) and np.all(lo<b):return False
        return True


def prepare_articulation(backend,obj,primitive,operation):
    from omnigibson.object_states import Open
    from .particle_payload import snapshot_particle_state,restore_particle_state,particle_state_unchanged
    sim=backend.og.sim
    operation.check()
    # Native rigid-body velocity snapshots borrow the physics tensor buffers.
    # Freeze them before any hypothetical setter / keep_still call can mutate
    # those buffers; copying only at restore time would preserve the trial.
    state=copy.deepcopy(sim.dump_state(serialized=False))
    particles=snapshot_particle_state(sim)
    from .placement import scene_object_identities
    identities=scene_object_identities(sim)
    def restore():
        if scene_object_identities(sim)!=identities or not particle_state_unchanged(particles):
            raise SkillError('target_changed','Scene identities changed during articulation preview',changed=True)
        sim.load_state(copy.deepcopy(state),serialized=False)
        restore_particle_state(particles)
    rng=random.getstate();numpy_rng=np.random.get_state();torch_rng=backend.torch.random.get_rng_state()
    start=obj.get_joint_positions().clone();boxes=[]
    current_geometry=getattr(operation,'geometry',None) or backend._local_geometry(operation)
    # Hypothetical joint poses must not publish occupancy/signature entries to
    # the live cache. In particular, the later real operation can use the same
    # joint goal, which would otherwise reuse a pre-physics preview raster.
    missing=object()
    live_cache=getattr(backend,'_live_gt_collision_cache',missing)
    names=('_navigation_layers','_navigation_support','_navigation_clearance','_navigation_layer_yaw','_navigation_layer_heights')
    saved={name:getattr(backend,name,None) for name in names}
    payload=backend._container_payload(obj)
    def vertices():
        return {str(link.prim_path):link.collision_boundary_points_world.cpu().numpy().copy()
                for item in [obj,*[record[0] for record in payload]] for link in item.links.values()
                if not link.is_meta_link and link.collision_boundary_points_world is not None}
    try:
        if live_cache is not missing:
            backend._live_gt_collision_cache=copy.deepcopy(live_cache)
        if primitive=='open':
            from .articulation_goal import checked_open_goal
            goal=checked_open_goal(backend,obj,operation.check)
        else:
            if not obj.states[Open].set_value(False,fully=True):
                raise SkillError('local_adjustment_blocked','No stable articulated target state')
            goal=obj.get_joint_positions().clone()
        count=max(1,math.ceil(float(abs(goal-start).max())/.025))
        if count>160:raise SkillError('local_adjustment_exhausted','Articulation preview exceeds bounded joint sweep')
        restore()
        previous=vertices();initial_vertices=previous
        with backend._editable_carry_joints(obj):
            for i in range(1,count+1):
                operation.check()
                # OG joint setters synchronize PhysX to Fabric immediately.
                # No physics step is needed: neighbouring props and the robot
                # must not be pushed while evaluating hypothetical door poses.
                obj.set_joint_positions(start+(goal-start)*i/count)
                backend._relocate_container_payload(payload)
                backend._carry_follow()
                current=vertices();boxes.extend(swept_link_bounds(previous,current));previous=current
            moved_links={key:points for key,points in previous.items()
                         if key not in initial_vertices or not np.allclose(points,initial_vertices[key],atol=1e-5,rtol=0.)}
            future_geometry=backend._local_geometry(operation,occupancy_overrides=moved_links)
            plan={'primitive':primitive,'object_prim':str(obj.prim_path),
                  'start_joints':start.cpu().tolist(),'goal_joints':goal.cpu().tolist(),'samples':count}
            restore()
    finally:
        try:
            restore()
        finally:
            random.setstate(rng);np.random.set_state(numpy_rng);backend.torch.random.set_rng_state(torch_rng)
            # Restore even on cancellation, without invoking an expired budget.
            # Checkers own their occupancy arrays; the real operation must start
            # from the cache of the actual pre-operation scene.
            if live_cache is missing:
                backend.__dict__.pop('_live_gt_collision_cache',None)
            else:
                backend._live_gt_collision_cache=live_cache
            for name,value in saved.items():setattr(backend,name,value)
            backend.frames_revision=-1

    operation.record('articulation_preview',samples=count,swept_link_intervals=len(boxes),
                     object_prim=str(obj.prim_path),hypothetical_state_restored=True)
    return ArticulationSweep(backend,boxes,current_geometry[1]),future_geometry[1],plan
