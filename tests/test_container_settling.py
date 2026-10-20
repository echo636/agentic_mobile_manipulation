"""Waiting must advance contents, without restoring obsolete settled poses."""
from contextlib import nullcontext
from types import SimpleNamespace
import unittest

from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class ContainerSettlingTests(unittest.TestCase):
    def fixture(self):
        class Body:
            def __init__(self, height):
                self.height = height
            def set_position_orientation(self, position, orientation):
                self.height = position
            def keep_still(self):
                pass
        box, child = Body(0.), Body(1.)
        backend = RGBBackend.__new__(RGBBackend)
        backend._performance_write_failed = True
        backend._get_held = lambda: None
        backend._anchored_operation = lambda: nullcontext()
        backend.robot = SimpleNamespace(get_joint_positions=lambda: None, q_to_action=lambda _: None)
        backend.og = SimpleNamespace(sim=SimpleNamespace(get_sim_step_dt=lambda: .1))
        backend.env = SimpleNamespace(scene=SimpleNamespace(objects=[box, child]))
        backend._stabilized_containers = {box: (0., None)}
        backend._stabilized_container_payloads = {box: [(child, box, 1.)]}
        backend._container_payload = lambda target: (
            [(child, target, child.height-target.height)] if child.height >= 0 else [])
        backend._relocate_container_payload = lambda records: [
            setattr(item, 'height', parent.height+relative) for item, parent, relative in records]
        def step(_):
            backend._restore_stabilized_containers()
            box.height += .01
            child.height -= .1
            backend._restore_stabilized_containers()
        backend._step = step
        return backend, box, child

    def test_wait_allows_gravity_and_retains_the_new_pose_afterwards(self):
        backend, box, child = self.fixture()
        result = backend.execute_visual('wait', None, 10, seconds=.3)
        self.assertEqual(result['steps'], 3)
        self.assertAlmostEqual(child.height, .7)
        self.assertEqual(box.height, 0.)
        backend._restore_stabilized_containers()
        self.assertAlmostEqual(child.height, .7)

    def test_interrupted_wait_does_not_reinstate_pre_wait_contents(self):
        backend, _, child = self.fixture()
        def interrupted(_):
            child.height -= .1
            raise RuntimeError('cancelled after physics')
        backend._step = interrupted
        with self.assertRaisesRegex(RuntimeError, 'cancelled after physics'):
            backend.execute_visual('wait', None, 10, seconds=.3)
        backend._restore_stabilized_containers()
        self.assertAlmostEqual(child.height, .9)

    def test_escaped_content_is_never_pulled_back_into_the_container(self):
        backend, box, child = self.fixture()
        backend._step = lambda _: setattr(child, 'height', -1.)
        backend.execute_visual('wait', None, 1, seconds=.1)
        backend._restore_stabilized_containers()
        self.assertEqual(child.height, -1.)
        self.assertEqual(backend._stabilized_container_payloads[box], [])


if __name__ == '__main__':
    unittest.main()
