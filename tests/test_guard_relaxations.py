"""Evidence-based actuator guard regressions; CPU fixtures, not simulator success."""
import importlib.util
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.placement import CheckedPlacement
from manipulation_agent.omnigibson_backend import OmniGibsonBackend


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires CPU geometry environment')
class GuardRelaxationTests(unittest.TestCase):
    def support_fixture(self):
        import torch
        def body(name):
            return SimpleNamespace(name=name, links={'root': SimpleNamespace(prim_path='/'+name)})
        held, target, robot = [body(name) for name in ('door', 'cabinet', 'robot')]
        # Archived task078: no contact predicate, but stationary and flush.
        held.aabb = (torch.tensor([-.01, -.13, 5.96e-8]), torch.tensor([.01, .13, .4565]))
        held.get_linear_velocity = lambda: torch.zeros(3)
        backend = CheckedPlacement()
        backend.torch, backend.robot = torch, robot
        backend._get_held = lambda: None
        hit = {'hit': True, 'rigidBody': '/cabinet', 'position': torch.zeros(3),
               'normal': torch.tensor([0., 0., 1.])}
        sampling = ModuleType('omnigibson.utils.sampling_utils')
        sampling.raytest = lambda *args, **kwargs: hit
        return backend, held, target, hit, sampling

    def test_archived_flush_support_survives_absent_contact_predicate(self):
        import torch
        backend, held, target, hit, sampling = self.support_fixture()
        with patch.dict('sys.modules', {'omnigibson.utils.sampling_utils': sampling}):
            valid, evidence = backend._selected_surface_support(held, target, torch.zeros(3), False)
        self.assertTrue(valid, evidence)
        self.assertFalse(evidence['touching_selected_object'])
        self.assertTrue(all(evidence['checks'].values()))

    def test_relaxed_contact_does_not_accept_wrong_surface_side_or_unsettled_object(self):
        import torch
        for fault in ('wrong_object', 'side_contact', 'wrong_shelf', 'moving', 'still_held'):
            with self.subTest(fault=fault):
                backend, held, target, hit, sampling = self.support_fixture()
                point = torch.zeros(3)
                if fault == 'wrong_object': hit['rigidBody'] = '/other'
                if fault == 'side_contact': hit['normal'] = torch.tensor([1., 0., 0.])
                if fault == 'wrong_shelf': point[2] = .5
                if fault == 'moving': held.get_linear_velocity = lambda: torch.tensor([.2, 0., 0.])
                if fault == 'still_held': backend._get_held = lambda: held
                with patch.dict('sys.modules', {'omnigibson.utils.sampling_utils': sampling}):
                    valid, _ = backend._selected_surface_support(held, target, point, True)
                self.assertFalse(valid)

    def carry_fixture(self):
        import torch
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        backend = RGBBackend.__new__(RGBBackend)
        backend.output, backend.torch = Path(folder.name), torch
        backend.steps, backend.deadline = 0, EpisodeDeadline()
        backend.ideal_carry = True
        backend._base_target = backend._object_anchor = None
        quat = torch.tensor([0., 0., 0., 1.])
        def body(name, xyz):
            obj = SimpleNamespace(name=name, position=torch.tensor(xyz), fixed_base=False)
            obj.get_position_orientation = lambda: (obj.position.clone(), quat.clone())
            obj.set_position_orientation = lambda p, q: obj.position.copy_(p)
            obj.keep_still = lambda: None
            obj.states = {}
            return obj
        robot, held, payload = body('robot', [0., 0., 0.]), body('atomizer', [.5, 0., 1.]), body('payload', [.5, 0., 1.2])
        robot.base_idx = torch.tensor([0, 1])
        robot.get_joint_positions = lambda: torch.zeros(4)
        robot.set_joint_positions = lambda *args, **kwargs: None
        robot.q_to_action = lambda q: q
        backend.robot, backend._ideal_held = robot, held
        backend._carry_relative = (held.position.clone(), quat.clone())
        backend._carry_contents = [(payload, (torch.tensor([0., 0., .2]), quat.clone()))]
        backend._carry_dependencies = [('unchanged',)]
        backend._video_frame = lambda *args: None
        backend._placement_record = lambda record: None
        backend._ground = lambda *args, **kwargs: (held, held.position.clone(), {})
        backend._navigate = lambda *args: self.fail('A nearby carried switch must not navigate')
        states = ModuleType('omnigibson.object_states')
        for name in ('Open', 'Inside', 'ToggledOn'):
            setattr(states, name, type(name, (), {}))
        toggle = SimpleNamespace(value=False, accepted=True)
        def set_toggle(wanted):
            toggle.value = wanted
            return toggle.accepted
        toggle.set_value, toggle.get_value = set_toggle, lambda: toggle.value
        held.states[states.ToggledOn] = toggle
        def physics():
            # Exercise the real anchor/follow ordering against displaced bodies.
            for obj in (robot, held, payload): obj.position.add_(.01)
            backend.steps += 1
        def dump(**kwargs):
            return [obj.position.clone() for obj in (robot, held, payload)], toggle.value
        def load(snapshot, **kwargs):
            poses, toggle.value = snapshot
            for obj, pose in zip((robot, held, payload), poses): obj.position.copy_(pose)
        backend.og = SimpleNamespace(sim=SimpleNamespace(step_physics=physics, dump_state=dump, load_state=load))
        transforms = ModuleType('omnigibson.utils.transform_utils')
        transforms.pose_transform = lambda p, q, offset, oq: (p+offset, oq)
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils = transforms
        og_module = ModuleType('omnigibson')
        og_module.utils, og_module.object_states = utils, states
        modules = {'omnigibson': og_module, 'omnigibson.object_states': states, 'omnigibson.utils': utils,
                   'omnigibson.utils.transform_utils': transforms}
        return backend, held, payload, toggle, modules

    def test_held_self_toggle_preserves_carry_and_payload_across_physics_steps(self):
        import torch
        backend, held, payload, toggle, modules = self.carry_fixture()
        original = [obj.position.clone() for obj in (backend.robot, held, payload)]
        relation, contents, dependencies = backend._carry_relative, backend._carry_contents, backend._carry_dependencies
        with patch.dict('sys.modules', modules), patch.object(
                OmniGibsonBackend, '_step', lambda b, action: b.og.sim.step_physics()):
            for primitive, wanted in (('toggle_on', True), ('toggle_off', False)):
                backend.execute_visual(primitive, {'image_ref': 'current', 'point': [.5, .5]}, 100)
                self.assertEqual(toggle.value, wanted)
                self.assertIs(backend._get_held(), held)
                self.assertIs(backend._carry_relative, relation)
                self.assertIs(backend._carry_contents, contents)
                self.assertIs(backend._carry_dependencies, dependencies)
                for obj, expected in zip((backend.robot, held, payload), original):
                    self.assertTrue(torch.allclose(obj.position, expected))
                self.assertIsNone(backend._base_target)
                self.assertIsNone(backend._object_anchor)
        self.assertEqual(backend.steps, 60)

    def test_other_object_toggle_and_articulated_operations_keep_hand_guard(self):
        backend, held, payload, toggle, modules = self.carry_fixture()
        for primitive, target in [('toggle_on', payload), ('toggle_off', payload),
                                  ('open', held), ('close', held), ('open', payload)]:
            with self.subTest(primitive=primitive, target=target.name):
                backend._ground = lambda *args, target=target, **kwargs: (target, target.position.clone(), {})
                with self.assertRaises(SkillError) as error:
                    backend.execute_visual(primitive, {'image_ref': 'current', 'point': [.5, .5]}, 100)
                self.assertEqual(error.exception.code, 'hand_occupied')
        self.assertEqual(backend.steps, 0)
        self.assertFalse(toggle.value)

    def test_rejected_held_toggle_restores_state_without_losing_carry(self):
        backend, held, payload, toggle, modules = self.carry_fixture()
        toggle.accepted = False
        with patch.dict('sys.modules', modules):
            with self.assertRaises(SkillError) as error:
                backend.execute_visual('toggle_on', {'image_ref': 'current', 'point': [.5, .5]}, 100)
        self.assertEqual(error.exception.code, 'execution_error')
        self.assertFalse(toggle.value)
        self.assertIs(backend._get_held(), held)
        self.assertIsNone(backend._base_target)
        self.assertIsNone(backend._object_anchor)


if __name__ == '__main__':
    unittest.main()
