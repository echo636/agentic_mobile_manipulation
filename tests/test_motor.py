import json
from pathlib import Path
import tempfile
import unittest
from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.motor_program import MotorProgram
from manipulation_agent.executors.motor import validate_motor
from manipulation_agent.observations.mock_motor import MockMotorBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.tools import tool_specs


class ProgramTests(unittest.TestCase):
    def test_composition_branches_and_helpers(self):
        calls=[]
        program='''def nudge(x):
    return move(x)
def run():
    total = 0
    for i in range(3):
        total += nudge(i)
    if total == 3:
        return {"status":"completed", "reason":"commands complete", "sum": total}
    return {"status":"blocked", "reason":"unexpected feedback"}
'''
        result=MotorProgram(program,{'move':lambda x:calls.append(x) or x}).run()
        self.assertEqual(result['sum'],3);self.assertEqual(calls,[0,1,2])

    def test_forbidden_access_is_rejected_before_any_motion(self):
        for statement in ['import os','x = observe.__globals__','x = open("secret")',
                          'x = [i for i in range(3)]','x = lambda: 0','x = (1).__class__']:
            with self.subTest(statement=statement):
                calls=[]
                source='def run():\n    move()\n    '+statement+'\n    return {"status":"completed","reason":"x"}'
                # Unknown names are evaluated at runtime; unavailable host calls
                # cannot be invoked but may follow already-executed commands.
                with self.assertRaises(SkillError): MotorProgram(source,{'move':lambda:calls.append(1)}).run()
                if 'open(' not in statement:self.assertEqual(calls,[])

    def test_unbounded_computation_stops(self):
        with self.assertRaisesRegex(SkillError,'budget'):
            MotorProgram('def run():\n    while True:\n        pass',{},max_operations=30).run()
        with self.assertRaises(SkillError): MotorProgram('def run():\n    return run()',{}).run()

    def test_large_allocation_and_nonfinite_values_rejected(self):
        for value in ['[0] * 1000000000','"a" * 1000000000','1e999','range(1000000000)']:
            with self.subTest(value=value),self.assertRaises(SkillError):
                MotorProgram('def run():\n    x = '+value+'\n    return {"status":"completed","reason":"x"}',{}).run()

    def test_unknown_call_cannot_reach_python_builtins(self):
        for name in ['eval','exec','open','getattr','globals','__import__']:
            with self.subTest(name=name),self.assertRaises(SkillError):
                MotorProgram('def run():\n    '+name+'("x")',{}).run()


class MotorHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'run';self.rec=Recorder(self.path,{'backend':'mock','observation_mode':'rgb_only'})
        self.backend=MockMotorBackend(self.path)
        self.h=VisionHarness(self.backend,self.rec,Budget(max_actions=20,max_sim_steps=500),profile='motor')

    def program(self,body,request='code',revision=None):
        return self.h.call('execute_code',{'revision':self.h.revision if revision is None else revision,
            'program':'def run():\n'+''.join('    '+line+'\n' for line in body)},request)

    def test_profile_isolation(self):
        self.assertEqual(set(self.h.catalog),{'observe','describe_controls','execute_code','finish','start_observation','get_observation','cancel_observation'})
        for profile in ['minimal','skills','workflow']:
            self.assertNotIn('execute_code',{t['name'] for t in tool_specs(profile)})
        self.assertEqual(self.h.call('act',{},'bad')['error']['code'],'unknown_tool')

    def test_multiple_motions_and_idempotency(self):
        args=['base_velocity(x=0.1, steps=10)','gripper("right", 0, steps=10)',
              'return {"status":"completed","reason":"check RGB"}']
        result=self.program(args,revision=0)
        self.assertTrue(result['ok']);self.assertEqual(self.backend.steps,21)
        self.assertEqual(len(result['observation']['images']),4)
        self.assertEqual(self.program(args,revision=0),result)
        self.assertEqual(self.backend.steps,21)
        self.assertFalse(self.rec.run['task_success'])

    def test_partial_error_retains_motion_and_fresh_images(self):
        before=self.h.snapshot['images'][0]['image_ref']
        result=self.program(['hold(5)','x = 1 / 0'])
        self.assertFalse(result['ok']);self.assertEqual(self.backend.steps,5)
        self.assertTrue(result['error']['world_may_have_changed'])
        self.assertNotEqual(result['observation']['images'][0]['image_ref'],before)

    def test_program_step_budget_no_overshoot(self):
        result=self.program(['for i in range(10):','    hold(90)','return {"status":"completed","reason":"x"}'])
        self.assertFalse(result['ok']);self.assertEqual(self.backend.steps,180)
        self.assertEqual(result['error']['code'],'budget_exhausted')

    def test_stale_revision_cannot_move(self):
        result=self.program(['hold(1)'],revision=99)
        self.assertFalse(result['ok']);self.assertEqual(self.backend.steps,0)

    def test_observation_budget_and_no_truth(self):
        result=self.program(['for i in range(100):','    observe()'])
        self.assertEqual(result['error']['code'],'program_limit')
        self.assertEqual(set(result['observation']),{'images','observation_mode','capture','revision'})
        self.assertEqual(self.backend.steps,0)

    def test_brake_step_consumes_budget(self):
        with self.assertRaises(SkillError):
            self.backend.execute_motor('base_velocity',{'x':.1,'y':0,'yaw':0,'steps':90},90)
        self.assertEqual(self.backend.steps,0)

    def test_joint_dimension_speed_and_base_norm_checked(self):
        for name,args in [('joint_delta',dict(group='arm_right',delta=[.1],steps=30)),
                          ('joint_delta',dict(group='arm_right',delta=[.3]*7,steps=1)),
                          ('base_velocity',dict(x=.25,y=.25,yaw=0,steps=30)),
                          ('gripper',dict(hand='right',opening=float('nan'),steps=30))]:
            with self.subTest(name=name),self.assertRaises(SkillError):
                validate_motor(name,args,{'arm_right':7},1/30)

    def test_finish_does_not_return_independent_score(self):
        result=self.h.call('finish',{'outcome':'achieved','reason':'agent claim'},'finish')
        self.assertTrue(result['closed']);self.assertNotIn('task_success',result)
        self.assertIs(self.rec.run['task_success'],False)


if __name__=='__main__': unittest.main()
