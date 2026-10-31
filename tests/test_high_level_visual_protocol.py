"""The long-horizon hot-drink run must keep the public RGB action contract."""

from scripts.run_demo_trial import agent_profile
from manipulation_agent.tools import tool_specs
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.vision_policy import system_prompt
from types import SimpleNamespace
from unittest.mock import Mock


def test_high_level_run_uses_pixel_actions_and_explicit_navigation():
    profile = agent_profile(high_level_harness=True, semantic_magic=False)
    specs = {item['name']: item for item in tool_specs(profile)}
    assert set(specs) == {'initialize', 'look', 'act', 'finish', 'list_skills', 'read_skill'}
    target = specs['act']['inputSchema']['properties']['target']
    assert {'image_ref', 'point'} <= set(target['properties'])
    assert 'navigate_to' in specs['act']['inputSchema']['properties']['primitive']['enum']


def test_legacy_named_comparison_is_not_the_visual_protocol():
    assert agent_profile(high_level_harness=True, semantic_magic=True) == 'semantic'


def test_unified_planning_switch_preserves_pixel_action_contract():
    for enabled in (False, True):
        profile = agent_profile(high_level_harness=True, long_horizon_planning_skill=enabled)
        specs = {item['name']: item for item in tool_specs(profile)}
        assert profile == ('planning' if enabled else 'skills')
        assert {'act', 'list_skills', 'read_skill'} <= set(specs)
        assert {'image_ref', 'point'} <= set(specs['act']['inputSchema']['properties']['target']['properties'])


def test_model_is_told_navigation_requires_its_own_pixel_action():
    prompt = system_prompt('minimal')
    assert 'primitive=navigate_to' in prompt
    assert 'No manipulation action moves the base for you' in prompt
    assert 'Do not report blocked solely because an earlier pixel or action failed' in prompt


def test_pixel_grounded_operation_does_not_trigger_hidden_navigation(tmp_path):
    target = {'image_ref': 'rgb-00001-front', 'point': [0.4, 0.5]}
    selected = SimpleNamespace(category='mug', states={})
    backend = RGBBackend.__new__(RGBBackend)
    backend.output = tmp_path
    backend.high_level_harness = True
    backend.semantic_magic = False
    backend._demo_focus = None
    backend._ground = Mock(return_value=(selected, 'point', {'selected_pixel': target['point']}))
    backend._semantic_targets = Mock(return_value={'mug': selected})
    backend._execute_high_level_semantic = Mock(return_value={'primitive': 'grasp'})
    backend._navigate = Mock()

    result = backend.execute_visual('grasp', target, 100)

    backend._ground.assert_called_once_with(target, require_object=True)
    backend._execute_high_level_semantic.assert_called_once_with(
        'grasp', 'mug', 100, auto_navigate=False, selected_object=selected)
    backend._navigate.assert_not_called()
    assert result['grounded_category'] == 'mug'


def test_visual_worktop_alias_keeps_the_model_selected_surface(tmp_path):
    target = {'image_ref': 'rgb-00001-front', 'point': [0.4, 0.5]}
    chosen_worktop = SimpleNamespace(category='countertop', states={})
    bound_worktop = SimpleNamespace(category='countertop', states={})
    backend = RGBBackend.__new__(RGBBackend)
    backend.output = tmp_path
    backend.high_level_harness = True
    backend.semantic_magic = False
    backend._demo_focus = None
    backend._ground = Mock(return_value=(chosen_worktop, 'point', {'selected_pixel': target['point']}))
    backend._semantic_targets = Mock(return_value={'countertop': bound_worktop})
    backend._execute_high_level_semantic = Mock(return_value={'primitive': 'place_on_top'})

    backend.execute_visual('place_on_top', target, 100)

    backend._execute_high_level_semantic.assert_called_once_with(
        'place_on_top', 'countertop', 100, auto_navigate=False,
        selected_object=chosen_worktop)


def test_navigation_is_its_own_pixel_selected_action(tmp_path):
    target = {'image_ref': 'rgb-00001-front', 'point': [0.4, 0.5]}
    backend = RGBBackend.__new__(RGBBackend)
    backend.output = tmp_path
    backend.high_level_harness = True
    backend.semantic_magic = False
    backend._demo_focus = None
    selected_floor = SimpleNamespace(category='floors')
    backend._ground = Mock(return_value=(selected_floor, 'point', {'selected_pixel': target['point']}))
    backend._navigate = Mock(return_value={'nav_status': 'reached'})

    result = backend.execute_visual('navigate_to', target, 100)

    backend._ground.assert_called_once_with(target, require_object=True)
    backend._navigate.assert_called_once()
    anchor, max_steps = backend._navigate.call_args.args
    assert anchor.selected_object is selected_floor
    assert anchor.aabb == ('point', 'point')
    assert max_steps == 100
    assert result['nav_status'] == 'reached'
