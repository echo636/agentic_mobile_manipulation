"""Distinct burners heat their own contents; overlapping burners do not double heat."""
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from manipulation_agent.executors.heat_source_links import install_heat_source_links


class HeatSourceLinksTests(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        native = Mock()
        self.cls = type('HeatSourceOrSink', (), {'_update': native})
        self.native = native
        self.heat = Mock()
        self.temperature = SimpleNamespace(OBJ_IDXS={}, update_temperature_from_heatsource_or_sink=self.heat)
        module = ModuleType('omnigibson.object_states.heat_source_or_sink')
        module.HeatSourceOrSink, module.__file__ = self.cls, __file__
        states = ModuleType('omnigibson.object_states')
        states.Temperature, states.heat_source_or_sink = self.temperature, module
        self.query = Mock()
        og = ModuleType('omnigibson')
        og.sim = SimpleNamespace(psqi=SimpleNamespace(overlap_sphere=self.query))
        constants = ModuleType('omnigibson.utils.constants')
        constants.PrimType = SimpleNamespace(CLOTH='cloth')
        self.modules = {'omnigibson': og, 'omnigibson.object_states': states,
                        'omnigibson.object_states.heat_source_or_sink': module,
                        'omnigibson.utils': ModuleType('omnigibson.utils'),
                        'omnigibson.utils.constants': constants}
        override = patch.dict(sys.modules, self.modules)
        override.start()
        self.addCleanup(override.stop)
        install_heat_source_links()
        self.state = self.cls()
        self.state.requires_inside = False
        self.state.links = {x: SimpleNamespace(get_position_orientation=lambda x=x: (torch.tensor([x, 0., 0.]), None))
                            for x in [0., 1.]}
        self.state._default_link = None
        self.state.get_value = lambda: True
        self.state.distance_threshold, self.state.temperature, self.state.heating_rate = .2, 200., .04

    def test_second_burner_and_overlapping_hits_heat_once(self):
        first, second, shared, unrelated = object(), object(), object(), object()
        objects = dict(first=first, second=second, shared=shared, unrelated=unrelated)
        self.temperature.OBJ_IDXS = {obj: i for i, obj in enumerate([first, second, shared])}
        self.state.obj = SimpleNamespace(scene=SimpleNamespace(
            object_registry=lambda kind, key, default=None: objects.get(key) if kind == 'prim_path' else []))
        def query(*, radius, pos, reportFn):
            for name in (['first', 'shared', 'unrelated'] if pos[0] == 0 else ['second', 'shared']):
                reportFn(SimpleNamespace(rigid_body=name + '/base_link'))
        self.query.side_effect = query
        self.state._update()
        self.assertEqual(self.state._affected_objects, {first, second, shared})
        self.heat.assert_called_once_with(objs={first, second, shared}, temperature=200., rate=.04)

    def test_disabled_source_clears_affected_without_heating(self):
        self.state.get_value = lambda: False
        self.state._affected_objects = {object()}
        self.state._update()
        self.assertEqual(self.state._affected_objects, set())
        self.query.assert_not_called()
        self.heat.assert_not_called()

    def test_single_link_and_enclosed_sources_keep_native_update(self):
        self.state.requires_inside = True
        self.state._update()
        self.state.requires_inside = False
        self.state.links.pop(1.)
        self.state._update()
        self.assertEqual(self.native.call_count, 2)
        self.query.assert_not_called()
        self.heat.assert_not_called()
