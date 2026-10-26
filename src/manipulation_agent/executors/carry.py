"""Native grasp ownership and one-time object state actions.

No body pose is projected during subsequent physics steps.
"""
import json
import math
from ..contracts import SkillError
from ..records import now
from .particle_payload import snapshot_particles, relocate_particles, verify_particles, particle_count


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


class ControlledCarry:

    def _grasp_state(self, obj, max_steps, point):
        """Transfer once, then let OG's assisted-grasp joint own the object."""
        from omnigibson.utils import transform_utils as T
        held = self._get_held()
        if held is obj:
            return {'primitive': 'grasp', 'postcondition': 'selected_object_already_held'}
        if held is not None:
            raise SkillError('hand_occupied', 'An object is already grasped')
        if obj.fixed_base:
            raise SkillError('fixed_object', 'The selected object has a fixed base')
        arm = self.primitives.arm
        joints = self.robot.get_joint_positions().clone()
        indices = self.robot.arm_control_idx[arm]
        ready = self.robot.untucked_default_joint_pos[indices]
        # A tucked hand is beside the torso. Bringing a wide object there
        # embeds it in the robot even when its finger contact is correct.
        if not self.torch.allclose(joints[indices], ready, atol=.01, rtol=0):
            joints[indices] = ready
            command = self.robot.q_to_action(joints)
            preparation_steps = min(30, max_steps)
            for _ in range(preparation_steps):
                self._step(command)
            max_steps -= preparation_steps
        original = obj.get_position_orientation()
        dependencies = self._grasp_dependencies(obj)
        contents = [(child, T.relative_pose_transform(*child.get_position_orientation(), *original))
                    for child, _, _ in dependencies]
        particles = snapshot_particles(self.env.scene, [obj, *[child for child, _ in contents]],
                                       include_supported=True)
        direction = T.quat2mat(self.robot.get_eef_orientation(arm))[:, 2]
        contact = (self.robot.get_eef_position(arm) + direction *
                   max(self.robot.eef_to_fingertip_lengths[arm].values()))
        # Grasp the selected object on its support plane facing the fingers.
        # An arbitrary visible point can leave the rest of the object behind
        # the fingertip plane, intersecting the fingers and palm.
        vertices = self.torch.as_tensor(self._collision_vertices(obj),
                                       dtype=point.dtype, device=point.device)
        depths = vertices @ direction
        surface = vertices[depths <= depths.min() + 1e-5]
        grasp_point = surface[self.torch.linalg.vector_norm(surface - point, dim=1).argmin()]
        obj.set_position_orientation(position=original[0] + contact - grasp_point)
        self._relocate_contents(obj, contents)
        relocate_particles(self.env.scene, particles)
        self.robot._establish_grasp(obj, obj.root_link_name, arm, contact, 'FixedJoint')
        for _ in range(min(6, max_steps)):
            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        if self._get_held() is not obj:
            raise SkillError('postcondition_error', 'Native grasp did not hold the selected object', changed=True)
        self.frames_revision = -1
        return {'primitive': 'grasp', 'implementation': 'native_assisted_grasp_joint',
                'postcondition': 'selected_object_in_native_grasp', 'fixed_joint_created': True,
                'payload_transfer': 'once_at_grasp', 'subsequent_motion': 'native_physics'}

    def _place_state(self, primitive, target, max_steps, point, yaw_degrees=None):
        """Snapshot transported material once; never pin it during settling."""
        held=self._get_held()
        contents=self._carry_contents
        self._placement_particle_payload=(held,snapshot_particles(
            self.env.scene,[held,*[child for child,_ in contents]],include_supported=True))
        try:
            if primitive=='place_inside':
                return self._checked_place_inside(target,max_steps,point=point)
            if primitive=='place_under':
                return self._checked_place_under(target,max_steps)
            method=self._checked_place_on_top if primitive=='place_on_top' else self._checked_place_next_to
            return method(target,max_steps,point,yaw_degrees)
        finally:
            self._placement_particle_payload=(None,[])

    def _state_action(self, primitive, obj, max_steps):
        from omnigibson.object_states import Open,ToggledOn
        state=Open if primitive in {'open','close'} else ToggledOn
        wanted=primitive in {'open','toggle_on'}
        if state not in obj.states:raise SkillError('pre_condition_error','Selected object does not support this operation')
        # Open.set_value teleports articulated joints. Drawer contents must move
        # with their supporting volume, not be left at the previous world pose.
        payload=self._container_payload(obj) if state is Open else []
        particles=snapshot_particles(self.env.scene,[obj,*[child for child,_,_ in payload]]) if state is Open else []
        from .particle_payload import particle_link_motion, particle_containment_observation
        moved_particles=particle_link_motion(particles)
        open_goal=None
        if primitive=='open':
            from .articulation_goal import checked_open_goal
            open_goal=checked_open_goal(self,obj,self.deadline.check)
        samples=[]
        def capture(phase):
            if state is Open:
                from .articulation_diagnostics import articulation_sample
                samples.append(articulation_sample(self,obj,phase))
                # A native CUDA/process fault cannot reach report(). Persist
                # each boundary while its joint/contact evidence is available.
                self._placement_record(dict(status='articulation_phase',
                    request_id=getattr(self,'_active_request_id',None),
                    target=obj.name,primitive=primitive,**samples[-1]))
        def report(status,**details):
            if state is Open:
                from .articulation_diagnostics import record_articulation
                record_articulation(self,obj,primitive,samples,status=status,**details)
        capture('before_setter')
        # Upstream symbolic OPEN samples a random opening and returns early
        # when already ajar. Use a checked usable opening; close fully.
        if open_goal is not None:
            obj.set_joint_positions(open_goal);obj.keep_still()
            obj.states[state].clear_cache();obj.state_updated()
            accepted=bool(obj.states[state].get_value())
        else:
            accepted=obj.states[state].set_value(wanted,**({'fully':True} if state is Open else {}))
        if not accepted:raise SkillError('execution_error','Object state setter rejected the selected operation',changed=True)
        self._relocate_container_payload(payload)
        transported_particles=moved_particles()
        # A door setter moves only its articulated support frames. Leave
        # material in stationary shelves/bottles to ordinary physics rather
        # than resetting its velocities or demanding zero natural settling.
        relocate_particles(self.env.scene,transported_particles)
        verify_particles(self.env.scene,transported_particles)
        capture('after_setter')
        for step in range(min(30,max_steps)):
            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if step in (0,4,14,29):capture('settle_'+str(step+1))
        if bool(obj.states[state].get_value())!=wanted:
            report('failed',reason='state_reopened_or_reclosed_after_settling',
                   payload=[child.name for child,_,_ in payload])
            raise SkillError('postcondition_error','Requested object operation did not remain stable',changed=True)
        self._verify_container_payload(obj,payload)
        if particles:
            self._placement_record({'status':'articulation_particle_settle',
                'target':obj.name,'primitive':primitive,
                'transported_particles':particle_count(transported_particles),
                'particles':particle_containment_observation(self.env.scene,particles,outer=obj)})
        # Still require containment for material this setter actually
        # transported. Unmoved background material is not an open/close
        # postcondition and is never restored to conceal its real settling.
        verify_particles(self.env.scene,transported_particles)
        report('passed',payload=[child.name for child,_,_ in payload])
        return {'primitive':primitive,'implementation':'official_state_setter_with_link_payload',
                'fully_open_or_closed':primitive=='close','opening_policy':'largest_checked_usable_endpoint' if primitive=='open' else None,
                'postcondition':'official_state_and_containment_after_settling',
                'preserved_contained_objects':len(payload),'failure_policy':'retain_physical_result'}

    def _checked_attach(self, parent, max_steps):
        """Attach the carried child to the visually selected compatible parent."""
        from omnigibson.object_states import AttachedTo
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
            # Native joint constraints and contacts determine the outcome.
            self._carry_detach()
            accepted=state.set_value(parent,True,bypass_alignment_checking=True,
                                     check_physics_stability=True)
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
        return {'primitive':'attach','implementation':'official_AttachedTo_state_setter',
                'postcondition':'AttachedTo.get_value_after_settling',
                'preserved_carried_objects':len(contents),'failure_policy':'retain_physical_result'}

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

    @property
    def _carry_dependencies(self):
        held = self._get_held()
        return [] if held is None else self._grasp_dependencies(held)

    @property
    def _carry_contents(self):
        held = self._get_held()
        dependencies=[] if held is None else self._grasp_dependencies(held)
        if not dependencies:return []
        from omnigibson.utils import transform_utils as T
        pose = held.get_position_orientation()
        return [(child, T.relative_pose_transform(*child.get_position_orientation(), *pose))
                for child, _, _ in dependencies]

    def _get_held(self):
        return self.primitives._get_obj_in_hand()

    def _carry_record(self, **data):
        with (self.output/'carry_diagnostics.jsonl').open('a') as f:
            f.write(json.dumps({'at':now(),'env_step':self.steps,'audience':'executor_private',**data})+'\n')


    def _relocate_contents(self, held, contents):
        from omnigibson.utils import transform_utils as T
        for obj,relative in contents:
            obj.set_position_orientation(*T.pose_transform(*held.get_position_orientation(),*relative));obj.keep_still()
        root,particles=getattr(self,'_placement_particle_payload',(None,[]))
        if root is held:relocate_particles(self.env.scene,particles)


    def _carry_detach(self):
        for arm in self.robot.arm_names:
            self.robot.release_grasp_immediately(arm=arm)

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
                self._placement_record({'status':'payload_relation_failed',
                    'child':child.name,'parent':parent.name,'relation':kind,
                    'child_position':child.get_position_orientation()[0].tolist(),
                    'parent_position':parent.get_position_orientation()[0].tolist(),
                    'child_orientation':child.get_position_orientation()[1].tolist(),
                    'parent_orientation':parent.get_position_orientation()[1].tolist(),
                    'child_linear_velocity':child.get_linear_velocity().tolist()})
                raise SkillError('postcondition_error','Carried payload lost its original support, containment or attachment after placement',changed=True)
