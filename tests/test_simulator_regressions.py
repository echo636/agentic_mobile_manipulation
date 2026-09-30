import math
import importlib.util
from pathlib import Path
import random
from types import SimpleNamespace
import tempfile
import unittest

from manipulation_agent.observations.rig import look_at_orientation
from manipulation_agent.omnigibson_backend import normalize_embedded_robot
from manipulation_agent.executors.omnigibson_rgb import RGBBackend

spec=importlib.util.spec_from_file_location('observation_validator',Path(__file__).resolve().parents[1]/'scripts/validate_async_observation.py')
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)


class SimulatorRegressions(unittest.TestCase):
    def test_tilted_robot_axis_audit_uses_robot_frame(self):
        angle=.3; sx,cx=math.sin(angle/2),math.cos(angle/2)
        base=[sx,0,0,cx]
        for direction in ([1,0,-.3],[0,1,-.3],[-1,0,-.3],[0,-1,-.3]):
            x,y,z,w=look_at_orientation([0,0,0],direction)
            world=[cx*x+sx*w,cx*y-sx*z,cx*z+sx*y,cx*w-sx*x]
            relative=validator.base_relative_forward(world,base)
            n=math.sqrt(sum(v*v for v in direction))
            for a,b in zip(relative,direction):self.assertAlmostEqual(a,b/n)

    def test_camera_tracks_target_including_vertical_and_half_turn(self):
        rng=random.Random(7)
        targets=[[0,0,1],[0,0,-1],[1,0,0],[-1,0,0],[0,1,0],[0,-1,0]]
        targets += [[rng.uniform(-10,10) for _ in range(3)] for _ in range(500)]
        for target in targets:
            x,y,z,w=look_at_orientation([0,0,0],target)
            self.assertAlmostEqual(x*x+y*y+z*z+w*w,1)
            forward=[-2*(x*z+y*w),-2*(y*z-x*w),-(1-2*(x*x+y*y))]
            norm=math.sqrt(sum(t*t for t in target))
            for a,b in zip(forward,target):self.assertAlmostEqual(a,b/norm)

    def test_bad_camera_direction_rejected(self):
        for target in ([0,0,0],[float('nan'),1,0],[float('inf'),0,1]):
            with self.assertRaises(ValueError):look_at_orientation([0,0,0],target)

    def test_spectator_failure_does_not_abort_robot_video(self):
        with tempfile.TemporaryDirectory() as folder:
            b=RGBBackend.__new__(RGBBackend);b.output=Path(folder);b.steps=13
            calls=[];b.video=SimpleNamespace(closed=False,append=lambda *args:calls.append(args))
            def broken():raise AssertionError('torch compile regression fixture')
            b._position_spectator=broken;b._render_rgb_views=lambda **kw:{'front':'actual frame'}
            b._video_frame('env_step')
            self.assertEqual(calls,[({'front':'actual frame'},13,'env_step')])
            self.assertIn('last_camera_pose',(b.output/'recording_warnings.jsonl').read_text())

    def test_legacy_robot_migration_retains_identity_and_asset_check(self):
        entry={'class_module':'omnigibson.robots.r1pro','class_name':'R1Pro',
               'args':{'name':'robot_original','expected_file_hash':'original-hash','default_reset_mode':'untuck'}}
        data={'objects_info':{'init_info':{'robot_original':entry}}}
        self.assertEqual(len(normalize_embedded_robot(data)),1)
        self.assertEqual(entry['class_name'],'Robot')
        self.assertEqual(entry['args'],{'name':'robot_original','expected_file_hash':'original-hash',
                                       'default_reset_mode':'untuck','model':'r1pro'})
        self.assertEqual(normalize_embedded_robot(data),[])


if __name__=='__main__':unittest.main()
