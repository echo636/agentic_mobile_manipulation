from types import SimpleNamespace
import unittest

from manipulation_agent.executors.topology_rebuild import rebuild_cut_topology


class CutTopologyTests(unittest.TestCase):
    def test_refresh_does_not_restart_physics_or_reload_the_scene(self):
        events = []

        class Scene:
            transition_rule_api = SimpleNamespace(refresh_all_rules=lambda: events.append('rules'))

        obj = SimpleNamespace(name='half_pepper', scene=Scene(), states={},
                              initialize=lambda: events.append('initialize'),
                              keep_still=lambda: None)

        class Sim:
            _objects_to_initialize = [obj]
            object_state_types_on_joint_break = set()
            current_time = 12.
            held = 'knife'

            def render(self):
                pass

            def update_handles(self):
                events.append('handles')

            def stop(self, *args, **kwargs):
                raise AssertionError('Cut must not restart or restore the world')

            play = dump_state = load_state = stop

        sim = Sim()
        backend = SimpleNamespace(og=SimpleNamespace(sim=sim), steps=31,
                                  _placement_record=lambda record: None)
        rebuild_cut_topology(backend)
        self.assertEqual(events, ['initialize', 'handles', 'rules'])
        self.assertEqual(sim._objects_to_initialize, [])
        self.assertEqual((sim.current_time, sim.held, backend.steps), (12., 'knife', 31))
