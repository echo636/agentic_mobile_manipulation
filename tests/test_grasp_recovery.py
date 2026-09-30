import math
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.omnigibson_backend import OmniGibsonBackend
from manipulation_agent.observations.boundary import public_execution_error

class GraspRecoveryTests(unittest.TestCase):
    def backend(self, fail_restore=False):
        backend=RGBBackend.__new__(RGBBackend);state={'q':[0.,0.,0.,1.]};events=[]
        def load(saved,**kw):
            if fail_restore:raise RuntimeError('Restoration failed')
            state.update(saved)
        backend.og=NS(sim=NS(dump_state=lambda **kw:dict(state),load_state=load))
        backend.robot=NS(get_position_orientation=lambda:([0.,0.,0.],state['q']))
        backend.torch=NS(isfinite=lambda values:NS(all=lambda:all(math.isfinite(v) for v in values)))
        backend._placement_record=events.append
        return backend,state,events

    def test_nan_grasp_is_failed_action_but_restores_pose(self):
        b,state,events=self.backend()
        def crash(*args):
            state['q']=[float('nan')]*4
            raise AssertionError('orientation nan is not a unit quaternion')
        with patch.object(OmniGibsonBackend,'execute',side_effect=crash):
            with self.assertRaises(SkillError) as caught:b.execute('grasp','object',20)
        self.assertEqual(caught.exception.code,'physics_instability')
        feedback=public_execution_error(caught.exception)
        self.assertEqual(feedback['code'],'physics_instability')
        self.assertNotIn('quaternion',feedback['message'])
        self.assertEqual(state['q'],[0.,0.,0.,1.])
        self.assertEqual(events[-1]['status'],'rolled_back')
        self.assertEqual(b.frames_revision,-1)

    def test_failed_restore_is_never_reported_as_recovered(self):
        b,_,_=self.backend(fail_restore=True)
        with patch.object(OmniGibsonBackend,'execute',side_effect=AssertionError('nan quaternion')):
            with self.assertRaisesRegex(RuntimeError,'Restoration failed'):b.execute('grasp','object',20)

    def test_ordinary_action_error_preserves_its_original_category(self):
        b,_,_=self.backend()
        with patch.object(OmniGibsonBackend,'execute',side_effect=SkillError('out_of_reach','Too far')):
            with self.assertRaises(SkillError) as caught:b.execute('grasp','object',20)
        self.assertEqual(caught.exception.code,'out_of_reach')
