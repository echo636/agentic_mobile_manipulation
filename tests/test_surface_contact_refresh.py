"""Release-time contact refresh must not turn a geometric failure into success."""
from types import SimpleNamespace
import unittest

from manipulation_agent.executors.placement import CheckedPlacement


class SurfaceContactRefreshTests(unittest.TestCase):
    def run_settling(self, supported, budget):
        class State:
            def __init__(self, owner):
                self.owner = owner
                self.cached = False  # A cached pre-contact result.

            def clear_cache(self):
                self.cached = None

            def get_value(self):
                if self.cached is None:
                    self.cached = self.owner.contact
                return self.cached

        class Body:
            def __init__(self, physically_supported):
                self.supported = physically_supported
                self.awake_for = 0
                self.contact = False
                self.states = {'contact': State(self)}

            def wake(self):
                self.awake_for = 10

            def step(self):
                self.contact = self.supported and self.awake_for > 0
                self.awake_for = max(0, self.awake_for - 1)

        held, child, target = Body(supported), Body(True), Body(True)
        calls = []

        def step(action):
            calls.append(action)
            held.step()
            child.step()
            # State may be queried while processing an ordinary scene step.
            held.states['contact'].get_value()

        backend = SimpleNamespace(
            robot=SimpleNamespace(get_joint_positions=lambda: [], q_to_action=lambda q: q),
            _step=step,
        )
        CheckedPlacement._settle_surface_contacts(backend, held, target, [child], budget)
        return held.states['contact'].get_value(), child.states['contact'].get_value(), len(calls)

    def test_sleeping_released_assembly_gets_fresh_contact_without_extra_steps(self):
        self.assertEqual(self.run_settling(True, 100), (True, True, 50))

    def test_refresh_preserves_real_support_failure(self):
        self.assertEqual(self.run_settling(False, 100), (False, True, 50))

    def test_refresh_respects_a_short_step_budget(self):
        self.assertEqual(self.run_settling(True, 2), (True, True, 2))


if __name__ == '__main__':
    unittest.main()
