"""Replay must stop when the historical prefix no longer matches."""
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock,patch

PROBE=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/probe_navigation_prefix.py'))


class NavigationPrefixProbeTests(TestCase):
    def test_wrong_object_or_nonfinite_hit_cannot_pass_grounding_gate(self):
        expected={'selected_object_prim':'bag81','hit_position':[1.,2.,3.]}
        matches=PROBE['grounding_matches']
        self.assertFalse(matches(expected,{'selected_object_prim':'bag82','hit_position':[1.,2.,3.]},.05))
        self.assertFalse(matches(expected,{'selected_object_prim':'bag81','hit_position':[float('nan'),2.,3.]},.05))
        self.assertFalse(matches(expected,{'selected_object_prim':'bag81','hit_position':[1.2,2.,3.]},.05))

    def test_grounding_divergence_stops_before_executing_changed_target(self):
        expected={'selected_object_prim':'bag81','hit_position':[1.,2.,3.]}
        wrong={'selected_object_prim':'floor','hit_position':[1.,2.,3.]}
        backend=SimpleNamespace(provenance=lambda:{'input_hashes':{'bddl_definition':'frozen'},'demo_motion':False},
            observe=lambda:{'images':[{'view':'front','image_ref':'fresh-front'}]},
            _ground=Mock(return_value=(None,None,wrong)),execute_visual=Mock(),
            finalize_video=lambda:{'status':'fixture'},close=Mock(),steps=0)
        plan={'task':'carrying_in_groceries','instance':301,'seed':0,'required_input_hashes':{'bddl_definition':'frozen'},
            'grounding_position_tolerance_m':.05,'actions':[{'original_event':'original',
            'arguments':{'primitive':'grasp','target':{'image_ref':'old-front','point':[.5,.5]}},
            'grounding':expected,'expected_ok':True,'expected_error':None}]}
        with tempfile.TemporaryDirectory() as parent:
            with patch('manipulation_agent.executors.omnigibson_rgb.RGBBackend',return_value=backend):
                result=PROBE['execute'](plan,Path(parent)/'probe')
        self.assertEqual(result['status'],'prefix_diverged')
        self.assertEqual(result['reason'],'grounding')
        backend.execute_visual.assert_not_called()
        backend.close.assert_called_once()

    def test_wrong_scene_rejects_before_any_action(self):
        backend=SimpleNamespace(provenance=lambda:{'input_hashes':{'scene_template':'different'}},close=Mock())
        plan={'task':'carrying_in_groceries','instance':301,'seed':0,'required_input_hashes':{'scene_template':'frozen'},'actions':[]}
        with tempfile.TemporaryDirectory() as parent:
            with patch('manipulation_agent.executors.omnigibson_rgb.RGBBackend',return_value=backend):
                with self.assertRaisesRegex(ValueError,'input hashes differ'):
                    PROBE['execute'](plan,Path(parent)/'probe')
        backend.close.assert_called_once()
