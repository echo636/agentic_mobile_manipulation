"""Explicit ideal carry without a dynamic robot/object fixed joint.

Object identity comes from the selected RGB pixel only. This is a research
motor protocol, not physical grasp control. Ordinary environment collisions
and gravity remain enabled; held poses are projected around each control step.
"""
from contextlib import contextmanager, nullcontext
import json
import math
from ..contracts import SkillError
from ..records import now
from .particle_payload import snapshot_particles, relocate_particles, verify_particles, particle_count
from .carry_posture import CompactCarry


def support_closure(root, objects, relation):
    """Collect only the root's transitive rigid payload, with no task lookup."""
    seen={id(root)};queue=[root];edges=[]
    for parent in queue:
        for child in objects:
            if id(child) in seen:continue
            kind=relation(child,parent)
            if kind:
                seen.add(id(child));queue.append(child);edges.append((child,parent,kind))
    return edges


def attachment_relation(candidate, member):
    """Traverse an attachment in either direction without changing its joint.

    Payload edges point away from the selected grasp, which need not be the
    native attachment root. Preserve the direction for later state checks.
    """
    from omnigibson.object_states import AttachedTo
    state=getattr(candidate,'states',{}).get(AttachedTo)
    if state is not None and state.get_value(member):
        kind='AttachedTo'
    else:
        state=getattr(member,'states',{}).get(AttachedTo)
        kind='AttachedToParent' if state is not None and state.get_value(candidate) else None
    if kind and getattr(candidate,'fixed_base',True):
        raise SkillError('fixed_object','The selected assembly is attached to a fixed object')
    return kind


class ControlledCarry(CompactCarry):
    @contextmanager
    def _settling_container_contents(self):
        """Let resident rigid contents settle, then retain their actual new poses.

        Navigation stabilization must not pin a tilted tool forever during a
        wait or lid operation. Keep container roots supported, release only
        resident pose stabilization, and recapture native containment after
        physics (or after an enclosing operation has rolled back). Escaped or
        removed objects are never restored to their previous locations.
        """
        pins = getattr(self, '_stabilized_container_payloads', {})
        containers = tuple(pins)
        for container in containers:
            pins.pop(container)
        try:
            yield
        finally:
            if containers:
                live = {id(obj) for obj in self.env.scene.objects}
                held = self._get_held()
                for container in containers:
                    if id(container) not in live:
                        getattr(self, '_stabilized_containers', {}).pop(container, None)
                    elif container is not held:
                        pins[container] = self._container_payload(container)

    def _restore_carry_joints(self):
        # Pose carry represents the selected assembly as a controlled body.
        # Preserve the captured articulation too; root projection alone can
        # kick a passive lid open during a turn. Never infer a desired state.
        for obj,joints in getattr(self,'_carry_joint_positions',{}).items():
            obj.set_joint_positions(joints);obj.keep_still()

    @contextmanager
    def _editable_carry_joints(self,obj):
        positions=getattr(self,'_carry_joint_positions',{})
        previous=positions.pop(obj,None)
        try:yield
        except BaseException:
            if previous is not None:positions[obj]=previous
            raise
        else:
            if previous is not None:positions[obj]=obj.get_joint_positions().clone()

    @contextmanager
    def _anchored_operation(self, obj=None):
        previous=self._base_target;old_object=getattr(self,'_object_anchor',None)
        pos,quat=self.robot.get_position_orientation();joints=self.robot.get_joint_positions()
        indices=self.torch.tensor([i for i in range(len(joints)) if i not in self.robot.base_idx.tolist()],device=joints.device)
        self._base_target={'position':pos.clone(),'orientation':quat.clone(),'indices':indices,
                           'posture':joints[indices].clone(),'held':None,'relative':None}
        if obj is not None:self._object_anchor=(obj,tuple(v.clone() for v in obj.get_position_orientation()))
        original_step=self.og.sim.step_physics
        def anchored_physics(*args,**kwargs):
            local=getattr(self,'_local_operation',None)
            if local is not None:local.check()
            self._restore_base_target();self._restore_object_anchor();self._follow_placement_particles()
            try:
                result=original_step(*args,**kwargs)
                # step_physics advances PhysX without refreshing Fabric mesh
                # transforms. Object poses/velocities are current, but AABBs,
                # support anchors and fillable geometry can still describe
                # the preceding pose. Sampling must read one physical state.
                sync=getattr(self.og.sim,'sync_physx_to_fabric',None)
                if sync is not None:sync()
                return result
            finally:self._restore_base_target();self._restore_object_anchor();self._follow_placement_particles()
        self.og.sim.step_physics=anchored_physics
        try:yield
        finally:
            self.og.sim.step_physics=original_step
            try:self._restore_base_target();self._restore_object_anchor();self._follow_placement_particles()
            finally:self._base_target=previous;self._object_anchor=old_object

    def _restore_object_anchor(self):
        if getattr(self,'_object_anchor',None) is not None:
            obj,pose=self._object_anchor
            if not any(current is obj for current in self.env.scene.objects):
                self._object_anchor=None
                self.frames_revision=-1
                return
            obj.set_position_orientation(*pose);obj.keep_still()

    def _ideal_state_action(self, primitive, obj, max_steps):
        from omnigibson.object_states import Open,ToggledOn
        from .placement import placement_transaction
        state=Open if primitive in {'open','close'} else ToggledOn
        wanted=primitive in {'open','toggle_on'}
        if state not in obj.states:raise SkillError('pre_condition_error','Selected object does not support this operation')
        # Open.set_value teleports articulated joints. Drawer contents must move
        # with their supporting volume, not be left at the previous world pose.
        payload=self._container_payload(obj) if state is Open else []
        particles=snapshot_particles(self.env.scene,[obj]) if state is Open else []
        edit=self._editable_carry_joints(obj) if state is Open else nullcontext()
        settling=self._settling_container_contents() if state is Open else nullcontext()
        prepared=getattr(getattr(self,'_local_operation',None),'articulation_plan',None) if state is Open else None
        open_goal=None
        if primitive=='open' and prepared is None:
            from .articulation_goal import checked_open_goal
            open_goal=checked_open_goal(self,obj,self.deadline.check)
        samples=[]
        def capture(phase):
            if state is Open:
                from .articulation_diagnostics import articulation_sample
                samples.append(articulation_sample(self,obj,phase))
        def report(status,**details):
            if state is Open:
                from .articulation_diagnostics import record_articulation
                record_articulation(self,obj,primitive,samples,status=status,**details)
        with edit,settling,self._anchored_operation(obj),placement_transaction(self.og.sim,self._placement_record):
            capture('before_setter')
            # Upstream symbolic OPEN samples a random opening and returns early
            # when already ajar. Use a checked usable opening; close fully.
            if prepared is not None:
                current=obj.get_joint_positions()
                start=self.torch.as_tensor(prepared['start_joints'],dtype=current.dtype,device=current.device)
                if str(obj.prim_path)!=prepared['object_prim'] or prepared['primitive']!=primitive or not self.torch.allclose(current,start,atol=.02,rtol=0.):
                    raise SkillError('target_changed','Articulation changed after safe-stance preparation')
                obj.set_joint_positions(self.torch.as_tensor(prepared['goal_joints'],dtype=current.dtype,device=current.device))
                obj.states[state].clear_cache();obj.state_updated()
                accepted=bool(obj.states[state].get_value())==wanted
            elif open_goal is not None:
                obj.set_joint_positions(open_goal);obj.keep_still()
                obj.states[state].clear_cache();obj.state_updated()
                accepted=bool(obj.states[state].get_value())
            else:
                accepted=obj.states[state].set_value(wanted,**({'fully':True} if state is Open else {}))
            if not accepted:raise SkillError('execution_error','Object state setter rejected the selected operation',changed=True)
            self._relocate_container_payload(payload)
            relocate_particles(self.env.scene,particles)
            capture('after_setter')
            for step in range(min(30,max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                if step in (0,4,14,29):capture('settle_'+str(step+1))
            if bool(obj.states[state].get_value())!=wanted:
                report('failed',reason='state_reopened_or_reclosed_after_settling',
                       payload=[child.name for child,_,_ in payload])
                raise SkillError('postcondition_error','Requested object operation did not remain stable',changed=True)
            self._verify_container_payload(obj,payload)
            verify_particles(self.env.scene,particles)
            report('passed',payload=[child.name for child,_,_ in payload])
        return {'primitive':primitive,'implementation':'transactional_official_state_setter_with_link_payload',
                'fully_open_or_closed':primitive=='close','opening_policy':'largest_checked_usable_endpoint' if primitive=='open' else None,
                'postcondition':'official_state_and_containment_after_settling',
                'preserved_contained_objects':len(payload),'failure_policy':'restore_pre_action_state'}

    def _checked_attach(self, parent, max_steps):
        """Attach the carried child to the visually selected compatible parent."""
        from omnigibson.object_states import AttachedTo
        if not self.ideal_carry:
            raise SkillError('pre_condition_error','Attach requires controlled carry mode')
        child=self._get_held()
        if child is None:
            raise SkillError('empty_hand','No carried object')
        if child is parent or AttachedTo not in child.states or AttachedTo not in parent.states:
            raise SkillError('unsupported_relation','Selected objects do not support attachment')
        state=child.states[AttachedTo]
        if state.parent is not None and state.parent is not parent:
            raise SkillError('pre_condition_error','Carried object is attached to another parent')
        if state.parent is not parent:
            child_link, parent_link = state._find_attachment_links(parent, bypass_alignment_checking=True)
            if child_link is None or parent_link is None:
                raise SkillError('unsupported_relation','Selected parent has no available compatible attachment link')
        contents=list(self._carry_contents)
        dependencies=list(self._carry_dependencies)
        try:
            # The new fixed joint must move the connected bodies together.
            # Repeatedly projecting only the parent's pose fights the native
            # constraint solver and can inject energy into the whole assembly.
            # Keep the robot anchor, rollback and parent identity checks while
            # allowing the attached objects to respond to contacts and gravity.
            with self._placement_context(watch=(parent,)):
                self._carry_detach()
                accepted=state.set_value(parent,True,bypass_alignment_checking=True,
                                         check_physics_stability=False,can_joint_break=False)
                if not accepted:
                    raise SkillError('execution_error','Attachment state setter rejected the selected pair',changed=True)
                self._relocate_contents(child,contents)
                for _ in range(min(30,max_steps)):
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                if not state.get_value(parent):
                    raise SkillError('postcondition_error','Attachment did not remain after settling',changed=True)
                self._verify_payload(dependencies)
        finally:
            self.frames_revision=-1
        return {'primitive':'attach','implementation':'transactional_official_AttachedTo_state_setter',
                'postcondition':'AttachedTo.get_value_after_settling',
                'preserved_carried_objects':len(contents),'failure_policy':'restore_pre_action_state'}

    def _container_payload(self, container):
        """Snapshot rigid contents relative to their support or fillable link.

        This observes executor geometry only; no task bindings or goals are read.
        """
        from omnigibson.object_states import Inside
        from omnigibson.utils import transform_utils as T
        links=[link for link in container.links.values() if link.is_meta_link and
               link.meta_link_type in {'fillable','openfillable'}]
        if not links:return []
        payload=[]
        lo,hi=container.aabb
        for child in self.env.scene.objects:
            if child in (container,self.robot) or getattr(child,'fixed_base',True) or Inside not in getattr(child,'states',{}):continue
            a,b=child.aabb;center=(a+b)/2
            if not bool(((center>=lo)&(center<=hi)).all()) or not child.states[Inside].get_value(container):continue
            link=next((link for link in links if bool(link.check_points_in_volume(center.unsqueeze(0)).item())),None)
            if link is None:
                raise SkillError('pre_condition_error','Cannot identify the supporting volume of existing contents')
            relative=T.relative_pose_transform(*child.get_position_orientation(),*link.get_position_orientation())
            payload.append((child,link,relative))
        if payload and getattr(container,'n_joints',0):
            from .container_support import support_frames
            frames=support_frames(self,container,[child for child,_,_ in payload])
            payload=[(child,frames.get(child,link),T.relative_pose_transform(
                *child.get_position_orientation(),*frames.get(child,link).get_position_orientation()))
                for child,link,_ in payload]
        return payload

    def _relocate_container_payload(self,payload):
        from omnigibson.utils import transform_utils as T
        for child,link,relative in payload:
            child.set_position_orientation(*T.pose_transform(*link.get_position_orientation(),*relative));child.keep_still()

    def _verify_container_payload(self,container,payload):
        from omnigibson.object_states import Inside
        escaped=[record for record in payload if not record[0].states[Inside].get_value(container)]
        if escaped:
            if hasattr(self, 'output'):
                from omnigibson.utils import transform_utils as T
                details=[]
                for child,link,relative in escaped:
                    position=child.get_position_orientation()[0]
                    expected=T.pose_transform(*link.get_position_orientation(),*relative)[0]
                    lo,hi=child.aabb;center=(lo+hi)/2
                    details.append({'object':child.name,'anchor_link':link.prim_path,
                        'position':position.tolist(),'expected_link_relative_position':expected.tolist(),
                        'displacement_m':float(self.torch.linalg.vector_norm(position-expected)),
                        'aabb_center_in_anchor_volume':bool(link.check_points_in_volume(center.unsqueeze(0)).item())
                            if link.is_meta_link else None,
                        'linear_velocity':child.get_linear_velocity().tolist()})
                self._carry_record(event='container_payload_violation',container=container.name,
                                   escaped_contents=details)
            raise SkillError('postcondition_error','Operation displaced existing contents from the container',changed=True)

    def _get_held(self):
        return self._ideal_held if self.ideal_carry else self.primitives._get_obj_in_hand()

    def _carry_record(self, **data):
        with (self.output/'carry_diagnostics.jsonl').open('a') as f:
            f.write(json.dumps({'at':now(),'env_step':self.steps,'audience':'executor_private',**data})+'\n')

    def _carry_follow(self):
        if not self.ideal_carry or self._ideal_held is None:return
        live={id(obj) for obj in self.env.scene.objects}
        if any(id(obj) not in live for obj in (self._ideal_held,*(c for c,_ in self._carry_contents))):
            self._carry_detach()
            self.frames_revision=-1
            raise SkillError('target_changed','Carried object changed during simulation',changed=True)
        from omnigibson.utils import transform_utils as T
        origin=(self.robot.eef_links[self._demo_arm].get_position_orientation()
                if getattr(self,'demo_motion',False) and getattr(self,'_demo_arm',None)
                else self.robot.get_position_orientation())
        pose=T.pose_transform(*origin,*self._carry_relative)
        self._ideal_held.set_position_orientation(*pose);self._ideal_held.keep_still()
        self._restore_carry_joints()
        for obj,relative in self._carry_contents:
            obj.set_position_orientation(*T.pose_transform(*pose,*relative));obj.keep_still()
        try:
            relocate_particles(self.env.scene,getattr(self,'_carry_particles',[]))
        except SkillError:
            self._carry_detach()
            self.frames_revision=-1
            raise

    def _relocate_contents(self, held, contents):
        from omnigibson.utils import transform_utils as T
        for obj,relative in contents:
            obj.set_position_orientation(*T.pose_transform(*held.get_position_orientation(),*relative));obj.keep_still()
        root,particles=getattr(self,'_placement_particle_payload',(None,[]))
        if root is held:relocate_particles(self.env.scene,particles)

    def _follow_placement_particles(self):
        # Preserve the selected assembly until the placement transaction ends,
        # including sampler physics and Environment.step's separate code path.
        _,particles=getattr(self,'_placement_particle_payload',(None,[]))
        if particles:relocate_particles(self.env.scene,particles)

    def _carry_detach(self):
        if not self.ideal_carry:
            for arm in self.robot.arm_names:self.robot.release_grasp_immediately(arm=arm)
        else:
            self._ideal_held=None;self._carry_relative=None;self._carry_contents=[];self._carry_dependencies=[]
            self._carry_particles=[]
            self._carry_joint_positions={}

    @contextmanager
    def _placement_context(self,target=None,*,watch=()):
        from .placement import placement_transaction
        held=getattr(self,'_ideal_held',None);relative=getattr(self,'_carry_relative',None);contents=list(getattr(self,'_carry_contents',[]))
        dependencies=list(getattr(self,'_carry_dependencies',[]))
        particles=list(getattr(self,'_carry_particles',[]))
        joints=dict(getattr(self,'_carry_joint_positions',{}))
        old_particles=getattr(self,'_placement_particle_payload',(None,[]))
        self._placement_particle_payload=(held,particles)
        previous=getattr(self,'_placement_watch',())
        self._placement_watch=tuple(obj for obj in (held,target,*watch,*(c for c,_ in contents)) if obj is not None)
        try:
            with self._anchored_operation(target),placement_transaction(self.og.sim,self._placement_record):yield
        except Exception as exc:
            if isinstance(exc,SkillError) and exc.code=='target_changed':
                self._carry_detach()
                self.frames_revision=-1
            else:
                self._ideal_held=held;self._carry_relative=relative;self._carry_contents=contents;self._carry_dependencies=dependencies
                self._carry_particles=particles
                self._carry_joint_positions=joints
                if held is not None:self._carry_follow()
            raise
        finally:
            self._placement_watch=previous
            self._placement_particle_payload=old_particles

    def _check_placement_objects(self):
        watched=getattr(self,'_placement_watch',())
        if watched:
            live={id(obj) for obj in self.env.scene.objects}
            if any(id(obj) not in live for obj in watched):
                self.frames_revision=-1
                raise SkillError('target_changed','Placement object changed during simulation',changed=True)

    def _grasp_dependencies(self,obj):
        """Read-only closure shared by grasp and loaded-departure prediction."""
        from omnigibson.object_states import Inside,OnTop,Touching
        candidates=[child for child in self.env.scene.objects if child not in (obj,self.robot)
                    and hasattr(child,'states')]
        def relation(child,parent):
            attached=attachment_relation(child,parent)
            if attached:return attached
            if getattr(child,'fixed_base',True):return None
            lo,hi=parent.aabb;clo,chi=child.aabb;center=(clo+chi)/2
            if Inside in child.states and bool(((center>=lo-.02)&(center<=hi+.02)).all()) and child.states[Inside].get_value(parent):return 'Inside'
            overlap=bool(((chi[:2]>=lo[:2])&(clo[:2]<=hi[:2])).all())
            if overlap and float(clo[2])>=float(lo[2])-.02 and float(clo[2])<=float(hi[2])+.08:
                if OnTop in child.states and Touching in child.states and child.states[OnTop].get_value(parent) and child.states[Touching].get_value(parent):return 'OnTop'
            return None
        return support_closure(obj,candidates,relation)

    def _ideal_grasp(self,obj,max_steps):
        from omnigibson.utils import transform_utils as T
        from omnigibson.object_states import Inside,OnTop,Touching
        torch=self.torch
        if self._ideal_held is obj:
            try:
                verify_particles(self.env.scene,getattr(self,'_carry_particles',[]))
            except Exception:
                self._carry_detach();self.frames_revision=-1
                raise
            return {'primitive':'grasp','postcondition':'selected_object_already_held'}
        if self._ideal_held is not None:raise SkillError('hand_occupied','A carry relationship already exists')
        if obj.fixed_base:raise SkillError('fixed_object','The selected object has a fixed base')
        # Preserve both contained objects and supported objects (e.g. food on a
        # plate), including nested payloads. Fixed scene objects never follow.
        original=obj.get_position_orientation()
        local=getattr(self,'_local_operation',None)
        prepared=getattr(local,'carry_plan',None)
        if prepared is not None:
            pose=self._local_pose()
            if (math.dist(pose[:2],prepared['pose'][:2])>.01 or
                    abs(math.atan2(math.sin(pose[2]-prepared['pose'][2]),math.cos(pose[2]-prepared['pose'][2])))>.02):
                raise SkillError('local_adjustment_blocked','Base moved after carry preparation')
        dependencies=self._grasp_dependencies(obj)
        if not getattr(self,'demo_motion',False):
            from .carry_geometry import PayloadSweep, plan_instant_carry
            payload=[obj,*[child for child,_,_ in dependencies]]
            if prepared is None:
                pose=self._local_pose()
                check=local.check if local is not None else self.deadline.check
                sweep=PayloadSweep(self,obj,payload,pose,check=check)
                base_z=float(self.robot.get_position_orientation()[0][2])
                route,status=plan_instant_carry(sweep.origin,(*pose[:2],base_z,pose[2]),
                                               sweep.bounds,sweep.destination_clear,check=check)
                if route is None:
                    self._carry_record(status=status,diagnostic=sweep.last_failure)
                    raise SkillError('local_adjustment_blocked','No collision-free carry destination found')
                prepared={'pose':pose,'route':route,'status':status,'transfer_mode':'instant_pose_transfer'}
            self._validate_carry_destination(obj,prepared,payload,max_steps)
        contents=[(child,T.relative_pose_transform(*child.get_position_orientation(),*original)) for child,_,_ in dependencies]
        particles=snapshot_particles(self.env.scene,[obj,*[child for child,_,_ in dependencies]],include_supported=True)
        demo = getattr(self,'demo_motion',False) and getattr(self,'_demo_arm',None)
        # The entire selected assembly is leaving its old support. An outer
        # container can have recorded nested children as well as this root;
        # retaining those records would pull the children back after release.
        moving={id(obj),*(id(child) for child,_,_ in dependencies)}
        stabilized=getattr(self,'_stabilized_containers',{})
        for container in list(stabilized):
            if id(container) in moving:stabilized.pop(container)
        payloads=getattr(self,'_stabilized_container_payloads',{})
        for container,records in list(payloads.items()):
            if id(container) in moving:payloads.pop(container)
            else:payloads[container]=[record for record in records if id(record[0]) not in moving]
        base_pos,base_quat=self.robot.get_position_orientation()
        # In demo mode the carried pose follows the visible hand. The object is
        # lifted over recorded control steps after the carry relation is made.
        if prepared is not None:
            relative=T.relative_pose_transform(*original,base_pos,base_quat)
        elif demo:
            relative=T.relative_pose_transform(*original,
                *self.robot.eef_links[self._demo_arm].get_position_orientation())
        else:
            relative=T.relative_pose_transform(*original,base_pos,base_quat)
        self._ideal_held=obj;self._carry_relative=relative;self._carry_contents=contents;self._carry_dependencies=dependencies
        self._carry_particles=particles
        self._carry_joint_positions={body:body.get_joint_positions().clone()
            for body in [obj,*[child for child,_ in contents]] if getattr(body,'n_joints',0)}
        self._carry_record(status='attached',object=obj.name,contained_objects=[c.name for c,_ in contents],
                           payload_relations=[{'child':c.name,'parent':p.name,'relation':kind} for c,p,kind in dependencies],
                           contained_particles=particle_count(particles,'contained'),
                           supported_particles=particle_count(particles,'supported'),
                           preserved_articulated_objects=[body.name for body in self._carry_joint_positions],
                           implementation='pose_projection_no_fixed_joint',
                           original_position=original[0].tolist())
        try:
            self._carry_follow()
            if prepared is not None:
                packing=self._execute_prepared_carry(obj,prepared,max_steps)
            else:
                with self._anchored_operation():
                    for _ in range(min(6,max_steps)):
                        self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                packing={'status':'demo_hand_carry'}
            pos,quat=obj.get_position_orientation()
            if not bool(torch.isfinite(pos).all() and torch.isfinite(quat).all()):
                raise SkillError('physics_instability','Non-finite held pose',changed=True)
            verify_particles(self.env.scene,particles)
        except Exception:
            # Retain actual physics outcomes, but a failed attachment must not
            # become an unchecked "already held" success on retry.
            self._carry_detach();self.frames_revision=-1
            raise
        return {'primitive':'grasp','implementation':'controlled_pose_carry','postcondition':'selected_object_held_and_finite',
                'physical_grasp':False,'fixed_joint_created':False,'carry_posture':packing,'contained_rigid_objects':len(contents),
                'contained_particles':particle_count(particles,'contained'),
                'supported_particles':particle_count(particles,'supported')}

    def _verify_payload(self, dependencies, *, support_target=None, support_resting=None):
        from omnigibson.object_states import Inside,OnTop,Touching,AttachedTo
        verify_particles(self.env.scene,getattr(self,'_placement_particle_payload',(None,[]))[1])
        for child,parent,kind in dependencies:
            if kind=='AttachedToParent':
                child,parent=parent,child
                kind='AttachedTo'
            state={'Inside':Inside,'OnTop':OnTop,'AttachedTo':AttachedTo}[kind]
            if state not in child.states or not child.states[state].get_value(parent):
                # Releasing an assembly on a surface can transfer a supported
                # child's weight to that same surface. Requiring the old
                # OnTop edge would reject an otherwise stable placement.
                # Containment remains mandatory; sliding/falling elsewhere or
                # an unmeasured resting state never qualifies.
                resting=(support_resting or {}).get(id(child),{})
                if (kind=='OnTop' and support_target is not None
                        and resting.get('stable',False)
                        and OnTop in child.states and Touching in child.states
                        and child.states[OnTop].get_value(support_target)
                        and child.states[Touching].get_value(support_target)):
                    self._placement_record({'status':'payload_support_transferred',
                        'child':child.name,'original_parent':parent.name,
                        'support':support_target.name,'resting_motion':resting})
                    continue
                raise SkillError('postcondition_error','Carried payload lost its original support, containment or attachment after placement',changed=True)

    def _ideal_release(self,max_steps):
        if self._ideal_held is None:raise SkillError('empty_hand','No carried object')
        self._carry_record(status='released',object=self._ideal_held.name)
        self._carry_detach()
        if getattr(self,'demo_motion',False):self._demo_arm=None
        with self._anchored_operation():
            for _ in range(min(30,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        return {'primitive':'release','implementation':'controlled_carry_detach_and_settle','physical_grasp':False}
