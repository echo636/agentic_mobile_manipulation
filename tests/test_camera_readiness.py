import json
from pathlib import Path
import tempfile
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np

from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class Tensor:
    def __init__(self,value):self.value=np.asarray(value);self.shape=self.value.shape
    def clone(self):return Tensor(self.value.copy())
    def detach(self):return self
    def cpu(self):return self
    def numpy(self):return self.value


class CameraReadiness(unittest.TestCase):
    def backend(self,output,ready_after):
        b=RGBBackend.__new__(RGBBackend);b.output=Path(output);b.steps=0;b.image_size=8
        b._position_rig=Mock();b.og=SimpleNamespace(sim=SimpleNamespace(render=Mock(),step=Mock(side_effect=AssertionError('physics advanced')),
            current_time=0.,current_time_step_index=0))
        b.robot=SimpleNamespace(get_position_orientation=lambda:(Tensor([1.,2.,0.]),Tensor([0.,0.,0.,1.])),
                                get_joint_positions=lambda:Tensor([.2,.4]))
        b._completed_render=0;b._test_require_barrier=True
        def complete(**kwargs):
            self.assertEqual(kwargs,dict(delta_time=0.,pause_timeline=False,wait_for_render=True,rt_subframes=1))
            b._completed_render=b.og.sim.render.call_count
        b._test_orchestrator=SimpleNamespace(step=Mock(side_effect=complete))
        lazy=ModuleType('omnigibson.lazy')
        lazy.omni=SimpleNamespace(replicator=SimpleNamespace(core=SimpleNamespace(orchestrator=b._test_orchestrator)),
                                 timeline=SimpleNamespace(get_timeline_interface=lambda:SimpleNamespace(get_current_time=lambda:0.)))
        og=ModuleType('omnigibson');og.lazy=lazy
        mock_import=patch.dict(sys.modules,{'omnigibson':og,'omnigibson.lazy':lazy})
        mock_import.start();self.addCleanup(mock_import.stop)
        class Sensor:
            def get_obs(self):
                if b._test_require_barrier and b._completed_render != b.og.sim.render.call_count:
                    raise AssertionError('Readback consumed a previous rendered pose')
                return {'rgb':Tensor(np.full((8,8,4),b.og.sim.render.call_count)),
                        'depth_linear':Tensor(np.ones((8,8)))},{}
            @property
            def intrinsic_matrix(self):
                if b.og.sim.render.call_count<ready_after:
                    raise AssertionError('intrinsic matrix for sensor: mas_rgb_right is degenerate!')
                return Tensor([[4,0,4],[0,4,4],[0,0,1]])
        b.rig={v:Sensor() for v in ('front','back','left','right')}
        return b

    def test_unready_calibration_retries_all_views_with_frozen_physics(self):
        with tempfile.TemporaryDirectory() as d:
            b=self.backend(d,ready_after=7)
            pixels=b._render_rgb_views(require_calibration=True)
            self.assertEqual(set(pixels),set(b.rig));self.assertEqual(set(b._sensor_intrinsics),set(b.rig))
            self.assertTrue(all(np.all(value==7) for value in pixels.values()))
            b.og.sim.step.assert_not_called();self.assertEqual(b.steps,0)
            record=json.loads((Path(d)/'camera_readiness.jsonl').read_text())
            self.assertEqual(record['status'],'passed');self.assertEqual(record['render_attempts'],4)
            self.assertEqual(len(record['retries']),3)
            barriers=[json.loads(line) for line in (Path(d)/'annotation_barriers.jsonl').read_text().splitlines()]
            self.assertEqual(len(barriers),4)
            self.assertTrue(all(r['before']==r['after'] and r['status']=='passed' for r in barriers))

    def test_permanent_invalid_calibration_fails_with_bounded_diagnostics(self):
        with tempfile.TemporaryDirectory() as d:
            b=self.backend(d,ready_after=1000)
            with self.assertRaisesRegex(RuntimeError,'Camera render products did not become ready'):
                b._render_rgb_views(require_calibration=True)
            self.assertEqual(b.og.sim.render.call_count,33)
            b.og.sim.step.assert_not_called()
            record=json.loads((Path(d)/'camera_readiness.jsonl').read_text())
            self.assertEqual(record['status'],'failed');self.assertEqual(len(record['retries']),30)
            self.assertFalse(hasattr(b,'_sensor_packets'))

    def test_barrier_time_or_joint_change_cannot_be_silently_accepted(self):
        for change in ('time','joints'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                b=self.backend(d,ready_after=1)
                def advances(**_):
                    if change=='time':b.og.sim.current_time=.1
                    else:b.robot.get_joint_positions=lambda:Tensor([.3,.4])
                b._test_orchestrator.step.side_effect=advances
                with self.assertRaisesRegex(RuntimeError,'Replicator capture changed simulation state'):
                    b._render_rgb_views(require_calibration=True)
                self.assertFalse(hasattr(b,'_sensor_packets'))
                record=json.loads((Path(d)/'annotation_barriers.jsonl').read_text())
                self.assertEqual(record['status'],'failed')
                self.assertIn('sim_time' if change=='time' else 'joints',record['changed_fields'])

    def test_video_only_readback_does_not_take_annotation_barrier(self):
        with tempfile.TemporaryDirectory() as d:
            b=self.backend(d,ready_after=1);b._test_require_barrier=False
            b._render_rgb_views(require_calibration=False)
            b._test_orchestrator.step.assert_not_called()
            self.assertFalse((Path(d)/'annotation_barriers.jsonl').exists())


if __name__=='__main__':unittest.main()
