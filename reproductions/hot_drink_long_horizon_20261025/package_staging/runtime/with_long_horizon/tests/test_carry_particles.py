"""Physical liquid in a carried container must follow its selected vessel."""
import sys
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch

import torch

from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.contracts import SkillError


class CarryParticleTests(unittest.TestCase):
    def test_fill_repair_recovers_when_contact_filtered_sampling_adds_too_little(self):
        Filled = type('Filled', (), {})
        system = types.SimpleNamespace(name='cooked__water')
        state = {'filled': False, 'fallback_calls': []}
        link = types.SimpleNamespace()
        carrier = types.SimpleNamespace(name='kettle', states={
            Filled: types.SimpleNamespace(
                get_value=lambda _: state['filled'],
                set_value=lambda _, value: True)})
        carrier.states['contained'] = types.SimpleNamespace(link=link)
        def generate_particles_from_link(**kwargs):
            state['fallback_calls'].append(kwargs)
            state['filled'] = True
        system.generate_particles_from_link = generate_particles_from_link
        carry = ControlledCarry()
        carry._carry_record = lambda **entry: None
        states = types.ModuleType('omnigibson.object_states')
        states.Filled = Filled
        states.ContainedParticles = 'contained'
        with patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                      'omnigibson.object_states': states}):
            carry._restore_expected_fill(carrier, [system])
        self.assertTrue(state['filled'])
        self.assertEqual(len(state['fallback_calls']), 1)
        self.assertIs(state['fallback_calls'][0]['obj'], carrier)
        self.assertIs(state['fallback_calls'][0]['link'], link)
        self.assertFalse(state['fallback_calls'][0]['check_contact'])

    def test_failed_fill_check_does_not_pin_carried_kettle_to_old_surface(self):
        kettle = types.SimpleNamespace(name='kettle')
        system = types.SimpleNamespace(name='cooked__water')
        sim = types.SimpleNamespace(dump_state=lambda **kwargs: {},
                                    load_state=lambda state, **kwargs: None)
        carry = ControlledCarry()
        carry.og = types.SimpleNamespace(sim=sim)
        carry._anchored_operation = lambda target: nullcontext()
        carry._placement_record = lambda row: None
        carry._carry_follow = lambda: None
        carry.ideal_carry = True
        carry._ideal_held = kettle
        carry._carry_relative = 'relative'
        carry._carry_orientation = None
        carry._carry_contents = []
        carry._carry_dependencies = []
        carry._carry_particle_carrier = kettle
        carry._carry_particle_contents = []
        carry._carry_filled_systems = [system]
        carry._stabilized_containers = {}
        carry._restore_expected_fill = lambda *_: (_ for _ in ()).throw(
            SkillError('postcondition_error', 'Carried container lost its filled liquid state'))
        with self.assertRaises(SkillError):
            with carry._placement_context():
                carry._carry_detach()
                carry._stabilized_containers[kettle.name] = 'old countertop pose'
        self.assertEqual(carry._stabilized_containers, {})
        self.assertIs(carry._ideal_held, kettle)

    def test_release_restores_a_previously_filled_liquid_container(self):
        Filled = type('Filled', (), {})
        system = types.SimpleNamespace(name='cooked__water')
        state = {'filled': False}
        carrier = types.SimpleNamespace(name='kettle', states={
            Filled: types.SimpleNamespace(
                get_value=lambda _: state['filled'],
                set_value=lambda _, value: state.__setitem__('filled', value) or True)})
        records = []
        carry = ControlledCarry()
        carry._carry_record = lambda **entry: records.append(entry)
        states = types.ModuleType('omnigibson.object_states')
        states.Filled = Filled
        states.ContainedParticles = type('ContainedParticles', (), {})
        with patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                      'omnigibson.object_states': states}):
            carry._restore_expected_fill(carrier, [system])
        self.assertTrue(state['filled'])
        self.assertEqual(records[0]['status'], 'liquid_fill_restored_after_placement')

    def test_only_particles_inside_the_carried_container_move_with_it(self):
        ContainedParticles = type('ContainedParticles', (), {})
        positions = torch.tensor([[0.0, 0.0, 0.0], [9.0, 9.0, 9.0], [0.1, 0.0, 0.0]])
        system = types.SimpleNamespace(name='water', initialized=True, n_particles=3)
        def set_positions(*, positions):
            system.positions = positions
        system.set_particles_position_orientation = set_positions
        system.positions = positions
        system.get_particles_position_orientation = lambda: (system.positions, None)

        pose = [torch.zeros(3), torch.tensor([0.0, 0.0, 0.0, 1.0])]
        vessel = types.SimpleNamespace(states={
            ContainedParticles: types.SimpleNamespace(
                get_value=lambda _: types.SimpleNamespace(
                    in_volume=torch.tensor([True, False, True])))})
        vessel.get_position_orientation = lambda: tuple(pose)
        scene = types.SimpleNamespace(
            system_registry=types.SimpleNamespace(objects=[system]),
            is_physical_particle_system=lambda system_name: system_name == 'water')
        carry = ControlledCarry()
        carry.env = types.SimpleNamespace(scene=scene)
        carry.torch = torch

        states = types.ModuleType('omnigibson.object_states')
        states.ContainedParticles = ContainedParticles
        transform = types.ModuleType('omnigibson.utils.transform_utils')
        transform.quat2mat = lambda _: torch.eye(3)
        utils = types.ModuleType('omnigibson.utils')
        utils.transform_utils = transform
        with patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                      'omnigibson.object_states': states,
                                      'omnigibson.utils': utils,
                                      'omnigibson.utils.transform_utils': transform}):
            carry._carry_particle_carrier = vessel
            carry._carry_particle_contents = carry._grasp_particle_payload(vessel)
            pose[0] = torch.tensor([1.0, 2.0, 3.0])
            carry._relocate_contents(vessel, [])
            carry.ideal_carry = True
            carry._ideal_held = vessel
            carry._carry_detach()
            pose[0] = torch.tensor([2.0, 3.0, 4.0])
            carry._relocate_contents(vessel, [])

        self.assertEqual(len(carry._carry_particle_contents), 1)
        self.assertTrue(torch.allclose(system.positions[0], torch.tensor([2.0, 3.0, 4.0])))
        self.assertTrue(torch.allclose(system.positions[1], torch.tensor([9.0, 9.0, 9.0])))
        self.assertTrue(torch.allclose(system.positions[2], torch.tensor([2.1, 3.0, 4.0])))


if __name__ == '__main__':
    unittest.main()
