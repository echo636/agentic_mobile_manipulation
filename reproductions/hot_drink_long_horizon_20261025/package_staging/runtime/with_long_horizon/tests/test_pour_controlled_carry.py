"""The RGB pour action must see the object held by controlled carry."""
import sys
import types
import unittest
from unittest.mock import patch

from manipulation_agent.omnigibson_backend import OmniGibsonBackend
from manipulation_agent.executors.placement import CheckedPlacement
from manipulation_agent.contracts import SkillError


class PourControlledCarryTests(unittest.TestCase):
    def test_pour_clears_source_before_generating_destination_particles(self):
        Contains = type("Contains", (), {})
        ContainedParticles = type("ContainedParticles", (), {})
        Filled = type("Filled", (), {})
        water = types.SimpleNamespace(name="cooked__water")
        particle_count = {"source": 10, "destination": 0}

        def clear_source(system, value):
            self.assertIs(system, water)
            self.assertFalse(value)
            particle_count["source"] = 0
            # Nearby carried containers can overlap. The source's volume
            # removal then catches destination particles from the same system.
            particle_count["destination"] = 0
            return True

        def fill_destination(system, value):
            self.assertIs(system, water)
            self.assertTrue(value)
            particle_count["destination"] += 10
            return True

        source = types.SimpleNamespace(states={
            Contains: types.SimpleNamespace(
                get_value=lambda system: particle_count["source"] > 0,
                set_value=clear_source),
            ContainedParticles: types.SimpleNamespace(link=types.SimpleNamespace(volume=0.25)),
        })
        target = types.SimpleNamespace(states={
            Filled: types.SimpleNamespace(
                get_value=lambda system: particle_count["destination"] >= 20,
                set_value=fill_destination),
            Contains: types.SimpleNamespace(
                get_value=lambda system: particle_count["destination"] > 0),
            ContainedParticles: types.SimpleNamespace(link=types.SimpleNamespace(volume=1.0)),
        })
        backend = OmniGibsonBackend.__new__(OmniGibsonBackend)
        backend._get_held = lambda: source
        backend.env = types.SimpleNamespace(
            scene=types.SimpleNamespace(system_registry=types.SimpleNamespace(objects=[water])))
        backend.robot = types.SimpleNamespace(get_joint_positions=lambda: [], q_to_action=lambda joints: joints)
        backend.steps = 0
        backend._step = lambda action: setattr(backend, "steps", backend.steps + 1)
        states = types.ModuleType("omnigibson.object_states")
        states.Contains, states.Filled, states.ContainedParticles = Contains, Filled, ContainedParticles
        with patch.dict(sys.modules, {"omnigibson": types.ModuleType("omnigibson"),
                                     "omnigibson.object_states": states}):
            result = backend._pour_into(target, 1)
        self.assertEqual(result["primitive"], "pour")
        self.assertTrue(result["destination_refilled_after_settle"])
        self.assertEqual(particle_count, {"source": 0, "destination": 20})

    def test_pour_uses_controlled_held_source(self):
        Contains = type("Contains", (), {})
        ContainedParticles = type("ContainedParticles", (), {})
        Filled = type("Filled", (), {})
        water = types.SimpleNamespace(name="water")
        source_contains = {water.name: True}
        target_filled = {water.name: False}
        source = types.SimpleNamespace(states={
            Contains: types.SimpleNamespace(
                get_value=lambda system: source_contains[system.name],
                set_value=lambda system, value: source_contains.__setitem__(system.name, value) or True),
        })
        target = types.SimpleNamespace(states={
            Filled: types.SimpleNamespace(
                get_value=lambda system: target_filled[system.name],
                set_value=lambda system, value: target_filled.__setitem__(system.name, value) or True),
            Contains: types.SimpleNamespace(
                get_value=lambda system: target_filled[system.name]),
        })
        backend = OmniGibsonBackend.__new__(OmniGibsonBackend)
        backend._get_held = lambda: source
        backend.primitives = types.SimpleNamespace(_get_obj_in_hand=lambda: None)
        backend.env = types.SimpleNamespace(
            scene=types.SimpleNamespace(system_registry=types.SimpleNamespace(objects=[water])))
        backend.robot = types.SimpleNamespace(get_joint_positions=lambda: [], q_to_action=lambda joints: joints)
        backend.steps = 0
        backend._step = lambda action: setattr(backend, "steps", backend.steps + 1)
        states = types.ModuleType("omnigibson.object_states")
        states.Contains, states.Filled, states.ContainedParticles = Contains, Filled, ContainedParticles
        with patch.dict(sys.modules, {"omnigibson": types.ModuleType("omnigibson"),
                                     "omnigibson.object_states": states}):
            result = backend._pour_into(target, 1)
        self.assertEqual(result["primitive"], "pour")
        self.assertTrue(target_filled[water.name])
        self.assertFalse(source_contains[water.name])

    def test_smaller_destination_receives_liquid_without_emptying_large_source(self):
        Contains = type("Contains", (), {})
        ContainedParticles = type("ContainedParticles", (), {})
        Filled = type("Filled", (), {})
        cooked = types.SimpleNamespace(name="cooked__water")
        remaining = {"source_contains": True, "source_filled": False,
                     "destination_contains": False,
                     "destination_filled": False}
        source = types.SimpleNamespace(states={
            Contains: types.SimpleNamespace(
                get_value=lambda system: remaining["source_contains"],
                set_value=lambda system, value: remaining.__setitem__("source_contains", value) or True),
            Filled: types.SimpleNamespace(
                get_value=lambda system: remaining["source_filled"],
                set_value=lambda system, value: remaining.__setitem__("source_filled", value) or True),
            ContainedParticles: types.SimpleNamespace(link=types.SimpleNamespace(volume=1.0)),
        })

        def fill_small_destination(system, value):
            remaining["destination_contains"] = True
            return True

        target = types.SimpleNamespace(states={
            Contains: types.SimpleNamespace(
                get_value=lambda system: remaining["destination_contains"]),
            Filled: types.SimpleNamespace(
                get_value=lambda system: remaining["destination_filled"],
                set_value=fill_small_destination),
            ContainedParticles: types.SimpleNamespace(link=types.SimpleNamespace(volume=0.25)),
        })
        backend = OmniGibsonBackend.__new__(OmniGibsonBackend)
        backend._get_held = lambda: source
        backend.env = types.SimpleNamespace(
            scene=types.SimpleNamespace(system_registry=types.SimpleNamespace(objects=[cooked])))
        backend.robot = types.SimpleNamespace(get_joint_positions=lambda: [], q_to_action=lambda joints: joints)
        backend.steps = 0
        backend._step = lambda action: setattr(backend, "steps", backend.steps + 1)
        states = types.ModuleType("omnigibson.object_states")
        states.Contains, states.Filled, states.ContainedParticles = Contains, Filled, ContainedParticles
        with patch.dict(sys.modules, {"omnigibson": types.ModuleType("omnigibson"),
                                     "omnigibson.object_states": states}):
            result = backend._pour_into(target, 1)
        self.assertTrue(result["partial_pour"])
        self.assertTrue(result["source_restored_after_leak"])
        self.assertTrue(remaining["source_contains"])
        self.assertTrue(remaining["source_filled"])
        self.assertTrue(remaining["destination_contains"])
        self.assertFalse(remaining["destination_filled"])

    def test_heating_placement_uses_checked_controlled_carry(self):
        HeatSourceOrSink = type("HeatSourceOrSink", (), {})
        point = types.SimpleNamespace(tolist=lambda: [0.0, 0.0, 0.0])
        target = types.SimpleNamespace(states={
            HeatSourceOrSink: types.SimpleNamespace(
                requires_inside=False, distance_threshold=0.2,
                links={"burner": types.SimpleNamespace(get_position_orientation=lambda: (point, None))})})
        placement = CheckedPlacement()
        calls = []
        placement._get_held = lambda: types.SimpleNamespace(aabb=(
            types.SimpleNamespace(tolist=lambda: [-0.05, -0.05, 0.0]),
            types.SimpleNamespace(tolist=lambda: [0.05, 0.05, 0.1])))
        placement._checked_place_on_top = lambda obj, steps, point: (
            calls.append((obj, steps, point)) or {"primitive": "place_on_top"})
        states = types.ModuleType("omnigibson.object_states")
        states.HeatSourceOrSink = HeatSourceOrSink
        with patch.dict(sys.modules, {"omnigibson": types.ModuleType("omnigibson"),
                                     "omnigibson.object_states": states}):
            result = placement._checked_place_near_heating_element(target, 40, (1, 2))
        self.assertEqual(calls, [(target, 40, (1, 2))])
        self.assertEqual(result["primitive"], "place_near_heating_element")

    def test_heating_placement_rejects_surface_outside_element_range(self):
        HeatSourceOrSink = type("HeatSourceOrSink", (), {})
        point = types.SimpleNamespace(tolist=lambda: [1.0, 0.0, 0.0])
        target = types.SimpleNamespace(states={
            HeatSourceOrSink: types.SimpleNamespace(
                requires_inside=False, distance_threshold=0.2,
                links={"burner": types.SimpleNamespace(get_position_orientation=lambda: (point, None))})})
        placement = CheckedPlacement()
        placement._get_held = lambda: types.SimpleNamespace(aabb=(
            types.SimpleNamespace(tolist=lambda: [-0.05, -0.05, 0.0]),
            types.SimpleNamespace(tolist=lambda: [0.05, 0.05, 0.1])))
        placement._checked_place_on_top = lambda *args: {"primitive": "place_on_top"}
        states = types.ModuleType("omnigibson.object_states")
        states.HeatSourceOrSink = HeatSourceOrSink
        with patch.dict(sys.modules, {"omnigibson": types.ModuleType("omnigibson"),
                                     "omnigibson.object_states": states}):
            with self.assertRaises(SkillError) as error:
                placement._checked_place_near_heating_element(target, 40, (1, 2))
        self.assertEqual(error.exception.code, "postcondition_error")


if __name__ == "__main__":
    unittest.main()
