import importlib.util
import json
from pathlib import Path


def load_analyzer():
    spec = importlib.util.spec_from_file_location(
        'workflow_efficiency', Path(__file__).parents[1] / 'scripts/analyze_workflow_efficiency.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze


def test_rejected_request_does_not_consume_action_budget(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    rows = []
    for index, (primitive, result) in enumerate([
        ('grasp', {'ok': False, 'actions_used': 1, 'error': {'code': 'out_of_reach'}}),
        ('look', {'ok': False, 'error': {'code': 'invalid_arguments'}}),
        ('open', {'ok': True, 'actions_used': 2}),
    ]):
        rows.extend([
            {'id': f'call{index}', 'request_id': str(index), 'kind': 'tool_call',
             'name': 'act', 'at': '2026-10-22T00:00:00+00:00',
             'arguments': {'primitive': primitive}},
            {'request_id': str(index), 'kind': 'tool_result', 'name': 'act',
             'at': '2026-10-22T00:00:01+00:00', 'result': result},
        ])
    (run / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    (run / 'run.json').write_text(json.dumps({'actions': 2}))
    report = load_analyzer()(tmp_path)
    assert report['actions'] == 2
    assert report['failed_actions'] == 1
    assert report['primitive_counts'] == {'grasp': 1, 'open': 1}
    assert report['action_requests'] == 3
    assert report['rejected_motor_calls'][0]['error']['code'] == 'invalid_arguments'
    assert report['timeline'][1]['action_number'] is None
    assert report['timeline'][2]['action_number'] == 2


def test_unanswered_request_has_no_assumed_budget_charge(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'events.jsonl').write_text(json.dumps({
        'id': 'call', 'request_id': 'pending', 'kind': 'tool_call', 'name': 'act',
        'at': '2026-10-22T00:00:00+00:00', 'arguments': {'primitive': 'navigate_to'}}))
    report = load_analyzer()(tmp_path)
    assert report['actions'] == 0
    assert report['action_requests'] == 1
    assert report['unanswered_motor_calls'] == 1
    assert report['timeline'][0]['action_number'] is None
