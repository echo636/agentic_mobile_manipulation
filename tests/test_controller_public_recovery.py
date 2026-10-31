import json
from manipulation_agent.clients.recovery import public_action_history


def completed(primitive, feedback, *, item_id='reused'):
    return {'type': 'item.completed', 'item': {'id': item_id, 'type': 'mcp_tool_call',
        'server': 'manipulation', 'tool': 'act', 'arguments': {'primitive': primitive},
        'result': {'content': [{'type': 'image', 'data': 'old-image-bytes'},
            {'type': 'text', 'text': json.dumps(feedback)}]}}}


def test_recovery_preserves_prior_grasp_and_rejection_without_images_or_private_fields():
    events = [completed('grasp', {'ok': True, 'effect': {
        'grasped_category': 'paper_bag', 'instance_id': 'private'},
        'observation': {'image': 'old-image'}, 'evaluation': {'task_success': True}}),
        completed('open', {'ok': False, 'error': {'code': 'hand_occupied',
            'world_may_have_changed': False, 'private_field': 'private'}})]
    history = public_action_history(events)
    assert len(history['records']) == 2  # IDs may repeat across fresh threads.
    assert history['records'][0]['effect'] == {'grasped_category': 'paper_bag'}
    assert history['records'][1]['error'] == {'code': 'hand_occupied', 'world_may_have_changed': False}
    assert 'private' not in json.dumps(history)
    assert 'old-image' not in json.dumps(history)


def test_incomplete_calls_and_nonmotor_content_are_not_presented_as_completed_actions():
    event = completed('release', {'ok': True})
    pending = dict(event, type='item.started')
    reading = completed('grasp', {'ok': True})
    reading['item']['tool'] = 'read_skill'
    assert public_action_history([pending, reading])['records'] == []


def test_truncation_is_explicit_and_unknown_feedback_is_not_success():
    events = [completed('grasp', {'ok': True}), completed('release', {'not_feedback': True})]
    history = public_action_history(events, max_records=1)
    assert history['omitted_earlier_records'] == 1
    assert history['records'] == [{'primitive': 'release', 'ok': None}]
