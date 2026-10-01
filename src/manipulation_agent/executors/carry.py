"""Explicit ideal carry without a dynamic robot/object fixed joint.

Object identity comes from the selected RGB pixel only. This is a research
motor protocol, not physical grasp control. Ordinary environment collisions
and gravity remain enabled; held poses are projected around each control step.
"""
from contextlib import contextmanager
import json
from ..contracts import SkillError
from ..records import now

class ControlledCarry:
    @contextmanager
    def _anchored_operation(self, obj=None):
        previous=self._base_target;old_object=getattr(self,'_object_anchor',None)
        pos,quat=self.robot.get_position_orientation();joints=self.robot.get_joint_positions()
        indices=self.torch.tensor([i for i in range(len(joints)) if i not in self.robot.base_idx.tolist()],device=joints.device)
        self._base_target={'position':pos.clone(),'orientation':quat.clone(),'indices':indices,
                           'posture':joints[indices].clone(),'held':None,'relative':None}
        if obj is not None:self._object_anchor=(obj,tuple(v.clone() for v in obj.get_position_orientation()))
        try:yield
        finally:self._base_target=previous;self._object_anchor=old_object

    def _restore_object_anchor(self):
        if getattr(self,'_object_anchor',None) is not None:
            obj,pose=self._object_anchor;obj.set_position_orientation(*pose);obj.keep_still()

    def _ideal_state_action(self, primitive, obj, max_steps):
        from omnigibson.object_states import Open,ToggledOn
        state=Open if primitive in {'open','close'} else ToggledOn
        wanted=primitive in {'open','toggle_on'}
        if state not in obj.states:raise SkillError('pre_condition_error','Selected object does not support this operation')
        with self._anchored_operation(obj):
            # Upstream symbolic OPEN samples a random opening and returns early
            # when already ajar. Fully open/close all annotated joints instead.
            accepted=obj.states[state].set_value(wanted,**({'fully':True} if state is Open else {}))
            if not accepted:raise SkillError('execution_error','Object state setter rejected the selected operation',changed=True)
            for _ in range(min(30,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if bool(obj.states[state].get_value())!=wanted:
                raise SkillError('postcondition_error','Requested object operation did not remain stable',changed=True)
        return {'primitive':primitive,'implementation':'official_state_setter_with_root_anchor',
                'fully_open_or_closed':state is Open,'postcondition':'official_state_after_settling'}

    def _get_held(self):
        return self._ideal_held if self.ideal_carry else self.primitives._get_obj_in_hand()

    def _carry_record(self, **data):
        with (self.output/'carry_diagnostics.jsonl').open('a') as f:
            f.write(json.dumps({'at':now(),'env_step':self.steps,'audience':'executor_private',**data})+'\n')

    def _carry_follow(self):
        if not self.ideal_carry or self._ideal_held is None:return
        from omnigibson.utils import transform_utils as T
        pose=T.pose_transform(*self.robot.get_position_orientation(),*self._carry_relative)
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
            self._ideal_held=None;self._carry_relative=None;self._carry_contents=[]

    @contextmanager
    def _placement_context(self):
        from .placement import placement_transaction
        held=getattr(self,'_ideal_held',None);relative=getattr(self,'_carry_relative',None);contents=list(getattr(self,'_carry_contents',[]))
        try:
            with placement_transaction(self.og.sim,self._placement_record):yield
        except Exception:
            self._ideal_held=held;self._carry_relative=relative;self._carry_contents=contents
            if held is not None:self._carry_follow()
            raise

    def _ideal_grasp(self,obj,max_steps):
        from omnigibson.utils import transform_utils as T
        from omnigibson.object_states import Inside
        torch=self.torch
        if self._ideal_held is obj:return {'primitive':'grasp','postcondition':'selected_object_already_held'}
        if self._ideal_held is not None:raise SkillError('hand_occupied','A carry relationship already exists')
        if obj.fixed_base:raise SkillError('fixed_object','The selected object has a fixed base')
        # Preserve contained rigid objects using a local spatial broad phase,
        # without consulting task scope or evaluator predicates.
        original=obj.get_position_orientation();lo,hi=obj.aabb;contents=[]
        for child in self.env.scene.objects:
            if child in (obj,self.robot) or getattr(child,'fixed_base',True) or not hasattr(child,'states') or Inside not in child.states:continue
            center=child.get_position_orientation()[0]
            if bool(((center>=lo)&(center<=hi)).all()) and child.states[Inside].get_value(obj):
                contents.append((child,T.relative_pose_transform(*child.get_position_orientation(),*original)))
        base_pos,base_quat=self.robot.get_position_orientation()
        # Lift at the selected object's XY first. Do not teleport its origin
        # into the robot's palm/collision geometry, as the symbolic grasp does.
        lifted=original[0].clone();lifted[2]+=.18
        relative=T.relative_pose_transform(lifted,original[1],base_pos,base_quat)
        self._ideal_held=obj;self._carry_relative=relative;self._carry_contents=contents
        self._carry_record(status='attached',object=obj.name,contained_objects=[c.name for c,_ in contents],
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

    def _ideal_release(self,max_steps):
        if self._ideal_held is None:raise SkillError('empty_hand','No carried object')
        self._carry_record(status='released',object=self._ideal_held.name)
        self._carry_detach()
        for _ in range(min(30,max_steps)):self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        return {'primitive':'release','implementation':'controlled_carry_detach_and_settle','physical_grasp':False}
