"""Exercise the real placement wrapper across legacy limits, without a simulator."""
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.executors.placement import CheckedPlacement


class VolumeSamplerBudgetTests(unittest.TestCase):
    def fixture(self, deadline, ticks, *, elapsed=0., expire_at=None):
        class Inside:
            pass

        class Pose:
            def clone(self):
                return self

        clock = {'elapsed': 0., 'unix': 1000., 'physics': 0}
        def physics():
            clock['physics'] += 1
        sim = SimpleNamespace(step_physics=physics)
        def sample(target, wanted):
            clock['elapsed'] = elapsed
            for tick in range(ticks):
                if tick == expire_at:
                    clock['unix'] = 2801.
                sim.step_physics()
            return True
        state = SimpleNamespace(set_value=sample, get_value=lambda target: True)
        held = SimpleNamespace(states={Inside: state}, get_position_orientation=lambda: (Pose(), Pose()))
        target = SimpleNamespace(fixed_base=True, links={
            'volume': SimpleNamespace(is_meta_link=True, meta_link_type='fillable')})
        holder = {'object': held}
        backend = CheckedPlacement()
        backend.og, backend.deadline = SimpleNamespace(sim=sim), deadline
        backend.sampling_physics_steps = backend.frames_revision = 0
        backend.ideal_carry = True
        backend._carry_contents, backend._carry_dependencies = [], []
        backend.robot = SimpleNamespace(get_joint_positions=lambda: 0, q_to_action=lambda q: q)
        backend._get_held = lambda: holder['object']
        backend._carry_detach = lambda: holder.update(object=None)
        backend._container_payload = lambda container: []
        backend._placement_context = lambda container: nullcontext()
        backend._relocate_contents = lambda obj, contents: None
        backend._step = lambda action: None
        backend._verify_container_payload = lambda container, contents: None
        backend._verify_payload = lambda dependencies: None
        states = ModuleType('omnigibson.object_states')
        states.Inside = Inside
        usd = ModuleType('omnigibson.utils.usd_utils')
        usd.RigidContactAPI = SimpleNamespace(is_in_contact=lambda *args: False)
        modules = {'omnigibson.object_states': states, 'omnigibson.utils.usd_utils': usd}
        return backend, target, sim, physics, clock, modules

    def run_sampler(self, fixture, max_steps):
        backend, target, _, _, clock, modules = fixture
        with patch.dict('sys.modules', modules), \
                patch('manipulation_agent.executors.placement.time.monotonic',
                      side_effect=lambda: clock['elapsed']), \
                patch('manipulation_agent.deadline.time.time', side_effect=lambda: clock['unix']):
            return backend._checked_place_inside(target, max_steps)

    def test_managed_sampler_exceeds_all_legacy_limits_with_time_remaining(self):
        fixture = self.fixture(EpisodeDeadline(2800.), 6001, elapsed=181.)
        result = self.run_sampler(fixture, max_steps=1)
        backend, _, sim, physics, clock, _ = fixture
        self.assertEqual(result['sampling_physics_steps'], 6001)
        self.assertEqual(clock['physics'], 6001)
        self.assertEqual(backend.sampling_physics_steps, 6001)
        self.assertIs(sim.step_physics, physics)

    def test_managed_sampler_stops_at_actual_episode_deadline_and_restores_physics(self):
        fixture = self.fixture(EpisodeDeadline(2800.), 6001, elapsed=181., expire_at=10)
        with self.assertRaises(SkillError) as error:
            self.run_sampler(fixture, max_steps=1)
        self.assertEqual(error.exception.code, 'episode_timeout')
        _, _, sim, physics, clock, _ = fixture
        self.assertEqual(clock['physics'], 10)
        self.assertIs(sim.step_physics, physics)

    def test_unmanaged_sampler_retains_explicit_time_and_step_caps(self):
        for ticks, elapsed, max_steps, executed in ((1, 181., 100, 0),
                                                   (5, 0., 1, 4), (6001, 0., 2000, 6000)):
            with self.subTest(ticks=ticks, elapsed=elapsed, max_steps=max_steps):
                fixture = self.fixture(EpisodeDeadline(), ticks, elapsed=elapsed)
                with self.assertRaises(SkillError) as error:
                    self.run_sampler(fixture, max_steps)
                self.assertEqual(error.exception.code, 'sampling_budget_exhausted')
                _, _, sim, physics, clock, _ = fixture
                self.assertEqual(clock['physics'], executed)
                self.assertIs(sim.step_physics, physics)


if __name__ == '__main__':
    unittest.main()
