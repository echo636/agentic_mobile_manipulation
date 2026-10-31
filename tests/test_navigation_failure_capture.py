"""Lossless private evidence for failures, with unchanged planning decisions."""
import gzip
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from manipulation_agent.executors.gt_navigation import (
    GridMap, NavigationError, plan_navigation, save_navigation_failure,
)


class NavigationFailureCaptureTests(unittest.TestCase):
    def test_failure_inputs_replay_same_error_and_do_not_change_original_error(self):
        for blocked_filter in (False, True):
            with self.subTest(blocked_filter=blocked_filter):
                free=bytearray([1])*1600
                for row in range(40):free[row*40+20]=0
                grid=GridMap(40,40,.1,(0.,0.),bytes(free))
                options=dict(standoff=.2,max_snap=.2,max_target_distance=.3)
                filter_fn=(lambda xy:False) if blocked_filter else None
                with self.assertRaises(NavigationError) as old:
                    plan_navigation(grid,(.5,1.),(3.5,1.),candidate_filter=filter_fn,**options)
                diagnostics={}
                with self.assertRaises(NavigationError) as captured:
                    plan_navigation(grid,(.5,1.),(3.5,1.),candidate_filter=filter_fn,
                                    diagnostics=diagnostics,**options)
                self.assertEqual(str(old.exception),str(captured.exception))
                self.assertEqual(diagnostics['stage'],'projection' if blocked_filter else 'reachability')
                with tempfile.TemporaryDirectory() as folder:
                    path=save_navigation_failure(folder,grid=grid,static_grid=grid,
                            diagnostics=diagnostics,error=captured.exception,context={'test':True})
                    with gzip.open(path,'rt') as f:payload=json.load(f)
                    g=payload['grid']; restored=GridMap(g['width'],g['height'],g['resolution'],tuple(g['origin']),bytes.fromhex(g['free_hex']))
                    self.assertEqual(restored,grid)
                    decisions={(r,c):value for r,c,value in diagnostics.get('candidate_filter_results',[])}
                    def replay_filter(xy):return decisions[restored.cell(xy)]
                    with self.assertRaises(NavigationError) as replay:
                        plan_navigation(restored,diagnostics['current'],diagnostics['hint'],
                            candidate_filter=replay_filter if diagnostics['filter_present'] else None,**options)
                    self.assertEqual(str(replay.exception),payload['error']['message'])
                    self.assertEqual(replay.exception.code,payload['error']['code'])
                    self.assertEqual(payload['audience'],'executor_private')
                    cli=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/replay_navigation_failure.py'),str(path)],capture_output=True,text=True)
                    self.assertEqual(cli.returncode,0,cli.stderr)
                    self.assertTrue(json.loads(cli.stdout)['reproduced'])

    def test_successful_plan_identical_with_capture_enabled(self):
        grid=GridMap(20,20,.1,(0.,0.),bytes([1])*400)
        baseline=plan_navigation(grid,(.3,.3),(1.5,1.5))
        diagnostics={}
        observed=plan_navigation(grid,(.3,.3),(1.5,1.5),diagnostics=diagnostics)
        self.assertEqual(baseline,observed)
        self.assertEqual(diagnostics['stage'],'passed')


class NavigationFailureAdapterTests(unittest.TestCase):
    def test_real_adapter_records_failed_plan_without_motor_steps(self):
        import torch
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from manipulation_agent.contracts import SkillError
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        with tempfile.TemporaryDirectory() as folder:
            backend=RGBBackend.__new__(RGBBackend)
            backend.output=Path(folder)
            backend.steps=0
            backend.demo_motion=False
            backend.high_level_harness=False
            backend.rig_height=1.81
            backend.rig_pitch_degrees=35
            backend.rig_radius=.35
            occupancy=torch.ones((60,60),dtype=torch.uint8)
            occupancy[:,30]=0
            backend.env=SimpleNamespace(scene=SimpleNamespace(trav_map=SimpleNamespace(
                floor_heights=[0.],map_resolution=.1,_erode_trav_map=lambda grid:grid)))
            backend.robot=SimpleNamespace(get_position_orientation=lambda:(torch.tensor([-1.5,0.,.005]),None))
            backend._floor_map_with_open_garage_door=Mock(return_value=(occupancy,None))
            backend._dynamic_floor_obstacles=Mock(return_value=([],[]))
            backend._navigation_self_hulls=Mock(return_value=((),{}))
            target=SimpleNamespace(selected_object=SimpleNamespace(category='floors'),
                get_position_orientation=lambda:(torch.tensor([1.5,0.,0.]),None))
            with patch.dict('os.environ',{'MAS_NAV_RELAXED':'0'}):
                with self.assertRaises(SkillError) as error:backend._navigate(target,700)
            self.assertEqual(error.exception.code,'navigation_unreachable')
            self.assertFalse(error.exception.changed)
            self.assertEqual(backend.steps,0)
            files=list((backend.output/'navigation_failures').glob('*.json.gz'))
            self.assertEqual(len(files),1)
            with gzip.open(files[0],'rt') as f:payload=json.load(f)
            self.assertEqual(payload['diagnostics']['stage'],'reachability')
            self.assertTrue(payload['context']['floor_waypoint'])
            self.assertFalse(payload['diagnostics']['filter_present'])
            # The public exception remains the original error, not a snapshot.
            self.assertNotIn('free_hex',str(error.exception))
            cli=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/replay_navigation_failure.py'),str(files[0])],capture_output=True,text=True)
            self.assertEqual(cli.returncode,0,cli.stderr)
            self.assertTrue(json.loads(cli.stdout)['reproduced'])
