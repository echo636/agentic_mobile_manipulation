"""Evidence-based actuator guard regressions; CPU fixtures, not simulator success."""
import importlib.util
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.placement import CheckedPlacement


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
        held.get_angular_velocity = lambda: torch.zeros(3)
        backend = CheckedPlacement()
        backend.torch, backend.robot = torch, robot
        backend._get_held = lambda: None
        backend._collision_vertices = lambda obj: torch.tensor([[-.01, -.13, 5.96e-8], [.01, .13, 5.96e-8], [-.01, -.13, .4565], [.01, .13, .4565]]).numpy()
        backend._placement_contacts = lambda *args: []
        backend._placement_contact_report = lambda *args, **kwargs: {'admissible': True}
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

    def test_hollow_lower_patch_uses_rim_support_when_center_ray_misses(self):
        import torch
        backend, held, target, hit, sampling = self.support_fixture()
        backend._collision_vertices = lambda obj: torch.tensor(
            [[-.04,-.04,0.],[-.04,.04,0.],[.04,-.04,0.],[.04,.04,0.]]).numpy()
        sampling.raytest = lambda start, *args, **kwargs: (
            {'hit': False} if float(torch.linalg.norm(start[:2])) < .02 else hit)
        with patch.dict('sys.modules', {'omnigibson.utils.sampling_utils': sampling}):
            valid, evidence = backend._selected_surface_support(held, target, torch.zeros(3), True)
            self.assertTrue(valid, evidence)
            self.assertGreater(len(evidence['support_probes']), 1)
            hit['rigidBody'] = '/other'
            self.assertFalse(backend._selected_surface_support(held, target, torch.zeros(3), True)[0])

    def test_relaxed_contact_does_not_accept_wrong_surface_side_or_unsettled_object(self):
        import torch
        for fault in ('wrong_object', 'side_contact', 'wrong_shelf', 'moving', 'rotating', 'robot_support', 'still_held'):
            with self.subTest(fault=fault):
                backend, held, target, hit, sampling = self.support_fixture()
                point = torch.zeros(3)
                if fault == 'wrong_object': hit['rigidBody'] = '/other'
                if fault == 'side_contact': hit['normal'] = torch.tensor([1., 0., 0.])
                if fault == 'wrong_shelf': point[2] = .5
                if fault == 'moving': held.get_linear_velocity = lambda: torch.tensor([.2, 0., 0.])
                if fault == 'rotating': held.get_angular_velocity = lambda: torch.tensor([0., 0., 1.])
                if fault == 'robot_support': backend._placement_contacts = lambda *args: ['/robot']
                if fault == 'still_held': backend._get_held = lambda: held
                with patch.dict('sys.modules', {'omnigibson.utils.sampling_utils': sampling}):
                    valid, _ = backend._selected_surface_support(held, target, point, True)
                self.assertFalse(valid)

    def carry_fixture(self):
        import torch
        backend = RGBBackend.__new__(RGBBackend)
        backend.torch, backend.steps = torch, 0
        backend.env = SimpleNamespace(scene=SimpleNamespace(active_systems={}))
        states = ModuleType('omnigibson.object_states')
        for name in ('Open', 'Inside', 'ToggledOn'):
            setattr(states, name, type(name, (), {}))
        held = SimpleNamespace(name='atomizer', states={})
        other = SimpleNamespace(name='other', states={})
        toggle = SimpleNamespace(value=False, accepted=True)
        def set_toggle(wanted):
            if toggle.accepted:
                toggle.value = wanted
            return toggle.accepted
        toggle.set_value, toggle.get_value = set_toggle, lambda: toggle.value
        held.states[states.ToggledOn] = toggle
        backend._get_held = lambda: held
        backend.primitives = SimpleNamespace(_empty_action=lambda: 'native_hold')
        backend._relocate_container_payload = lambda payload: self.assertEqual(payload, [])
        backend._verify_container_payload = lambda obj, payload: self.assertEqual(payload, [])
        def step(action):
            self.assertEqual(action, 'native_hold')
            backend.steps += 1
        backend._step = step
        return backend, held, other, toggle, {'omnigibson.object_states': states}

    def test_held_self_toggle_uses_native_state_and_settling(self):
        backend, held, _, toggle, modules = self.carry_fixture()
        with patch.dict('sys.modules', modules):
            for primitive, wanted in (('toggle_on', True), ('toggle_off', False)):
                backend._manipulation_preconditions(primitive, held)
                backend._state_action(primitive, held, 100)
                self.assertEqual(toggle.value, wanted)
                self.assertIs(backend._get_held(), held)
        self.assertEqual(backend.steps, 60)

    def test_other_object_toggle_and_articulated_operations_keep_hand_guard(self):
        backend, held, other, toggle, modules = self.carry_fixture()
        for primitive, target in [('toggle_on', other), ('toggle_off', other),
                                  ('open', held), ('close', held), ('open', other)]:
            with self.subTest(primitive=primitive, target=target.name), patch.dict('sys.modules', modules):
                with self.assertRaises(SkillError) as error:
                    backend._manipulation_preconditions(primitive, target)
                self.assertEqual(error.exception.code, 'hand_occupied')
        self.assertEqual(backend.steps, 0)

    def test_rejected_held_toggle_keeps_grasp_and_does_not_step(self):
        backend, held, _, toggle, modules = self.carry_fixture()
        toggle.accepted = False
        with patch.dict('sys.modules', modules), self.assertRaises(SkillError) as error:
            backend._state_action('toggle_on', held, 100)
        self.assertEqual(error.exception.code, 'execution_error')
        self.assertTrue(error.exception.changed)
        self.assertFalse(toggle.value)
        self.assertIs(backend._get_held(), held)
        self.assertEqual(backend.steps, 0)


if __name__ == '__main__':
    unittest.main()
