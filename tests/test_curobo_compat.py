import unittest
from manipulation_agent.executors.curobo_compat import trajectory_evaluator_device


class DeviceDefaultTests(unittest.TestCase):
    def test_omitted_device_uses_assigned_gpu_and_explicit_device_is_preserved(self):
        class Config:
            @staticmethod
            def from_basic(dof,min_dt=.01,tensor_args='cuda:0'):
                return dof,min_dt,tensor_args
        original=vars(Config)['from_basic']
        with self.assertRaises(ValueError):
            with trajectory_evaluator_device(Config,'cuda:3'):
                self.assertEqual(Config.from_basic(dof=7),(7,.01,'cuda:3'))
                self.assertEqual(Config.from_basic(7,.02,'cuda:4'),(7,.02,'cuda:4'))
                self.assertEqual(Config.from_basic(3,tensor_args='cuda:2'),(3,.01,'cuda:2'))
                raise ValueError('warmup failed')
        self.assertIs(vars(Config)['from_basic'],original)
        self.assertEqual(Config.from_basic(7),(7,.01,'cuda:0'))
