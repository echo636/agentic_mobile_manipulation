"""Reference recovery must retain failures and the common action ceiling."""
from pathlib import Path
from contextlib import redirect_stdout
import io
import math
import tempfile
from types import SimpleNamespace
import unittest

from manipulation_agent.contracts import SkillError
from manipulation_agent.reference_tasks import ReferenceTask, appliance_approach_point, placement_yaw_delta


class ApplianceApproachTests(unittest.TestCase):
    def test_lunch_fridge_target_is_on_near_face_at_body_height(self):
        low, high = [7.5326, -2.2074, .1192], [8.1056, -1.5822, 1.8268]
        point = appliance_approach_point((low, high), [6.175, 1.625, .0053])
        self.assertAlmostEqual(point[1], high[1])
        self.assertTrue(low[0] <= point[0] <= high[0])
        self.assertAlmostEqual(point[2], (low[2]+high[2])/2)

    def test_microwave_from_right_selects_right_face(self):
        low, high = [4.0254, .5523, .8849], [4.4494, 1.0439, 1.1903]
        point = appliance_approach_point((low, high), [6.2, .8, 0])
        self.assertAlmostEqual(point[0], high[0])
        self.assertTrue(low[1] <= point[1] <= high[1])

    def test_open_door_envelope_does_not_shift_target_outside_body(self):
        center = [4.2130208, .7885105, 1.038383]
        point = appliance_approach_point(([4.025, .2, .8849], [4.755, 1.044, 1.1903]),
                                         [7.875, -.875, 0], open_center=center)
        self.assertEqual(point, center)

    def test_world_placement_heading_is_independent_of_carry_arrival_yaw(self):
        for arrival in (-170., -45., 0., 90., 135., 179.):
            with self.subTest(arrival=arrival):
                radians = math.radians(arrival)
                quaternion = [0., 0., math.sin(radians/2), math.cos(radians/2)]
                delta = placement_yaw_delta(quaternion, -45.)
                actual = (arrival+delta+180.) % 360. - 180.
                self.assertAlmostEqual(actual, -45.)
                self.assertTrue(-180. <= delta <= 180.)


class ReferenceRecoveryTests(unittest.TestCase):
    def test_inventory_repack_retains_reach_and_preserves_omitted_tool(self):
        # Coordinates already exercised by the unload continuation. Repacking
        # must not navigate with a tool held or silently restore an omitted tool.
        tools = {'pliers': (24.21057, .28708), 'allen_wrench': (24.67402, .34865),
                 'screwdriver': (24.37291, .49238), 'flashlight': (24.53003, .68251),
                 'drill': (24.39630, 1.00261)}
        for omit in (None, 'drill'):
            with self.subTest(omit=omit):
                reference = ReferenceTask(None, None)
                held = None
                stance = None
                packed = []
                def action(primitive, role=None, point=None, **kwargs):
                    nonlocal held, stance
                    if primitive == 'navigate_to':
                        self.assertIsNone(held)
                        stance = point[:2]
                    elif primitive == 'grasp':
                        self.assertIsNone(held)
                        self.assertLess(math.dist(stance, tools[role]), 1.4)
                        for dx in (-.025, .025):
                            for dy in (-.025, .025):
                                self.assertLess(math.dist((stance[0]+dx, stance[1]+dy), tools[role]), 1.4)
                        held = role
                    elif primitive == 'place_inside':
                        self.assertEqual(role, 'box')
                        self.assertIsNotNone(held)
                        self.assertLess(math.dist(stance, (24.5, 1.5)), 1.4)
                        packed.append(held)
                        held = None
                reference.action = action
                reference.move = action
                reference.pack_kit(delivered=True, omit=omit)
                self.assertIsNone(held)
                self.assertCountEqual(packed, [item for item in tools if item != omit])

    def test_unloading_preserves_reach_without_navigating_a_loaded_assembly(self):
        reference = ReferenceTask(None, None)
        held = None
        stance = None
        placed = []
        def action(primitive, role=None, point=None, **kwargs):
            nonlocal held, stance
            if primitive == 'navigate_to':
                self.assertIsNone(held, 'Container contents must not trigger loaded base travel')
                stance = point[:2]
            elif primitive == 'grasp':
                self.assertIsNone(held)
                if role == 'pliers':
                    self.assertIn('flashlight', placed, 'Remove the supported flashlight first')
                held = role
            elif primitive == 'place_on_top':
                self.assertIsNotNone(held)
                self.assertEqual(role, 'bench')
                self.assertLess(math.dist(stance, point[:2]), 1.4)
                placed.append(held)
                held = None
        reference.action = action
        reference.unpack_delivered_kit()
        self.assertIsNone(held)
        self.assertCountEqual(placed, ['drill', 'flashlight', 'screwdriver', 'allen_wrench', 'pliers'])

    def reference(self, directory, failures, *, max_attempts=200):
        self.enterContext(redirect_stdout(io.StringIO()))
        backend = SimpleNamespace(steps=0, _ground=lambda: None, _predicate_values=lambda: {})

        def execute(primitive, target, max_steps, **kwargs):
            backend.steps += 1
            if primitive in ('open', 'close') and failures:
                raise failures.pop(0)
            return {'primitive': primitive}

        backend.execute_visual = execute
        reference = ReferenceTask(backend, Path(directory), max_attempts=max_attempts)
        # Coordinate selection is irrelevant to the retry; exercise the real
        # action counter, error ledger and result logging for every attempt.
        reference.move = lambda primitive, role: reference.action(primitive)
        return reference

    def displaced(self):
        return SkillError('postcondition_error', 'Operation displaced existing contents from the container')

    def test_unstable_close_retries_preserve_failures_and_action_budget(self):
        def unstable():
            return SkillError('postcondition_error', 'Requested object operation did not remain stable')
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [unstable(), unstable()])
            reference.close_packed_kit()
            self.assertEqual([row['primitive'] for row in reference.calls], ['wait', 'close']*3)
            self.assertEqual(sum(row['status']=='failed' for row in reference.calls), 2)
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [unstable()], max_attempts=3)
            with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                reference.close_packed_kit()
            self.assertEqual(len(reference.calls), 3)

    def test_unrelated_close_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced()])
            with self.assertRaises(SkillError):
                reference.close_packed_kit()
            self.assertEqual(len(reference.calls), 2)

    def test_wrong_placement_rotation_option_cannot_silently_succeed(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [])
            with self.assertRaisesRegex(SkillError, 'Unsupported reference options'):
                reference.action('place_on_top', yaw_degrees=180.)
            self.assertEqual(reference.backend.steps, 0)
            self.assertEqual(len(reference.calls), 1)
            self.assertEqual(reference.calls[0]['status'], 'failed')
            self.assertEqual(reference.calls[0]['options'], {'yaw_degrees': 180.})

    def test_placement_rotation_is_forwarded_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [])
            received = []
            reference.backend.execute_visual = lambda primitive, target, max_steps, **kwargs: received.append(kwargs)
            reference.action('place_on_top', placement_yaw_degrees=180.)
            self.assertEqual(received, [{'placement_yaw_degrees': 180.}])
            self.assertEqual(reference.calls[0]['options'], received[0])

    def test_recovered_open_preserves_failed_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced(), self.displaced()])
            result = reference.open_delivered_kit()
            self.assertEqual(result['primitive'], 'open')
            self.assertEqual(sum(r['status']=='failed' for r in reference.calls), 2)
            self.assertEqual(len(reference.calls), 6)

    def test_recovery_cannot_exceed_the_remaining_action_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced()], max_attempts=3)
            with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                reference.open_delivered_kit()
            self.assertEqual(len(reference.calls), 3)

    def test_unrelated_open_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [SkillError('out_of_reach', 'Move closer')])
            with self.assertRaises(SkillError) as caught:
                reference.open_delivered_kit()
            self.assertEqual(caught.exception.code, 'out_of_reach')
            self.assertEqual(sum(r['primitive']=='open' for r in reference.calls), 1)

    def test_persistent_containment_failure_never_counts_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = self.reference(directory, [self.displaced() for _ in range(3)])
            with self.assertRaises(SkillError):
                reference.open_delivered_kit()
            self.assertEqual(sum(r['status']=='failed' for r in reference.calls), 3)


if __name__ == '__main__':
    unittest.main()
