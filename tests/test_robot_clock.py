"""Robot clock accounting rules and their executor hooks (CPU only, no simulator)."""
import json
import math
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.runtime.robot_clock import (MANIPULATION_SECONDS, RobotClock, charge_base_motion,
                                                    clocked_action, clocked_leaf, robot_clock_record,
                                                    route_motion, simplify, yaw_of_xyzw)
from manipulation_agent.reporting.efficiency_table import (markdown_table, offline_robot_time, six_metrics)


def staircase(cells, step=.05):
    """A grid route that alternates +x and +y steps: the shape a map pathfinder returns for a diagonal."""
    points = [(0., 0.)]
    for i in range(cells):
        x, y = points[-1]
        points.append((x + step, y) if i % 2 == 0 else (x, y + step))
    return points


class RouteMotionTests(unittest.TestCase):
    def test_grid_staircase_is_not_charged_as_turning(self):
        route = staircase(80)
        self.assertEqual(len(simplify(route)), 2)
        distance, turn, seconds = route_motion(route, math.pi / 4, math.pi / 4)
        self.assertAlmostEqual(distance, 4.)
        self.assertAlmostEqual(turn, 0., places=6)
        self.assertAlmostEqual(seconds, 8.)

    def test_real_corner_and_end_yaw_are_charged(self):
        route = [(0., 0.), (1., 0.), (2., 0.), (2., 1.), (2., 2.)]
        distance, turn, seconds = route_motion(route, 0., math.pi)
        self.assertAlmostEqual(distance, 4.)
        self.assertAlmostEqual(turn, math.pi)          # 90 degrees at the corner + 90 to face the end yaw
        self.assertAlmostEqual(seconds, 4 / .5 + 3.)

    def test_turn_in_place_and_unknown_start_yaw(self):
        self.assertAlmostEqual(route_motion([(1., 1.)], 0., math.pi / 2)[2], 1.5)
        self.assertEqual(route_motion([(1., 1.)], None, 2.), (0., 0., 0.))

    def test_yaw_from_quaternion(self):
        half = math.pi / 4
        self.assertAlmostEqual(yaw_of_xyzw([0., 0., math.sin(half), math.cos(half)]), math.pi / 2)


class ChargingRuleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.backend = SimpleNamespace(output=Path(folder.name), steps=0, sampling_physics_steps=0)

    def ledger(self):
        return [json.loads(line) for line in (self.backend.output / 'robot_clock.jsonl').read_text().splitlines()]

    def act(self, primitive, leaf, **options):
        return clocked_action(self.backend, primitive, options, lambda: clocked_leaf(self.backend, leaf))

    def test_completed_manipulation_costs_ten_seconds(self):
        self.act('grasp', lambda: {'primitive': 'grasp'})
        clock = self.backend.robot_clock
        self.assertEqual((clock.manipulation_count, clock.total_s), (1, MANIPULATION_SECONDS))
        self.assertEqual(self.ledger()[0]['rule'], 'manipulation_completed')

    def test_refusal_before_any_attempt_is_free(self):
        def refuse():
            raise SkillError('hand_occupied', 'Hand occupied')
        with self.assertRaises(SkillError):
            self.act('grasp', refuse)
        clock = self.backend.robot_clock
        self.assertEqual((clock.total_s, clock.refused_acts), (0., 1))
        row = self.ledger()[0]
        self.assertEqual((row['ok'], row['error'], row['rule']), (False, 'hand_occupied', 'refused_before_attempt'))

    def test_failure_after_physics_started_is_charged(self):
        def fail_late():
            self.backend.steps += 3
            raise SkillError('placement_unstable', 'Did not settle')
        with self.assertRaises(SkillError):
            self.act('place_on_top', fail_late)
        clock = self.backend.robot_clock
        self.assertEqual((clock.manipulation_count, clock.failed_attempts_charged), (1, 1))

    def test_wait_charges_executed_seconds_and_navigation_charges_no_manipulation(self):
        clocked_action(self.backend, 'wait', {'seconds': 20.}, lambda: {'sim_seconds': 19.8})
        clocked_action(self.backend, 'navigate_to', {}, lambda: charge_base_motion(
            self.backend, [(0., 0.), (2., 0.)], [0., 0., 0., 1.], 0.))
        clock = self.backend.robot_clock
        self.assertAlmostEqual(clock.wait_s, 19.8)
        self.assertEqual(clock.manipulation_count, 0)
        self.assertAlmostEqual(clock.travel_s, 4.)
        self.assertAlmostEqual(clock.total_s, 23.8)
        self.assertEqual(clock.last_action['this_call'], {'travel_m': 2., 'travel_s': 4., 'manipulation_s': 0.,
                                                          'wait_s': 0.})
        self.assertEqual(self.ledger()[1]['motions'][0]['kind'], 'navigate_to')

    def test_motion_committed_inside_a_failed_action_still_counts(self):
        def approach_then_refuse():
            charge_base_motion(self.backend, [(0., 0.), (1., 0.)], [0., 0., 0., 1.], 0.)
            raise SkillError('container_closed', 'Closed')
        with self.assertRaises(SkillError):
            clocked_action(self.backend, 'place_inside', {}, approach_then_refuse)
        clock = self.backend.robot_clock
        self.assertAlmostEqual(clock.travel_s, 2.)
        self.assertEqual(clock.manipulation_count, 0)

    def test_nested_action_is_settled_once(self):
        clocked_action(self.backend, 'grasp', {}, lambda: clocked_action(self.backend, 'grasp', {}, dict))
        self.assertEqual(self.backend.robot_clock.manipulation_count, 1)
        self.assertEqual(len(self.ledger()), 1)

    def test_accounting_failure_never_changes_execution(self):
        self.backend.output = Path('/nonexistent/robot-clock')
        self.assertEqual(self.act('grasp', lambda: 'done'), 'done')
        self.assertIsNone(charge_base_motion(self.backend, [(0., 0.), (float('nan'), 0.)], [0., 0., 0., 1.], 0.))
        self.assertEqual(len(self.backend.robot_clock.anomalies), 2)

    def test_record_totals(self):
        self.act('grasp', dict)
        record = robot_clock_record(self.backend)
        self.assertEqual(record['robot_time_seconds'], 10.)
        self.assertEqual(record['robot_clock']['version'], 'robot_clock_v2_native_path')
        self.assertEqual(robot_clock_record(SimpleNamespace()), {})


class ExecutorHookTests(unittest.TestCase):
    def backend(self):
        import torch
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.runtime.deadline import EpisodeDeadline
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        b = RGBBackend.__new__(RGBBackend)
        b.output = Path(folder.name)
        b.steps = b.sampling_physics_steps = 0
        b.deadline = EpisodeDeadline()
        b._base_floor_clearance = 0.
        b._pose = (torch.tensor([0., 0., 0.]), torch.tensor([0., 0., 0., 1.]))
        b.robot = SimpleNamespace(get_position_orientation=lambda: b._pose)
        b.env = SimpleNamespace(scene=SimpleNamespace(trav_map=SimpleNamespace(floor_heights=[0.])))
        b.primitives = SimpleNamespace(_empty_action=lambda: None)
        b._transfer_base_pose = lambda position, orientation: setattr(b, '_pose', (position, orientation))
        b._step = lambda action: setattr(b, 'steps', b.steps + 1)
        return b

    def test_base_path_commit_charges_route_and_logs_it(self):
        import torch
        b = self.backend()
        transform = ModuleType('omnigibson.utils.transform_utils')
        transform.euler2quat = lambda euler: torch.tensor(
            [0., 0., math.sin(float(euler[2]) / 2), math.cos(float(euler[2]) / 2)])
        modules = {'omnigibson': ModuleType('omnigibson'), 'omnigibson.utils': ModuleType('omnigibson.utils'),
                   'omnigibson.utils.transform_utils': transform}
        modules['omnigibson.utils'].transform_utils = transform
        with patch.dict(sys.modules, modules):
            result = b._execute_base_path([[0., 0.], [2., 0.], [2., 2.]], math.pi, 10)
        self.assertEqual(result['planned_route_distance_m'], 4.)
        clock = b.robot_clock
        self.assertAlmostEqual(clock.travel_m, 4.)
        self.assertAlmostEqual(clock.travel_s, 4 / .5 + 3.)    # 90 degrees at the corner, 90 to the end yaw
        row = json.loads((b.output / 'base_motion.jsonl').read_text())
        self.assertAlmostEqual(row['robot_time']['seconds'], 11.)
        self.assertAlmostEqual(row['robot_time']['start_yaw'], 0.)

    def test_execute_visual_settles_the_action_and_evaluation_reports_it(self):
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        b = self.backend()
        b._complete_pending_object_initialization = Mock()
        with patch.object(RGBBackend, '_execute_visual', Mock(return_value={'primitive': 'open'})):
            self.assertEqual(b.execute_visual('open', {}, 10), {'primitive': 'open'})
        self.assertEqual(robot_clock_record(b)['manipulation_count'], 1)
        self.assertEqual(json.loads((b.output / 'robot_clock.jsonl').read_text())['primitive'], 'open')


class OfflineReportTests(unittest.TestCase):
    def test_old_reference_logs_are_recomputed_by_the_same_rules(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        run = Path(folder.name)
        plans = [{'at': '2026-11-12T10:00:00+00:00', 'status': 'planned', 'mode': 'approach',
                  'floor_click_snap_m': None, 'start': [0., 0.], 'endpoint': [2., 0.], 'target': [3., 0.],
                  'path': [[0., 0.], [2., 0.]]},
                 {'at': '2026-11-12T10:00:30+00:00', 'status': 'planned', 'mode': 'approach',
                  'floor_click_snap_m': None, 'start': [2., 0.], 'endpoint': [2., 2.], 'target': [2., 3.],
                  'path': [[2., 0.], [2., 2.]]},
                 {'at': '2026-11-12T10:00:40+00:00', 'status': 'failed', 'path': None}]
        (run / 'navigation_plans.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in plans))
        actions = [('navigate_to', 'passed', {}, {}), ('grasp', 'passed', {}, {}),
                   ('place_on_top', 'failed', {}, {}), ('wait', 'passed', {'seconds': 10.}, {'sim_seconds': 10.})]
        (run / 'reference_trajectory.jsonl').write_text(''.join(json.dumps({
            'event': 'action_finished', 'time': 1794477600. + i, 'started_at': 1794477600. + i, 'seconds': 1.5,
            'primitive': primitive, 'status': status, 'options': options, 'result': result}) + '\n'
            for i, (primitive, status, options, result) in enumerate(actions)))
        (run / 'run_record.json').write_text(json.dumps({
            'status': 'passed', 'started_at_unix': 1794477500., 'finished_at_unix': 1794477700.,
            'evaluation': {'task_success': True, 'ideal_navigation_distance_m': 2.8}}))
        row = six_metrics(run, label='reference')
        time = row['robot_time']
        self.assertEqual(time['source'], 'offline_executor_logs')
        self.assertAlmostEqual(time['travel_distance_m'], 4.)
        self.assertAlmostEqual(time['travel_seconds'], 4 / .5 + 1.5)   # one 90 degree turn between the legs
        self.assertEqual((time['manipulation_count'], time['wait_seconds']), (1, 10.))
        self.assertAlmostEqual(time['robot_time_seconds'], 9.5 + 10. + 10.)
        self.assertEqual((row['task_success'], row['steps'], row['failed_steps']), (True, 4, 1))
        self.assertIn('| reference | 是 | 4（1） | 30（10 + 10 + 10） | 4.0 |', markdown_table([row]))
        self.assertIsNone(offline_robot_time(run / 'missing', []))

    def test_clock_totals_in_the_evaluation_take_precedence(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        run = Path(folder.name)
        clock = RobotClock()
        clock.add_motion(3., 7.)
        (run / 'run.json').write_text(json.dumps({'task_success': False, 'wall_seconds': 50.,
                                                  'evaluation': clock.record()}))
        (run / 'events.jsonl').write_text('')
        row = six_metrics(run)
        self.assertEqual((row['robot_time']['source'], row['robot_time']['robot_time_seconds']),
                         ('robot_clock', 7.))

    def test_token_usage_falls_back_to_verified_native_session_archive(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        run = root / 'run'
        controller = root / 'controller'
        run.mkdir()
        controller.mkdir()
        (run / 'run.json').write_text(json.dumps({'task_success': True, 'wall_seconds': 120., 'evaluation': {}}))
        (run / 'events.jsonl').write_text('')
        (controller / 'model_events.jsonl').write_text(json.dumps({'type': 'turn.started'}) + '\n')
        (controller / 'controller.json').write_text(json.dumps({
            'reasoning_trace': {'session_sha256': 'pinned-sha-256'},
        }))
        (controller / 'native_session_token_usage.json').write_text(json.dumps({
            'session_sha256': 'pinned-sha-256',
            'final_total_token_usage': {
                'input_tokens': 6890950,
                'cached_input_tokens': 6687104,
                'output_tokens': 5159,
            },
        }))
        row = six_metrics(run, controller, label='model_ep')
        self.assertEqual(row['usage'], {
            'input_tokens': 6890950,
            'cached_input_tokens': 6687104,
            'output_tokens': 5159,
        })
        self.assertIn('6,890,950 / 6,687,104 / 5,159', markdown_table([row]))

        (controller / 'native_session_token_usage.json').write_text(json.dumps({
            'session_sha256': 'mismatched-sha-256',
            'final_total_token_usage': {'input_tokens': 999},
        }))
        self.assertEqual(six_metrics(run, controller)['usage'], {})


if __name__ == '__main__':
    unittest.main()
