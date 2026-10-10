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
        if obj is not None:self._object_anchor=(obj,tuple(v.clone() for v in obj.get_position_orientation()))
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
            obj,pose=self._object_anchor;obj.set_position_orientation(*pose);obj.keep_still()

    def _ideal_state_action(self, primitive, obj, max_steps):
        from omnigibson.object_states import Open,ToggledOn
        from .placement import placement_transaction
        state=Open if primitive in {'open','close'} else ToggledOn
        wanted=primitive in {'open','toggle_on'}
        if state not in obj.states:raise SkillError('pre_condition_error','Selected object does not support this operation')
        # Open.set_value teleports articulated joints. Drawer contents must move
        # with their supporting volume, not be left at the previous world pose.
        payload=self._container_payload(obj) if state is Open else []
        repaired_contents=0
        stabilized=getattr(self,'_stabilized_open_joints',{})
        prior=stabilized.get(obj) if state is Open else None
        try:
            with self._anchored_operation(obj),placement_transaction(self.og.sim,self._placement_record):
                # Upstream symbolic OPEN samples a random opening and returns early
                # when already ajar. Fully open/close all annotated joints instead.
                accepted=obj.states[state].set_value(wanted,**({'fully':True} if state is Open else {}))
                if not accepted:raise SkillError('execution_error','Object state setter rejected the selected operation',changed=True)
                if state is Open:
                    if wanted:
                        stabilized.pop(obj,None)
                    else:
                        joints=obj.states[Open].relevant_joints_info[1]
                        stabilized[obj]=[(joint,float(joint.get_state()[0])) for joint in joints]
                    self._stabilized_open_joints=stabilized
                self._relocate_container_payload(payload)
                for _ in range(min(30,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
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
        for obj,relative in self._carry_contents:
            obj.set_position_orientation(*T.pose_transform(*pose,*relative));obj.keep_still()

    def _relocate_contents(self, held, contents):
        from omnigibson.utils import transform_utils as T
        for obj,relative in contents:
            obj.set_position_orientation(*T.pose_transform(*held.get_position_orientation(),*relative));obj.keep_still()

    def _carry_detach(self):
        if not self.ideal_carry:
            for arm in self.robot.arm_names:self.robot.release_grasp_immediately(arm=arm)
        else:
            self._ideal_held=None;self._carry_relative=None;self._carry_orientation=None;self._carry_contents=[];self._carry_dependencies=[]

    @contextmanager
    def _placement_context(self,target=None):
        from .placement import placement_transaction
        held=getattr(self,'_ideal_held',None);relative=getattr(self,'_carry_relative',None)
        orientation=getattr(self,'_carry_orientation',None);contents=list(getattr(self,'_carry_contents',[]))
        dependencies=list(getattr(self,'_carry_dependencies',[]))
        try:
            with self._anchored_operation(target),placement_transaction(self.og.sim,self._placement_record):yield
        except Exception:
            self._ideal_held=held;self._carry_relative=relative;self._carry_orientation=orientation
            self._carry_contents=contents;self._carry_dependencies=dependencies
            if held is not None:self._carry_follow()
            raise

    def _ideal_grasp(self,obj,max_steps):
        from omnigibson.utils import transform_utils as T
        from omnigibson.object_states import Inside,OnTop,Touching
        torch=self.torch
        if self._ideal_held is obj:return {'primitive':'grasp','postcondition':'selected_object_already_held'}
        if self._ideal_held is not None:raise SkillError('hand_occupied','A carry relationship already exists')
        if obj.fixed_base:raise SkillError('fixed_object','The selected object has a fixed base')
        # Preserve both contained objects and supported objects (e.g. food on a
        # plate), including nested payloads. Fixed scene objects never follow.
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
        # Keep an assembly level while the visible hand moves and rotates.
        # Otherwise a plate carrying food can turn vertical during navigation,
        # invalidating its support relation before the destination is reached.
        self._carry_orientation=original[1].clone() if contents and getattr(self,'demo_motion',False) else None
        self._carry_contents=contents;self._carry_dependencies=dependencies
        self._carry_record(status='attached',object=obj.name,contained_objects=[c.name for c,_ in contents],
                           payload_relations=[{'child':c.name,'parent':p.name,'relation':kind} for c,p,kind in dependencies],
                           implementation='pose_projection_no_fixed_joint',original_position=original[0].tolist())
        self._carry_follow()
        with self._anchored_operation():
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
        self._carry_record(status='released',object=self._ideal_held.name)
        self._carry_detach()
        if getattr(self,'demo_motion',False):self._demo_arm=None
        with self._anchored_operation():
            for _ in range(min(30,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        return {'primitive':'release','implementation':'controlled_carry_detach_and_settle','physical_grasp':False}
