"""Direct physical joint/base controls using official OmniGibson controllers.

No object grounding, GT navigation, pose projection, symbolic action, attachment,
or object-state setter is used by this executor. Scene evaluation stays private.
"""
import json
import math
from .omnigibson_rgb import RGBBackend
from ..omnigibson_backend import OmniGibsonBackend
from ..contracts import SkillError
from ..records import now


def number(value, low, high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low <= value <= high:
        raise SkillError('invalid_motor_command', f'Expected a finite number within [{low}, {high}]')
    return float(value)


def validate_motor(name, arguments, dimensions, dt):
    expected = {'base_velocity':{'x','y','yaw','steps'}, 'joint_delta':{'group','delta','steps'},
                'gripper':{'hand','opening','steps'}, 'hold':{'steps'}}
    if name not in expected or set(arguments) != expected[name]:
        raise SkillError('invalid_motor_command', 'Unknown primitive or incorrect arguments')
    steps=arguments['steps']
    if type(steps) is not int or not 1 <= steps <= 90:
        raise SkillError('invalid_motor_command', 'steps must be an integer from 1 to 90')
    if name=='base_velocity':
        x=number(arguments['x'],-.25,.25); y=number(arguments['y'],-.25,.25)
        if math.hypot(x,y) > .25+1e-9: raise SkillError('invalid_motor_command','Combined base speed exceeds 0.25 m/s')
        number(arguments['yaw'],-.5,.5)
    elif name=='joint_delta':
        group=arguments['group']; delta=arguments['delta']
        if group not in {'arm_left','arm_right','trunk'} or group not in dimensions:
            raise SkillError('invalid_motor_command','group must be arm_left, arm_right or trunk')
        if not isinstance(delta,list) or len(delta)!=dimensions[group]:
            raise SkillError('invalid_motor_command','delta must match the documented joint count')
        for value in delta:
            number(value,-.35,.35)
            if abs(value)/(steps*dt) > .5+1e-9:
                raise SkillError('invalid_motor_command','Increase steps: commanded joint speed exceeds 0.5 rad/s')
    elif name=='gripper':
        if arguments['hand'] not in {'left','right'}: raise SkillError('invalid_motor_command','hand must be left or right')
        number(arguments['opening'],0,1)
    return steps + (1 if name=='base_velocity' else 0)


class MotorBackend(RGBBackend):
    direct_motor=True

    def _configure_robot_controls(self, robot_cfg):
        robot_cfg.update(grasping_mode='physical',disable_grasp_handling=False)
        robot_cfg['controller_config']['base'].update(motor_type='velocity')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.ideal_carry=False
        self.motor_dt=float(self.og.sim.get_sim_step_dt())
        self.motor_joint_indices=self.robot.controller_joint_idx
        self.motor_action_indices=self.robot.controller_action_idx
        q=self.robot.get_joint_positions()
        self.motor_targets={name:q[index].clone() for name,index in self.motor_joint_indices.items() if name!='base'}
        self.motor_dimensions={name:len(index) for name,index in self.motor_joint_indices.items()}
        if any(len(self.motor_action_indices[n])!=len(index) for n,index in self.motor_joint_indices.items()):
            raise RuntimeError('Direct joint executor requires matching controller command and joint dimensions')
        self.motor_terminated=False
        (self.output/'motor_contract.json').write_text(json.dumps(self.motor_contract(),indent=2)+'\n')

    def motor_contract(self):
        limits=self.robot.control_limits['position']
        joints=list(self.robot.joints)
        groups={}
        for name,index in self.motor_joint_indices.items():
            if name=='base': continue
            groups[name]={'count':len(index), 'joints':[joints[int(i)] for i in index],
                          'lower':[float(limits[0][i]) for i in index],
                          'upper':[float(limits[1][i]) for i in index]}
        return {'protocol':'rgb_physical_joint_program_v1','control_dt_seconds':self.motor_dt,
                'groups':groups,'frame':'robot body: x forward, y left, positive yaw left',
                'functions':{
                    'base_velocity(x=0, y=0, yaw=0, steps=30)':'m/s and rad/s; planar speed <=0.25, |yaw|<=0.5. Executes steps plus ONE counted zero-velocity braking step.',
                    'joint_delta(group, delta, steps=30)':'arm_left/right: 7 joints; trunk: 4 joints. Relative radians from measured joints at entry, linearly ramped fixed target. |delta|<=0.35; |delta|/(steps*dt)<=0.5. No IK or collision planning.',
                    'gripper(hand, opening, steps=30)':'left/right, opening in [0 closed,1 open]; joint-position ramp. Physical contacts only. No grasp/retention guarantee.',
                    'hold(steps=15)':'zero base velocity, retain last commanded arm/trunk/gripper targets.',
                    'observe()':'fresh four-camera RGB metadata; images displayed when the MCP call returns. No joint, pose, depth, target or evaluator truth.'},
                'steps_per_primitive':[1,90],'max_program_steps':240,'max_program_motor_calls':16,
                'program_language':'def run() and helpers; local assignments, if, for/range, while, break, return; numeric arithmetic, JSON lists/dicts/indexing; abs/min/max/round/len/int/float/sin/cos/sqrt. No imports/attributes/files. No variables persist between calls.',
                'return_contract':'run returns {status: completed|partial|blocked, reason: nonempty string}; completed means program completion, not task success.',
                'execution_feedback':'command acceptance and step counts only; inspect RGB for physical effects',
                'observation_mode':'rgb_only','grasping_mode':'physical','semantic_skills_available':False}

    def _step(self, action):
        # Intentionally bypass RGBBackend's ideal carry/base/object restoration.
        obs,reward,terminated,truncated,info=self.env.step(action)
        self.steps+=1
        self.motor_terminated=bool(terminated or truncated)
        for metric in self.evaluator.metrics:
            metric.step(self.env,action,obs,reward,terminated,truncated,info)
        self._video_frame('env_step')
        finite=bool(self.torch.isfinite(self.robot.get_joint_positions()).all())
        with (self.output/'motor_steps.jsonl').open('a') as stream:
            stream.write(json.dumps({'at':now(),'sim_step':self.steps,'action':action.detach().cpu().tolist(),
                'joint_positions':self.robot.get_joint_positions().detach().cpu().tolist(),
                'terminated':self.motor_terminated,'finite_robot_joints':finite,'audience':'offline_audit_only'})+'\n')
        if not finite: raise SkillError('physics_instability','Nonfinite robot state',changed=True)
        if self.motor_terminated: raise SkillError('simulation_ended','Simulation terminated; finish the episode',changed=True)

    def execute_motor(self,name,arguments,max_steps):
        count=validate_motor(name,arguments,self.motor_dimensions,self.motor_dt)
        if count>max_steps: raise SkillError('budget_exhausted','Motor command exceeds remaining step budget')
        if self.motor_terminated: raise SkillError('simulation_ended','Simulation has already ended')
        torch=self.torch; q=self.robot.get_joint_positions()
        target=None; group=None
        if name=='joint_delta':
            group=arguments['group']; index=self.motor_joint_indices[group]
            start=q[index].clone();target=start+torch.tensor(arguments['delta'],device=q.device)
        elif name=='gripper':
            group='gripper_'+arguments['hand'];index=self.motor_joint_indices[group]
            start=q[index].clone();lo,hi=self.robot.control_limits['position']
            target=lo[index]+arguments['opening']*(hi[index]-lo[index])
        if target is not None:
            lo,hi=self.robot.control_limits['position']
            if not bool(torch.isfinite(target).all()) or not bool(((target>=lo[index])&(target<=hi[index])).all()):
                raise SkillError('invalid_motor_command','Target exceeds robot joint limits; choose a smaller delta')
        before=self.steps
        for i in range(count):
            if target is not None:
                self.motor_targets[group]=start+(target-start)*min(1.,(i+1)/arguments['steps'])
            action=torch.zeros(self.robot.action_dim,device=q.device)
            for name_group,value in self.motor_targets.items(): action[self.motor_action_indices[name_group]]=value
            if name=='base_velocity' and i<arguments['steps']:
                action[self.motor_action_indices['base']]=torch.tensor([arguments['x'],arguments['y'],arguments['yaw']],device=q.device)
            self._step(action)
        return {'primitive':name,'status':'command_executed','sim_steps':self.steps-before,
                'verification':'command_execution_only; inspect RGB for actual motion and task effects'}

    def execute_visual(self,*args,**kwargs):
        raise SkillError('unsupported_interface','Semantic actions are disabled for the motor experiment')

    def provenance(self):
        result=OmniGibsonBackend.provenance(self)
        result.pop('inside_placement',None)
        result.update(executor='official_joint_and_holonomic_velocity_controllers',
            control_protocol='rgb_physical_joint_program_v1',grasping_mode='physical',
            pose_projection=False,object_state_setters=False,symbolic_primitives=False,
            gt_navigation=False,model_visible_truth=False,robot_camera_views=['front','back','left','right'],
            stock_wrist_cameras_enabled=False,record_video=self.record_video,
            control_contract=self.motor_contract())
        return result

    def evaluate(self):
        result=OmniGibsonBackend.evaluate(self)
        for key in ('ideal_navigation_distance_m','inside_placement','volume_sampling_physics_steps'):
            result.pop(key,None)
        result.update(protocol='rgb_physical_joint_program_v1',
                      time_metric_scope='all physical control steps; excludes LLM wall time',
                      official_submission_eligible=False)
        return result
