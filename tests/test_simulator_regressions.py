import math
import json
import importlib.util
from pathlib import Path
import random
from types import SimpleNamespace
import tempfile
import unittest

from manipulation_agent.observations.rig import look_at_orientation
from manipulation_agent.omnigibson_backend import normalize_embedded_robot, select_compatible_scene
from manipulation_agent.executors.omnigibson_rgb import RGBBackend

spec=importlib.util.spec_from_file_location('observation_validator',Path(__file__).resolve().parents[1]/'scripts/validate_async_observation.py')
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)


class SimulatorRegressions(unittest.TestCase):
    def test_stale_partial_scene_uses_matching_supplied_full_template(self):
        with tempfile.TemporaryDirectory() as folder:
            partial=Path(folder)/'template-partial_rooms.json';full=Path(folder)/'template.json'
            instance=Path(folder)/'instance.json'
            def scene(key,name):return {'metadata':{'task':{'inst_to_name':{key:name}}},
                                       'objects_info':{'init_info':{name:{}}}}
            partial.write_text(json.dumps(scene('firewood.n.01_1','firewood_1')))
            full.write_text(json.dumps(scene('plywood.n.01_1','plywood_1')))
            instance.write_text(json.dumps({'plywood.n.01_1':{},'robot_poses':{}}))
            original=partial.read_bytes()
            selected,data,rejected=select_compatible_scene(partial,instance)
            self.assertEqual(selected,full)
            self.assertEqual(rejected[0]['missing_instance_bindings'],['plywood.n.01_1'])
            self.assertEqual(partial.read_bytes(),original)
            full.unlink()
            with self.assertRaisesRegex(ValueError,'incompatible'):select_compatible_scene(partial,instance)

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

    def test_legacy_controller_goals_are_not_loaded_into_different_controllers(self):
        physical={'joint_pos':[1,2,3],'joint_vel':[0,0,0],'root_link':{'pos':[4,5,6]},
                  'controllers':{'arm_left':{'goal':{'target_pos':[7,8,9]}}}}
        data={'objects_info':{'init_info':{'r':{'class_module':'omnigibson.robots.r1pro',
              'class_name':'R1Pro','args':{'model':'r1pro'}}}},
              'state':{'registry':{'object_registry':{'r':physical}}}}
        changes=normalize_embedded_robot(data)
        self.assertEqual(physical['controller_groups'],{})
        self.assertEqual(physical['joint_pos'],[1,2,3])
        self.assertEqual(physical['root_link'],{'pos':[4,5,6]})
        self.assertEqual(changes[-1]['migration'],'legacy_controller_state')
        self.assertEqual(normalize_embedded_robot(data),[])

    def test_current_controller_state_is_preserved(self):
        state={'controller_groups':{'arm_left':{'goal':{'target':[1,2]}}}}
        data={'objects_info':{'init_info':{'r':{'class_name':'Robot','args':{'model':'r1pro'}}}},
              'state':{'registry':{'object_registry':{'r':state}}}}
        self.assertEqual(normalize_embedded_robot(data),[])
        self.assertEqual(state['controller_groups']['arm_left']['goal']['target'],[1,2])


if __name__=='__main__':unittest.main()
