"""A geometrically satisfied goal can still leave a demo object in hand."""
import json

from scripts.audit_demo_trial import (base_height_report, explicit_navigation_report,
                                      hand_empty_after_events, motion_checks,
                                      replay_high_level_actions)
from manipulation_agent.high_level_hot_drink import HighLevelHotDrink


def result(primitive, ok=True):
    return {'kind': 'tool_result', 'name': 'act',
            'result': {'ok': ok, 'effect': {'primitive': primitive}}}


def test_failed_final_placement_does_not_count_as_releasing_the_item():
    events = [result('grasp'), result('place_inside', False),
              result('place_inside', False), {'kind': 'tool_call', 'name': 'finish'}]
    assert hand_empty_after_events(events) is False


def test_successful_placement_or_release_clears_the_carry():
    assert hand_empty_after_events([result('grasp'), result('place_inside')]) is True
    assert hand_empty_after_events([result('grasp'), result('release')]) is True
    assert hand_empty_after_events([result('grasp'), result('place_inside'),
                                    result('grasp')]) is False


def test_semantic_actions_count_toward_hand_empty_audit():
    grasp = {**result('grasp'), 'name': 'act_semantic'}
    place = {**result('place_on_top'), 'name': 'act_semantic'}
    assert hand_empty_after_events([grasp]) is False
    assert hand_empty_after_events([grasp, place]) is True


def test_motion_audit_uses_the_recorded_mode():
    shown = [{'status': 'shown'}]
    assert all(motion_checks({'evaluation': {'demo_motion': True}},
                             {'demo_motion': True}, shown).values())
    assert all(motion_checks({'evaluation': {'demo_motion': False}},
                             {'demo_motion': False}, []).values())
    assert not all(motion_checks({'evaluation': {'demo_motion': False}},
                                 {'demo_motion': True}, []).values())
    assert not all(motion_checks({'evaluation': {'demo_motion': False}},
                                 {'demo_motion': False}, shown).values())


def test_base_height_audit_rejects_robot_below_floor(tmp_path):
    rows = [{'actual_position': [0, 0, 0.005]},
            {'actual_position': [1, 0, -1.4]}]
    (tmp_path / 'base_motion.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in rows))
    report = base_height_report(tmp_path)
    assert report['stable'] is False
    assert report['max_deviation_m'] > 1


def test_pixel_action_replay_uses_selected_pixel_and_private_grounding():
    pixel = {'image_ref': 'rgb-00002-front', 'point': [0.2, 0.3]}
    state = HighLevelHotDrink()
    state.apply('open', 'refrigerator')
    events = [
        {'kind': 'tool_call', 'name': 'act', 'arguments': {
            'primitive': 'open', 'target': pixel}},
        {'kind': 'private_executor_result', 'details': {
            'semantic_target': 'refrigerator',
            'private_grounding': {'selected_pixel': [0.2, 0.3]}}},
        {'kind': 'tool_result', 'name': 'act', 'result': {'ok': True}},
    ]
    report = replay_high_level_actions(events, [{'state': state.snapshot()}])
    assert report['committed_actions'] == 1
    assert report['private_state_log_matches'] is True


def test_legacy_closed_cabinet_trace_still_replays():
    old_state = HighLevelHotDrink(initial_open=())
    old_state.apply('open', 'refrigerator')
    events = [
        {'kind': 'tool_call', 'name': 'act_semantic', 'arguments': {
            'primitive': 'open', 'target': 'refrigerator'}},
        {'kind': 'private_executor_result', 'details': {
            'semantic_target': 'refrigerator'}},
        {'kind': 'tool_result', 'name': 'act_semantic', 'result': {'ok': True}},
    ]
    report = replay_high_level_actions(events, [{'state': old_state.snapshot()}])
    assert report['private_state_log_matches'] is True


def test_hidden_navigation_fails_visual_protocol_audit():
    nav = {'kind': 'tool_call', 'name': 'act', 'at': '2026-01-01T00:00:00Z',
           'arguments': {'primitive': 'navigate_to'}}
    end = {'kind': 'tool_result', 'name': 'act', 'at': '2026-01-01T00:00:02Z'}
    plan = {'at': '2026-01-01T00:00:01Z'}
    assert explicit_navigation_report([nav, end], [plan])['all_explicit'] is True
    nav['arguments']['primitive'] = 'grasp'
    assert explicit_navigation_report([nav, end], [plan])['all_explicit'] is False
