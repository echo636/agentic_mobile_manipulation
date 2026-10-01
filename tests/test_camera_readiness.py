import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

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
        b._position_rig=Mock();b.og=SimpleNamespace(sim=SimpleNamespace(render=Mock(),step=Mock(side_effect=AssertionError('physics advanced'))))
        class Sensor:
            def get_obs(self):
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


if __name__=='__main__':unittest.main()
