"""Archived stance failures: real geometry/planner, not simulator task evidence."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import importlib.util
import json
import math
import unittest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.gt_navigation import GridMap, SearchBudgetExhausted
from manipulation_agent.executors.heading_collision import HeadingCollisionChecker
from manipulation_agent.executors.local_manipulation import LocalLimits, LocalOperation, LocalCollision, stance_candidates
from manipulation_agent.executors.local_rgb import LocalRGBManipulation
from manipulation_agent.executors.local_stances import local_stance_proposals
from manipulation_agent.observations.boundary import public_execution_error

FIXTURES = Path(__file__).parent / 'fixtures/local_stances'


class GeometryMotor(LocalRGBManipulation):
    """Keep raycasts/lift out of a CPU geometry test; execute no physical action."""
    def __init__(self, start, collision, held):
        self.pose, self.collision, self.held = start, collision, held
        self.local_limits = LocalLimits()
        self.steps = self.sampling_physics_steps = 0
        self.ideal_carry = self.demo_motion = False
        self.deadline = SimpleNamespace(check=lambda **kw: None)
        self.records = []
        self.navigation_distance = 0.
        self.route = None

    def _local_pose(self): return self.pose
    def _local_geometry(self, op):
        return self.collision.grid, self.collision, {'held_object_footprint_included': self.held}, [], 0
    def _local_record(self, **kw): self.records.append(kw)
    def _approach_visible(self, *args, **kw): return True
    def _execute_gt_plan(self, grid, plan, max_steps, *, prepared_route):
        self.route = prepared_route
        return {'actual_path_distance_m': prepared_route.distance_m}


@unittest.skipUnless(importlib.util.find_spec('cv2') and importlib.util.find_spec('numpy'), 'navigation geometry dependencies')
class ArchivedStanceTests(unittest.TestCase):
    def fixture(self, number):
        import cv2
        import numpy as np
        cv2.setNumThreads(1)
        rows = json.loads((FIXTURES/'manifest.json').read_text())
        row = next(r for r in rows if r['agent_step'] == number)
        path = FIXTURES/row['file']
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['sha256'])
        with np.load(path, allow_pickle=False) as data:
            support = data['support']
            grid = GridMap(support.shape[1], support.shape[0], float(data['resolution']), tuple(data['origin']), support.tobytes())
            start, target = tuple(data['start']), tuple(data['target'])
            layers = list(zip(data['raw_layers'], [x['footprint'] for x in json.loads(str(data['layer_footprints_json']))]))
            collision = HeadingCollisionChecker(grid, layers, start[2], support)
        return row, start, target, collision

    def check_repaired_route(self, number):
        row, start, target, collision = self.fixture(number)
        motor = GeometryMotor(start, collision, number in (9,24))
        op = LocalOperation(motor, row['primitive'], None, 600, {})
        motor._local_reposition(row['primitive'], object(), target, op)
        self.assertIsNotNone(motor.route)
        current = start
        for phase in motor.route.phases:
            for xy in phase.points:
                end = (*xy, phase.final_yaw)
                self.assertTrue(collision.motion_free(current,end))
                current = end
        self.assertLessEqual(math.dist(current[:2],target[:2]), 1.4)
        record = next(x for x in motor.records if x['phase'] == 'stance_search')
        self.assertEqual(record['termination'], 'selected')

    def test_fridge_open_approach_has_missed_geometry_stance(self): self.check_repaired_route(1)
    def test_first_plate_has_missed_geometry_stance(self): self.check_repaired_route(4)
    def test_loaded_fridge_approach_has_missed_geometry_stance(self): self.check_repaired_route(9)
    def test_second_plate_has_missed_geometry_stance(self): self.check_repaired_route(18)
    def test_departure_rejected_radial_stance_gets_another_free_space_option(self): self.check_repaired_route(24)

    def test_far_table_side_is_no_longer_rejected_only_by_start_radius(self):
        row, start, target, collision = self.fixture(7)
        motor = GeometryMotor(start, collision, True)
        op = LocalOperation(motor,row['primitive'],None,600,{})
        try:
            motor._local_reposition(row['primitive'],object(),target,op)
        except SkillError as caught:
            # A finite route budget may still expire; the target disk must
            # contain free poses rather than inheriting the old start cutoff.
            self.assertEqual(caught.code,'local_adjustment_exhausted')
        summary = next(x for x in motor.records if x['phase']=='stance_search')
        candidates=next(x['candidates'] for x in motor.records if x['phase']=='candidates')
        self.assertTrue(any(math.dist(c['pose'][:2],start[:2])>2. and c.get('reason')!='collision' for c in candidates)
                        or summary.get('geometry_free_poses',0)>0)

    def test_target_approach_can_execute_checked_route_longer_than_four_meters(self):
        import numpy as np
        raw=np.ones((61,101),dtype='uint8')
        raw[:,50]=0;raw[42:57,50]=1
        grid=GridMap(101,61,.1,(-2.,-3.),raw.tobytes())
        footprint=[[-.15,-.1],[.15,-.1],[.15,.1],[-.15,.1]]
        start=(0.,0.,0.);target=(6.,0.,.8)
        collision=HeadingCollisionChecker(grid,[(raw,footprint)],0.,raw)
        motor=GeometryMotor(start,collision,False)
        op=LocalOperation(motor,'grasp',None,600,{})
        motor._local_reposition('grasp',object(),target,op)
        self.assertGreater(motor.route.distance_m,4.)
        current=start
        fresh=HeadingCollisionChecker(grid,[(raw,footprint)],0.,raw)
        self.assertFalse(fresh.motion_free(start,(4.7,0.,0.)))
        for phase in motor.route.phases:
            for xy in phase.points:
                end=(*xy,phase.final_yaw)
                self.assertTrue(fresh.motion_free(current,end));current=end
        self.assertLessEqual(math.dist(current[:2],target[:2]),1.4)

    def test_free_geometry_does_not_bypass_visibility(self):
        row, start, target, collision = self.fixture(4)
        motor = GeometryMotor(start,collision,False)
        motor._approach_visible = lambda *a,**kw: False
        op = LocalOperation(motor,row['primitive'],None,600,{})
        with self.assertRaises(SkillError) as caught:
            motor._local_reposition(row['primitive'],object(),target,op)
        self.assertEqual(caught.exception.stage,'local_target_visibility')
        self.assertIsNone(motor.route)

    def test_fallback_preparation_remains_cancellable(self):
        _, start, target, collision = self.fixture(4)
        calls = [0]
        def cancel():
            calls[0] += 1
            if calls[0] >= 30: raise SearchBudgetExhausted()
        with self.assertRaises(SearchBudgetExhausted):
            list(local_stance_proposals(start,start,target,LocalLimits(),
                 LocalCollision(collision,start,2.),check_cancelled=cancel))


class StanceFeedbackTests(unittest.TestCase):
    def test_only_fixed_stage_and_message_cross_rgb_boundary(self):
        exc = SkillError('local_adjustment_blocked','private object /World/secret at (1,2)',stage='local_stance_collision')
        result = public_execution_error(exc)
        self.assertEqual(result['stage'],'local_stance_collision')
        self.assertNotIn('secret',str(result))
        self.assertIn('different general approach',result['message'])
        exc.stage = '/World/secret'
        self.assertNotIn('stage',public_execution_error(exc))
        self.assertNotIn('secret',str(public_execution_error(exc)))
        exc.stage = 'local_stance_collision';exc.code='sampling_error'
        self.assertNotIn('stage',public_execution_error(exc))


if __name__ == '__main__': unittest.main()
