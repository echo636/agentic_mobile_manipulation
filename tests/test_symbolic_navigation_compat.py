"""Exercise the actual pinned parent/override methods without importing Isaac Sim."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from manipulation_agent.executors.symbolic_compat import adapt_symbolic_navigation_signature


def upstream_directory():
    configured = os.environ.get('MAS_OG_SOURCE')
    candidates = [Path(configured)] if configured else [
        parent / 'repos/BEHAVIOR-1K/OmniGibson/omnigibson/action_primitives'
        for parent in Path(__file__).resolve().parents
    ]
    for directory in candidates:
        if (directory / 'symbolic_semantic_action_primitives.py').is_file():
            return directory
    raise unittest.SkipTest('Pinned upstream source unavailable; set MAS_OG_SOURCE to action_primitives directory')


def upstream_method(directory, filename, class_name, method_name):
    path = directory / filename
    tree = ast.parse(path.read_text(), filename=str(path))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name)
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[method_name]


class NativeNavigationSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = upstream_directory()
        parent = upstream_method(directory, 'starter_semantic_action_primitives.py',
                                 'StarterSemanticActionPrimitives', '_navigate_to_obj')
        endpoint = upstream_method(directory, 'symbolic_semantic_action_primitives.py',
                                   'SymbolicSemanticActionPrimitives', '_navigate_to_pose')

        class NativeMethods:
            _navigate_to_obj = parent
            _navigate_to_pose = endpoint

            def __init__(self):
                self.pose = [1.25, -0.5, 0.75]
                self.calls = []
                self.robot = SimpleNamespace(set_position_orientation=self.set_pose)

            def _sample_pose_near_object(self, obj, **kwargs):
                self.calls.append(('sample', obj, kwargs))
                return self.pose

            def _get_robot_pose_from_2d_pose(self, pose):
                self.calls.append(('convert_pose', pose))
                return 'original_position', 'original_orientation'

            def set_pose(self, *pose):
                self.calls.append(('set_pose', pose))

            def _settle_robot(self):
                try:
                    yield 'native_settle_action_1'
                    yield 'native_settle_action_2'
                finally:
                    self.calls.append(('settle_closed',))

        cls.NativeMethods = NativeMethods

    def test_unadapted_pinned_inherited_call_reproduces_actual_failure(self):
        native = self.NativeMethods()
        with self.assertRaisesRegex(TypeError, 'skip_obstacle_update'):
            list(native._navigate_to_obj('selected_object'))
        self.assertEqual(len(native.calls), 1)  # Candidate found, endpoint never entered.

    def test_inherited_calls_preserve_sampling_flag_pose_and_native_settling(self):
        for skip in (False, True):
            with self.subTest(skip_obstacle_update=skip):
                expected = self.NativeMethods()
                expected_actions = list(expected._navigate_to_pose(expected.pose))
                native = self.NativeMethods()
                original_endpoint = native._navigate_to_pose.__func__
                info = adapt_symbolic_navigation_signature(native)
                actions = list(native._navigate_to_obj('selected_object', skip_obstacle_update=skip))
                self.assertTrue(info['applied'])
                self.assertEqual(actions, expected_actions)
                self.assertEqual(native.calls[0], ('sample', 'selected_object',
                                                  {'eef_pose': None, 'skip_obstacle_update': skip}))
                self.assertEqual(native.calls[1:], expected.calls)
                self.assertIs(type(native)._navigate_to_pose, original_endpoint)
                with self.assertRaisesRegex(TypeError, 'skip_obstacle_update'):
                    list(self.NativeMethods()._navigate_to_obj('other_instance'))

    def test_direct_default_call_and_generator_close_keep_native_behavior(self):
        native = self.NativeMethods()
        adapt_symbolic_navigation_signature(native)
        generator = native._navigate_to_pose(native.pose)
        self.assertEqual(native.calls, [])  # Wrapper does not execute or consume the generator.
        self.assertEqual(next(generator), 'native_settle_action_1')
        generator.close()
        self.assertEqual(native.calls[-1], ('settle_closed',))
        with self.assertRaisesRegex(TypeError, 'unknown_option'):
            native._navigate_to_pose(native.pose, unknown_option=True)


class DelegationTests(unittest.TestCase):
    def test_exact_return_and_exceptions_are_preserved(self):
        marker = object()

        class Primitive:
            def _navigate_to_pose(self, pose_2d):
                if pose_2d == 'fail':
                    raise ValueError('upstream endpoint failure')
                return marker

        native = Primitive()
        adapt_symbolic_navigation_signature(native)
        self.assertIs(native._navigate_to_pose('pose', skip_obstacle_update=True), marker)
        with self.assertRaisesRegex(ValueError, 'upstream endpoint failure'):
            native._navigate_to_pose('fail', skip_obstacle_update=False)

    def test_already_compatible_upstream_keeps_its_keyword_semantics(self):
        class Primitive:
            def _navigate_to_pose(self, pose_2d, *, skip_obstacle_update=False):
                return pose_2d, skip_obstacle_update

        native = Primitive()
        original = native._navigate_to_pose.__func__
        self.assertFalse(adapt_symbolic_navigation_signature(native)['applied'])
        self.assertIs(native._navigate_to_pose.__func__, original)
        self.assertEqual(native._navigate_to_pose('pose', skip_obstacle_update=True), ('pose', True))


if __name__ == '__main__':
    unittest.main()
