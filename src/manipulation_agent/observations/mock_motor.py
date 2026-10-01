"""Motor contract fixture; no physical or task-success evidence."""
from .mock_rgb import MockRGBBackend
from ..executors.motor import validate_motor
from ..contracts import SkillError


class MockMotorBackend(MockRGBBackend):
    def __init__(self,output):
        super().__init__(output)
        self.commands=[]

    def motor_contract(self):
        return {'protocol':'mock_motor_contract_only','functions':['base_velocity','joint_delta','gripper','hold','observe']}

    def execute_motor(self,name,arguments,max_steps):
        count=validate_motor(name,arguments,{'arm_left':7,'arm_right':7,'trunk':4},1/30)
        if count>max_steps: raise SkillError('budget_exhausted','Motor command exceeds remaining step budget')
        self.commands.append((name,arguments));self.steps+=count
        return {'primitive':name,'status':'command_executed','sim_steps':count,'verification':'mock_only'}

    def evaluate(self):
        return {'task_success':False,'official_task_success':False,'protocol':'mock_motor_contract_only','official_submission_eligible':False}

    def provenance(self):
        return {'name':'mock_motor','observation_mode':'rgb_only','validation_level':'cpu_contract_only'}
