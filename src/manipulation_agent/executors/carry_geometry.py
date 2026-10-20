"""Read-only whole-payload translation checks, shared by prediction and motion.

PhysX queries stay in the adapter. No hypothetical object or robot setters are
used. Existing support contacts may only shrink during vertical extraction;
new contacts are forbidden and the end of extraction must be clear.
"""
import math
import numpy as np

from .carry_posture import compact_translation, translation_samples


def translated_bounds(bounds, delta):
    delta=np.asarray(delta)
    return [(lo+delta,hi+delta) for lo,hi in bounds]


def overlap_volume(a, b):
    return float(np.prod(np.maximum(0.,np.minimum(a[1],b[1])-np.maximum(a[0],b[0]))))


class PayloadSweep:
    def __init__(self, backend, obj, payload, pose, *, check=lambda: None):
        import omnigibson.lazy as lazy
        self.check=check;self.backend=backend;self.obj=obj
        self.origin=np.asarray(obj.get_position_orientation()[0].cpu(),dtype=float)
        self.bounds=[]
        for item in payload:
            for link in item.links.values():
                if link.is_meta_link:continue
                points=link.collision_boundary_points_world
                if points is None:raise ValueError('Payload collision geometry unavailable')
                points=points.cpu().numpy()
                if not len(points) or not np.isfinite(points).all():
                    raise ValueError('Payload collision geometry invalid')
                self.bounds.append((points.min(axis=0),points.max(axis=0)))
        if not self.bounds:raise ValueError('Empty payload collision geometry')
        actual=backend._local_pose()
        delta=pose[2]-actual[2];c,s=math.cos(delta),math.sin(delta)
        self.robot_bounds=[]
        for link in backend.robot.links.values():
            points=link.collision_boundary_points_world
            if link.is_meta_link or points is None:continue
            points=points.cpu().numpy().copy()
            points[:,:2]=(points[:,:2]-actual[:2])@np.array([[c,s],[-s,c]])+pose[:2]
            self.robot_bounds.append((points.min(axis=0),points.max(axis=0)))
        self.ignored={str(link.prim_path) for item in [backend.robot,*payload] for link in item.links.values()}
        self.links={str(link.prim_path):link for item in backend.env.scene.objects for link in item.links.values()}
        self.ignored.update(path for path,link in self.links.items() if link.is_meta_link)
        self.query=lazy.omni.physx.get_physx_scene_query_interface()
        self.last_failure=None;self.queries=0

    def contacts(self, bounds):
        result={}
        for lo,hi in bounds:
            self.check();self.queries+=1
            # Small numeric reserve; Z comes from the actual payload collider,
            # not from a broad robot-height slab containing the table below.
            low=lo-.002;high=hi+.002
            error=None
            def hit(hit):
                nonlocal error
                path=str(hit.rigid_body)
                if path in self.ignored:return True
                try:
                    link=self.links.get(path)
                    if link is None:
                        result[path]=math.inf
                    else:
                        a,b=link.aabb
                        box=(a.cpu().numpy(),b.cpu().numpy())
                        result[path]=result.get(path,0.)+max(1e-12,overlap_volume((low,high),box))
                except Exception as exc:error=exc;return False
                return True
            self.query.overlap_box(tuple((high-low)/2),tuple((high+low)/2),(0.,0.,0.,1.),hit,False)
            if error is not None:raise RuntimeError('Payload sweep collision query failed') from error
            for i,box in enumerate(self.robot_bounds):
                volume=overlap_volume((low,high),box)
                if volume>1e-12:result['robot_link:'+str(i)]=result.get('robot_link:'+str(i),0.)+volume
        return result

    def clear(self, a, b, *, extraction=False, require_clear=True):
        a=np.asarray(a);b=np.asarray(b)
        initial=self.contacts(translated_bounds(self.bounds,a-self.origin)) if extraction else {}
        previous=a
        for point in translation_samples((a,b),.025):
            point=np.asarray(point)
            swept=[(np.minimum(lo+previous-self.origin,lo+point-self.origin),
                    np.maximum(hi+previous-self.origin,hi+point-self.origin)) for lo,hi in self.bounds]
            contacts=self.contacts(swept)
            if any(key not in initial or not math.isfinite(value) or value>initial[key]+1e-9
                   for key,value in contacts.items()):
                self.last_failure={'stage':'translation_sweep','blockers':sorted(contacts),'extraction':extraction}
                return False
            if extraction:
                # The next segment cannot re-acquire a contact already shed.
                initial=self.contacts(translated_bounds(self.bounds,point-self.origin))
            previous=point
        remaining=self.contacts(translated_bounds(self.bounds,b-self.origin))
        if remaining and require_clear:
            self.last_failure={'stage':'translation_endpoint','blockers':sorted(remaining),'extraction':extraction}
            return False
        return True


def plan_lift_and_compact(origin, base, bounds, clear, *, check=lambda: None):
    """One route definition for both preview and actual held-object movement."""
    origin=tuple(map(float,origin));lifted=(*origin[:2],origin[2]+.18)
    check()
    if not clear(origin,lifted,extraction=True):return None,'lift_blocked'
    lifted_bounds=translated_bounds(bounds,np.asarray(lifted)-origin)
    route,status=compact_translation(lifted,base,lifted_bounds,clear,check=check)
    # A large or blocked packing motion can still leave a valid lifted pose.
    # Its departure must be checked separately before this route is admitted.
    result=(origin,lifted,*tuple(route[1:] if route else ()))
    return result,'compact' if route else 'lift_only_'+status


class LoadedCollision:
    """Combine an unloaded robot grid with a predicted payload's 3-D sweeps."""
    def __init__(self, robot_collision, sweep, pose, route):
        self.robot_collision=robot_collision;self.sweep=sweep
        self.pose=tuple(pose);self.route=tuple(tuple(map(float,p)) for p in route)
        self.grid=robot_collision.grid;self.check_cancelled=lambda:None

    def motion_free(self, start, end):
        self.check_cancelled()
        if not self.robot_collision.motion_free(start,end):return False
        delta=math.atan2(math.sin(end[2]-start[2]),math.cos(end[2]-start[2]))
        count=max(1,math.ceil(math.dist(start[:2],end[:2])/.025),math.ceil(abs(delta)/math.radians(3)))
        # An AABB around each transformed source box conservatively covers its
        # vertices; extra angular sagitta reserves cover the continuous sweep.
        final=np.asarray(self.route[-1])-self.sweep.origin
        corners=[np.array([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1])
                          for z in (lo[2],hi[2])])+final for lo,hi in self.sweep.bounds]
        def boxes(pose):
            yaw=pose[2]-self.pose[2];c,s=math.cos(yaw),math.sin(yaw)
            result=[]
            for points in corners:
                moved=points.copy()
                moved[:,:2]=(moved[:,:2]-self.pose[:2])@np.array([[c,s],[-s,c]])+pose[:2]
                result.append((moved.min(axis=0),moved.max(axis=0)))
            return result
        previous=boxes(start)
        radius=max(float(np.linalg.norm(points[:,:2]-self.pose[:2],axis=1).max()) for points in corners)
        reserve=radius*(1-math.cos(abs(delta)/count/2))
        old_robot=self.sweep.robot_bounds
        # Payload/robot relative pose is invariant during departure and was
        # checked during the carry route. The static world query ignores both.
        self.sweep.robot_bounds=[]
        try:
            for i in range(1,count+1):
                self.check_cancelled()
                pose=(*(a+(b-a)*i/count for a,b in zip(start[:2],end[:2])),start[2]+delta*i/count)
                current=boxes(pose)
                swept=[(np.minimum(a,c)-np.array([reserve,reserve,0.]),
                        np.maximum(b,d)+np.array([reserve,reserve,0.])) for (a,b),(c,d) in zip(previous,current)]
                contacts=self.sweep.contacts(swept)
                if contacts:
                    self.sweep.last_failure={'stage':'loaded_departure','blockers':sorted(contacts)}
                    return False
                previous=current
            return True
        finally:self.sweep.robot_bounds=old_robot
