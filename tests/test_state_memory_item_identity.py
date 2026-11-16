"""State memory keeps one id per item across re-grasps and across controller sessions (defect fixes)."""
import json

from manipulation_agent.agent.state_memory import StateMemory


def _grasp(rev):
    return ({'primitive': 'grasp', 'target': {'image_ref': f'rgb-{rev}-front', 'point': [0.5, 0.6]}},
            {'ok': True, 'effect': {'primitive': 'grasp', 'status': 'completed'},
             'observation': {'revision': rev, 'images': []}, 'robot': {'hand': 'holding_item'}})


def _place_unverified(rev):
    return ({'primitive': 'place_on_top', 'target': {'image_ref': f'rgb-{rev}-front', 'point': [0.5, 0.4]}},
            {'ok': False, 'error': {'code': 'postcondition_error', 'stage': 'released_placement_unconfirmed',
                                    'message': 'released', 'world_may_have_changed': True},
             'observation': {'revision': rev, 'images': []}, 'robot': {'hand': 'empty'}})


def _place_ok(rev):
    return ({'primitive': 'place_on_top', 'target': {'image_ref': f'rgb-{rev}-front', 'point': [0.5, 0.4]}},
            {'ok': True, 'effect': {'primitive': 'place_on_top', 'status': 'completed'},
             'observation': {'revision': rev, 'images': []}, 'robot': {'hand': 'empty'}})


def _navigate(rev):
    return ({'primitive': 'navigate_to', 'target': {'image_ref': f'rgb-{rev}-front', 'point': [0.5, 0.8]}},
            {'ok': True, 'effect': {'primitive': 'navigate_to', 'status': 'completed', 'navigation_status': 'reached'},
             'observation': {'revision': rev, 'images': []}, 'robot': {'hand': 'empty'},
             'robot_time': {'this_call': {'travel_m': 4.0, 'travel_s': 9.0}, 'total_s': 100.0 + rev}})


def test_grasp_release_regrasp_keeps_the_same_id_and_clears_unverified():
    mem = StateMemory()
    for call in (_grasp(1), _place_unverified(2)):
        mem.observe('act', *call)
    assert mem.unverified_items() == ['item_1']
    mem.observe('act', *_grasp(3))
    assert mem.hand_holding['item'] == 'item_1'
    mem.observe('act', *_place_ok(4))
    assert mem.unverified_items() == []
    assert list(mem.object_states) == ['item_1']
    assert mem.object_states['item_1']['verified_placement'] is True


def test_grasp_elsewhere_after_unverified_release_is_a_new_item():
    mem = StateMemory()
    for call in (_grasp(1), _place_unverified(2), _navigate(3), _grasp(4)):
        mem.observe('act', *call)
    assert mem.hand_holding['item'] == 'item_2'
    assert mem.unverified_items() == ['item_1']


def _compat(call_id, args, receipt):
    return {'type': 'item.completed', 'item': {
        'id': call_id, 'type': 'mcp_tool_call', 'tool': 'act', 'arguments': args,
        'result': {'content': [{'type': 'text', 'text': json.dumps(receipt)}]}}}


def test_controller_replay_keeps_calls_whose_client_ids_restart_in_a_new_session():
    first = [_compat('item_1', *_grasp(1))]
    second = [_compat('item_1', *_place_ok(2))]  # codex restarts item ids in every session
    mem = StateMemory()
    assert mem.ingest_compat_events(first, namespace='segment0') == 1
    assert mem.ingest_compat_events(second, namespace='segment1') == 1
    assert mem.hand_holding['status'] == 'empty'
    assert mem.object_states['item_1']['verified_placement'] is True
    # Re-ingesting the same session is still de-duplicated.
    assert mem.ingest_compat_events(second, namespace='segment1') == 0


def test_controller_passes_a_distinct_namespace_per_session():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'scripts/run_codex_controller.py').read_text()
    assert "ingest_compat_events(segment_events, namespace='segment0')" in src
    assert "ingest_compat_events(segment_events, namespace=f'segment{attempt}')" in src
