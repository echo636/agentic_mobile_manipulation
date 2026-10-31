import math
from contextlib import nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.topology_rebuild import _state_mismatches, rebuild_cut_topology


class CutStateComparisonTests(unittest.TestCase):
    torch = SimpleNamespace(Tensor=())

    def state(self, value):
        return {0: {'registry': {'object_registry': {'cut_piece': {'non_kin': {
            'MaxTemperature': {'max_temperature': value}}}}}}}

    def test_native_initial_max_temperature_is_preserved(self):
        self.assertEqual(_state_mismatches(self.state(-math.inf), self.state(-math.inf), self.torch), [])

    def test_changed_or_invalid_temperature_still_fails(self):
        for before, after in [(-math.inf, 23.), (23., -math.inf),
                              (math.nan, math.nan), (math.inf, math.inf), (23., 24.), (-math.inf, None)]:
            with self.subTest(before=before, after=after):
                self.assertTrue(_state_mismatches(self.state(before), self.state(after), self.torch))

    def test_nonfinite_other_state_is_not_a_temperature_sentinel(self):
        self.assertTrue(_state_mismatches({'position': -math.inf}, {'position': -math.inf}, self.torch))
        self.assertTrue(_state_mismatches({'max_temperature': -math.inf}, {'max_temperature': -math.inf}, self.torch))
        self.assertEqual(_state_mismatches({'temperature': 23.}, {'temperature': 23.}, self.torch), [])

    def test_scene_reload_render_does_not_advance_rebuilt_timeline(self):
        class Timeline:
            time = 156.46667482703924

            def get_current_time(self):
                return self.time

            def set_current_time(self, value):
                self.time = value

            def commit(self):
                pass

        class Scene:
            idx = 0
            transition_rule_api = SimpleNamespace(refresh_all_rules=lambda: None)

        scene = Scene()
        obj = SimpleNamespace(name='half_log', scene=scene, states={},
                              initialize=lambda: None, keep_still=lambda: None)
        scene.objects = [obj]
        context = SimpleNamespace(_timeline=Timeline(), _current_time=147.67500770185143,
                                  _number_of_steps=17721, _physics_callback_functions={})

        class Sim:
            _sim_context = context
            _objects_to_initialize = [obj]
            object_state_types_on_joint_break = set()
            scenes = [scene]

            current_time = property(lambda self: context._current_time)
            current_time_step_index = property(lambda self: context._number_of_steps)

            def render(self):
                pass

            update_handles = render

            def stop(self):
                context._current_time = context._number_of_steps = 0
                context._timeline.time = 0.

            play = render

            def dump_state(self, serialized):
                return {0: {'registry': {'object_registry': {'half_log': {'position': 1.}}}}}

            def load_state(self, state, serialized):
                context._timeline.time += 1 / 30

        records = []
        backend = SimpleNamespace(og=SimpleNamespace(sim=Sim()), steps=1915,
                                  torch=self.torch, _placement_record=records.append)
        gm = SimpleNamespace(ENABLE_OBJECT_STATES=True, ENABLE_TRANSITION_RULES=True,
                             unlocked=nullcontext)
        with patch.dict('sys.modules', {
                'omnigibson.macros': SimpleNamespace(gm=gm),
                'omnigibson.prims.entity_prim': SimpleNamespace(EntityPrim=object)}):
            rebuild_cut_topology(backend)
        self.assertEqual(context._timeline.time, 156.46667482703924)
        self.assertEqual(context._current_time, 147.67500770185143)
        self.assertEqual(context._number_of_steps, 17721)
        self.assertEqual(records[-1]['status'], 'passed')
