"""Explicit ideal carry without a dynamic robot/object fixed joint.

Object identity comes from the selected RGB pixel only. This is a research
motor protocol, not physical grasp control. Ordinary environment collisions
and gravity remain enabled; held poses are projected around each control step.
"""
from contextlib import contextmanager
import json
from ..contracts import SkillError
from ..records import now


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


def bulk_grasp_reason(obj, dependencies):
    """Flag a risky selected support without substituting another object."""
    if len(dependencies) >= 2:
        return 'multiple_payloads'
    lo, hi = obj.aabb
    if max(float(hi[axis] - lo[axis]) for axis in (0, 1)) > 1.35:
        return 'furniture_size'
    return None


def supported_child_on_top(child, parent, on_top, touching):
    """Reject inverted contacts: a support beneath food is not its payload."""
    if on_top not in child.states or touching not in child.states:
        return False
    lo, hi = parent.aabb
    child_lo, child_hi = child.aabb
    if not bool(((child_hi[:2] >= lo[:2]) & (child_lo[:2] <= hi[:2])).all()):
        return False
    # A shallow plate can geometrically overlap a pizza resting on it. Its
    # lower face still lies below the pizza midpoint, so it must not follow a
    # grasp of the pizza even if a noisy bidirectional OnTop predicate is true.
    if (float(child_lo[2]) < float((lo[2] + hi[2]) / 2) - .01 or
            float(child_lo[2]) > float(hi[2]) + .08):
        return False
    if on_top in parent.states and parent.states[on_top].get_value(child):
        return False
    return bool(child.states[on_top].get_value(parent) and
                child.states[touching].get_value(parent))


class ControlledCarry:
    @contextmanager
    def _anchored_operation(self, obj=None):
        previous=self._base_target;old_object=getattr(self,'_object_anchor',None)
        pos,quat=self.robot.get_position_orientation();joints=self.robot.get_joint_positions()
        indices=self.torch.tensor([i for i in range(len(joints)) if i not in self.robot.base_idx.tolist()],device=joints.device)
        self._base_target={'position':pos.clone(),'orientation':quat.clone(),'indices':indices,
                           'posture':joints[indices].clone(),'held':None,'relative':None}
        if obj is not None:
            anchored = obj if isinstance(obj,(tuple,list)) else (obj,)
            self._object_anchor=tuple((item,tuple(v.clone() for v in item.get_position_orientation()))
                                      for item in anchored)
        original_step=self.og.sim.step_physics
        def anchored_physics(*args,**kwargs):
            self._restore_base_target();self._restore_object_anchor()
            try:return original_step(*args,**kwargs)
            finally:self._restore_base_target();self._restore_object_anchor()
        self.og.sim.step_physics=anchored_physics
        try:yield
        finally:
            self.og.sim.step_physics=original_step
            try:self._restore_base_target();self._restore_object_anchor()
            finally:self._base_target=previous;self._object_anchor=old_object

    def _restore_object_anchor(self):
        if getattr(self,'_object_anchor',None) is not None:
            for obj,pose in self._object_anchor:
                obj.set_position_orientation(*pose);obj.keep_still()

    def _record_open_joints(self, obj, state, phase, wanted):
        """Private read-only evidence; never included in action feedback."""
        record = {'status': 'open_joint_snapshot', 'phase': phase,
                  'target': obj.name, 'requested_open': wanted, 'joints': []}
        try:
            record['observed_open'] = bool(state.get_value())
            for joint in state.relevant_joints_info[1]:
                record['joints'].append({'name': joint.name,
                    'position': float(joint.get_state()[0]),
                    'lower_limit': float(joint.lower_limit),
                    'upper_limit': float(joint.upper_limit)})
        except Exception as exc:
            record['snapshot_error'] = type(exc).__name__
        self._placement_record(record)

    def _prepare_articulation_edit(self, primitive):
        if primitive not in {'open', 'close'} or getattr(self.og.sim, '_native_particle_edit_barrier', False) is not True:
            return
        protocol = (getattr(self, 'research_instance', None) or {}).get('articulation_edit_barrier')
        if protocol is None:
            self.og.sim.prepare_articulation_after_material_edit()
        elif protocol == 'native_state_checked_before_articulation_edit_v1':
            self.og.sim.prepare_articulation_after_material_edit(force=True, reason='before_native_' + primitive)
        else:
            raise ValueError('Unsupported research articulation edit barrier')

    def _ideal_state_action(self, primitive, obj, max_steps):
        self._prepare_articulation_edit(primitive)
        from omnigibson.object_states import Open,ToggledOn
        from .placement import placement_transaction
        state=Open if primitive in {'open','close'} else ToggledOn
        wanted=primitive in {'open','toggle_on'}
        if state not in obj.states:raise SkillError('pre_condition_error','Selected object does not support this operation')
        # Open.set_value teleports articulated joints. Drawer contents must move
        # with their supporting volume, not be left at the previous world pose.
        payload=self._container_payload(obj) if state is Open else []
        repaired_contents=0
        if state is Open:
            self._record_open_joints(obj,obj.states[state],"before",wanted)
        stabilized=getattr(self,'_stabilized_open_joints',{})
        prior=stabilized.get(obj) if state is Open else None
        try:
            with self._anchored_operation(obj),placement_transaction(self.og.sim,self._placement_record):
                # Upstream symbolic OPEN samples a random opening and returns early
                # when already ajar. Fully open/close all annotated joints instead.
                accepted=obj.states[state].set_value(wanted,**({'fully':True} if state is Open else {}))
                if not accepted:raise SkillError('execution_error','Object state setter rejected the selected operation',changed=True)
                if state is Open:
                    self._record_open_joints(obj,obj.states[state],"after_setter",wanted)
                    self._sync_diagnostic_articulation_visuals(obj)
                if state is Open:
                    if wanted:
                        stabilized.pop(obj,None)
                    else:
                        joints=obj.states[Open].relevant_joints_info[1]
                        stabilized[obj]=[(joint,float(joint.get_state()[0])) for joint in joints]
                    self._stabilized_open_joints=stabilized
                self._relocate_container_payload(payload)
                for _ in range(min(30,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                if state is Open:
                    self._record_open_joints(obj,obj.states[state],"after_settling",wanted)
                if bool(obj.states[state].get_value())!=wanted:
                    raise SkillError('postcondition_error','Requested object operation did not remain stable',changed=True)
                if state is Open and not wanted:
                    repaired_contents=self._repair_closed_container_contents(obj,payload)
                self._verify_container_payload(obj,payload)
                if any(container is obj for container in getattr(self,'_stabilized_container_payloads',{})):
                    self._stabilized_container_payloads[obj]=self._container_payload(obj)
        except Exception:
            if state is Open:
                if prior is None:stabilized.pop(obj,None)
                else:stabilized[obj]=prior
            raise
        return {'primitive':primitive,'implementation':'transactional_official_state_setter_with_link_payload',
                'fully_open_or_closed':state is Open,'postcondition':'official_state_and_containment_after_settling',
                'preserved_contained_objects':len(payload),
                'repaired_contained_objects':repaired_contents,
                'failure_policy':'restore_pre_action_state'}

    def _repair_closed_container_contents(self, container, payload):
        """Reseat only contents that left the selected fillable volume on closing."""
        from omnigibson.object_states import Inside
        displaced=[child for child,_,_ in payload if not child.states[Inside].get_value(container)]
        if not displaced:
            return 0
        fillable=[link for link in container.links.values() if link.is_meta_link and
                  link.meta_link_type in {'fillable','openfillable'}]
        if not fillable:
            raise SkillError('postcondition_error','Closed container has no fillable volume',changed=True)
        for child in displaced:
            if self._fillable_grid_pose(child,container,fillable,[]):
                continue
            others=[record for record in payload if record[0] is not child]
            if not self._repack_fillable_contents(child,container,fillable,others,[]):
                raise SkillError('postcondition_error','Closed container could not retain its contents',changed=True)
        self._placement_record({'status':'closed_container_contents_reseated',
            'target':container.name,'objects':[child.name for child in displaced]})
        return len(displaced)

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
            with self._placement_context(parent):
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
        """Snapshot existing rigid contents relative to their actual fillable link.

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
        return payload

    def _relocate_container_payload(self,payload):
        from omnigibson.utils import transform_utils as T
        for child,link,relative in payload:
            child.set_position_orientation(*T.pose_transform(*link.get_position_orientation(),*relative));child.keep_still()

    def _verify_container_payload(self,container,payload):
        from omnigibson.object_states import Inside
        if any(not child.states[Inside].get_value(container) for child,_,_ in payload):
            raise SkillError('postcondition_error','Operation displaced existing contents from the container',changed=True)

    def _get_held(self):
        return self._ideal_held if self.ideal_carry else self.primitives._get_obj_in_hand()

    def _carry_record(self, **data):
        with (self.output/'carry_diagnostics.jsonl').open('a') as f:
            f.write(json.dumps({'at':now(),'env_step':self.steps,'audience':'executor_private',**data})+'\n')

    def _carry_follow(self):
        if not self.ideal_carry or self._ideal_held is None:return
        from omnigibson.utils import transform_utils as T
        origin=(self.robot.eef_links[self._demo_arm].get_position_orientation()
                if getattr(self,'demo_motion',False) and getattr(self,'_demo_arm',None)
                else self.robot.get_position_orientation())
        pose=T.pose_transform(*origin,*self._carry_relative)
        if getattr(self,'_carry_orientation',None) is not None:
            pose=(pose[0],self._carry_orientation)
        self._ideal_held.set_position_orientation(*pose);self._ideal_held.keep_still()
        self._relocate_contents(self._ideal_held,self._carry_contents)

    def _grasp_particle_payload(self, obj):
        """Keep physical particles inside the selected container during ideal carry."""
        if getattr(self, 'high_level_harness', False):
            return []
        scene = getattr(getattr(self, 'env', None), 'scene', None)
        if getattr(scene, 'system_registry', None) is None:
            return []
        from omnigibson.object_states import ContainedParticles
        from omnigibson.utils import transform_utils as T
        if ContainedParticles not in obj.states:
            return []
        origin, orientation = obj.get_position_orientation()
        payload = []
        for system in self.env.scene.system_registry.objects:
            if not self.env.scene.is_physical_particle_system(system_name=system.name) or not system.n_particles:
                continue
            contained = obj.states[ContainedParticles].get_value(system)
            indices = self.torch.where(contained.in_volume)[0]
            if not len(indices):
                continue
            positions, _ = system.get_particles_position_orientation()
            indices = indices.to(device=positions.device)
            rotation = T.quat2mat(orientation).to(device=positions.device,dtype=positions.dtype)
            local = (positions[indices] - origin.to(device=positions.device,dtype=positions.dtype)) @ rotation
            payload.append((system,indices.clone(),local.clone()))
        return payload

    def _relocate_particle_contents(self, held):
        if held is not getattr(self,'_carry_particle_carrier',None):
            return
        from omnigibson.utils import transform_utils as T
        origin, orientation = held.get_position_orientation()
        for system, indices, local in getattr(self,'_carry_particle_contents',[]):
            if not system.initialized or not system.n_particles or int(indices.max()) >= system.n_particles:
                continue
            positions, _ = system.get_particles_position_orientation()
            updated = positions.clone()
            rotation = T.quat2mat(orientation).to(device=updated.device,dtype=updated.dtype)
            updated[indices] = local @ rotation.T + origin.to(device=updated.device,dtype=updated.dtype)
            system.set_particles_position_orientation(positions=updated)

    def _relocate_contents(self, held, contents):
        from omnigibson.utils import transform_utils as T
        for obj,relative in contents:
            obj.set_position_orientation(*T.pose_transform(*held.get_position_orientation(),*relative));obj.keep_still()
        self._relocate_particle_contents(held)

    def _carry_detach(self):
        if not self.ideal_carry:
            for arm in self.robot.arm_names:self.robot.release_grasp_immediately(arm=arm)
        else:
            self._ideal_held=None;self._carry_relative=None;self._carry_orientation=None;self._carry_contents=[];self._carry_dependencies=[]
            # A placement moves the released container after detaching it.
            # Retain its particle transform until the placement commits.

    def _restore_expected_fill(self, carrier, systems):
        """Keep a vessel filled when unsupported CPU particles spill on release."""
        if carrier is None or not systems:
            return
        from omnigibson.object_states import ContainedParticles, Filled
        if Filled not in carrier.states:
            return
        for system in systems:
            if carrier.states[Filled].get_value(system):
                continue
            accepted=carrier.states[Filled].set_value(system, True)
            method='official_filled_setter'
            if accepted and not carrier.states[Filled].get_value(system):
                # Filled.set_value reports success even when contact filtering
                # generated too few particles to cross its fill threshold.
                # Semantic magic keeps the selected vessel's liquid state while
                # the CPU particle system settles around nearby furniture.
                if ContainedParticles in carrier.states:
                    system.generate_particles_from_link(
                        obj=carrier,link=carrier.states[ContainedParticles].link,
                        check_contact=False)
                    method='selected_vessel_volume_without_contact_filter'
            if not accepted or not carrier.states[Filled].get_value(system):
                raise SkillError('postcondition_error',
                                 'Carried container lost its filled liquid state', changed=True)
            self._carry_record(status='liquid_fill_restored_after_placement',
                               object=carrier.name, system=system.name, method=method)

    @contextmanager
    def _placement_context(self,target=None):
        from .placement import placement_transaction
        held=getattr(self,'_ideal_held',None);relative=getattr(self,'_carry_relative',None)
        orientation=getattr(self,'_carry_orientation',None);contents=list(getattr(self,'_carry_contents',[]))
        dependencies=list(getattr(self,'_carry_dependencies',[]))
        particle_carrier=getattr(self,'_carry_particle_carrier',None)
        particles=list(getattr(self,'_carry_particle_contents',[]))
        filled_systems=list(getattr(self,'_carry_filled_systems',[]))
        stabilized_containers=dict(getattr(self,'_stabilized_containers',{}))
        stabilized_payloads={obj:list(records) for obj,records in
                             getattr(self,'_stabilized_container_payloads',{}).items()}
        try:
            with self._anchored_operation(target),placement_transaction(self.og.sim,self._placement_record):
                yield
                if particle_carrier is not None and particles:
                    from omnigibson.object_states import Contains
                    if Contains in particle_carrier.states and any(
                            not particle_carrier.states[Contains].get_value(system)
                            for system, _, _ in particles):
                        raise SkillError('postcondition_error',
                                         'Placement lost liquid from the carried container', changed=True)
                self._restore_expected_fill(particle_carrier,filled_systems)
        except Exception:
            self._ideal_held=held;self._carry_relative=relative;self._carry_orientation=orientation
            self._carry_contents=contents;self._carry_dependencies=dependencies
            self._carry_particle_carrier=particle_carrier;self._carry_particle_contents=particles
            self._carry_filled_systems=filled_systems
            self._stabilized_containers=stabilized_containers
            self._stabilized_container_payloads=stabilized_payloads
            if held is not None:self._carry_follow()
            raise
        else:
            if self._ideal_held is None:
                self._carry_particle_carrier=None;self._carry_particle_contents=[]
                self._carry_filled_systems=[]

    def _grasp_payload_snapshot(self,obj):
        """Capture current rigid support before the visible hand can disturb it."""
        from omnigibson.utils import transform_utils as T
        from omnigibson.object_states import Inside,OnTop,Touching
        original=obj.get_position_orientation()
        candidates=[child for child in self.env.scene.objects if child not in (obj,self.robot)
                    and not getattr(child,'fixed_base',True) and hasattr(child,'states')]
        def relation(child,parent):
            lo,hi=parent.aabb;clo,chi=child.aabb;center=(clo+chi)/2
            if Inside in child.states and bool(((center>=lo-.02)&(center<=hi+.02)).all()) and child.states[Inside].get_value(parent):return 'Inside'
            if supported_child_on_top(child,parent,OnTop,Touching):return 'OnTop'
            return None
        dependencies=support_closure(obj,candidates,relation)
        contents=[(child,T.relative_pose_transform(*child.get_position_orientation(),*original)) for child,_,_ in dependencies]
        return dependencies,contents

    def _grasp_support_anchors(self, obj):
        """Protect only the movable container currently holding the selected item.

        Reaching into a light basket can tip it before the selected child is
        attached. Its other contents must remain in their original volume while
        the visible arm withdraws. This is motor-only scene state: it never
        changes the model's selected object, point, or target destination.
        """
        from omnigibson.object_states import Inside
        if Inside not in obj.states:
            return ()
        center = sum(obj.aabb) / 2
        anchors = []
        seen = {id(obj), id(self.robot)}
        for parent in self.env.scene.objects:
            if (id(parent) in seen or getattr(parent, 'fixed_base', True)
                    or not hasattr(parent, 'states')):
                continue
            lo, hi = parent.aabb
            if not bool(((center >= lo - .03) & (center <= hi + .03)).all()):
                continue
            if not obj.states[Inside].get_value(parent):
                continue
            anchors.append(parent)
            seen.add(id(parent))
            for sibling in self.env.scene.objects:
                if (id(sibling) in seen or getattr(sibling, 'fixed_base', True)
                        or Inside not in getattr(sibling, 'states', {})):
                    continue
                a, b = sibling.aabb
                sibling_center = (a + b) / 2
                if (bool(((sibling_center >= lo - .03) & (sibling_center <= hi + .03)).all())
                        and sibling.states[Inside].get_value(parent)):
                    anchors.append(sibling)
                    seen.add(id(sibling))
        if anchors:
            self._carry_record(status='grasp_support_snapshot', object=obj.name,
                               anchored_objects=[item.name for item in anchors])
        return tuple(anchors)

    def _ideal_grasp(self,obj,max_steps,payload_snapshot=None,support_anchors=()):
        from omnigibson.utils import transform_utils as T
        torch=self.torch
        if self._ideal_held is obj:return {'primitive':'grasp','postcondition':'selected_object_already_held'}
        if self._ideal_held is not None:raise SkillError('hand_occupied','A carry relationship already exists')
        if obj.fixed_base:raise SkillError('fixed_object','The selected object has a fixed base')
        # Preserve both contained objects and supported objects (e.g. food on a
        # plate), including nested payloads. Fixed scene objects never follow.
        original=obj.get_position_orientation()
        self._carry_particle_carrier=None;self._carry_particle_contents=[];self._carry_filled_systems=[]
        dependencies,contents=(payload_snapshot if payload_snapshot is not None else
                               self._grasp_payload_snapshot(obj))
        if payload_snapshot is not None:
            self._relocate_contents(obj,contents)
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
        if getattr(self,'demo_motion',False) and getattr(self,'_demo_arm',None):
            relative=T.relative_pose_transform(*original,
                *self.robot.eef_links[self._demo_arm].get_position_orientation())
        else:
            # Legacy ideal carry starts at a safe elevated pose.
            lifted=original[0].clone();lifted[2]+=.18
            relative=T.relative_pose_transform(lifted,original[1],base_pos,base_quat)
        self._ideal_held=obj;self._carry_relative=relative
        # Keep the grasped object's world orientation through visible hand
        # motion. Thin boards and plates otherwise turn vertical with the
        # wrist, making a later selected volume physically too small for them.
        self._carry_orientation=original[1].clone() if getattr(self,'demo_motion',False) else None
        self._carry_contents=contents;self._carry_dependencies=dependencies
        self._carry_particle_carrier=obj
        self._carry_particle_contents=self._grasp_particle_payload(obj)
        self._carry_filled_systems=[]
        if self._carry_particle_contents:
            from omnigibson.object_states import Filled
            self._carry_filled_systems=[system for system,_,_ in self._carry_particle_contents
                                        if Filled in obj.states and obj.states[Filled].get_value(system)]
        self._carry_record(status='attached',object=obj.name,contained_objects=[c.name for c,_ in contents],
                           contained_particles=sum(len(indices) for _,indices,_ in self._carry_particle_contents),
                           payload_relations=[{'child':c.name,'parent':p.name,'relation':kind} for c,p,kind in dependencies],
                           implementation='pose_projection_no_fixed_joint',original_position=original[0].tolist())
        self._carry_follow()
        with (self._anchored_operation(support_anchors)
              if support_anchors else self._anchored_operation()):
            for _ in range(min(6,max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        pos,quat=obj.get_position_orientation()
        if not bool(torch.isfinite(pos).all() and torch.isfinite(quat).all()):
            raise SkillError('physics_instability','Non-finite held pose',changed=True)
        return {'primitive':'grasp','implementation':'controlled_pose_carry','postcondition':'selected_object_held_and_finite',
                'physical_grasp':False,'fixed_joint_created':False,'contained_rigid_objects':len(contents)}

    def _verify_payload(self, dependencies):
        from omnigibson.object_states import Inside,OnTop
        for child,parent,kind in dependencies:
            state=Inside if kind=='Inside' else OnTop
            if state not in child.states or not child.states[state].get_value(parent):
                raise SkillError('postcondition_error','Carried payload lost its original support or containment after placement',changed=True)

    def _ideal_release(self,max_steps):
        if self._ideal_held is None:raise SkillError('empty_hand','No carried object')
        held=self._ideal_held
        contents=list(self._carry_contents)
        dependencies=list(self._carry_dependencies)
        self._carry_record(status='released',object=held.name)
        self._carry_detach()
        if getattr(self,'demo_motion',False):self._demo_arm=None
        with self._anchored_operation():
            for _ in range(min(30,max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                self._relocate_contents(held,contents)
        self._carry_particle_carrier=None;self._carry_particle_contents=[]
        self._restore_expected_fill(held,getattr(self,'_carry_filled_systems',[]))
        self._carry_filled_systems=[]
        if dependencies:self._verify_payload(dependencies)
        return {'primitive':'release','implementation':'controlled_carry_detach_and_settle',
                'physical_grasp':False,'preserved_carried_objects':len(contents)}
