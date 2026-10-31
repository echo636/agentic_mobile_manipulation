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


def test_recovery_review_window_keeps_rejections_and_does_not_claim_causality(tmp_path):
    run=tmp_path/'run'
    run.mkdir()
    entries=[('grasp', {'ok':False,'actions_used':1,'error':{'code':'out_of_reach'}}),
             ('navigate_to', {'ok':True,'actions_used':2}),
             ('grasp', {'ok':False,'error':{'code':'stale_observation'}}),
             ('grasp', {'ok':True,'actions_used':3})]
    rows=[]
    for i,(primitive,result) in enumerate(entries):
        rows.extend([{'id':f'call{i}','request_id':str(i),'kind':'tool_call','name':'act',
                      'at':f'2026-10-22T00:00:{i*2:02d}+00:00','arguments':{'primitive':primitive}},
                     {'request_id':str(i),'kind':'tool_result','name':'act',
                      'at':f'2026-10-22T00:00:{i*2+1:02d}+00:00','result':result}])
    (run/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    report=load_analyzer()(tmp_path)
    windows=report['recovery_review_candidates']
    assert len(windows)==1  # stale-observation rejection is not an executor failure
    candidate=windows[0]
    assert candidate['failed_call_id']=='call0'
    assert candidate['next_same_primitive_success_call_id']=='call3'
    assert candidate['following_request_count']==3
    assert candidate['following_budget_actions']==2
    assert candidate['following_tool_wall_seconds']==3
    assert candidate['confirmed_recovery'] is None


def test_pending_retry_is_not_successful_recovery(tmp_path):
    run=tmp_path/'run'
    run.mkdir()
    rows=[{'id':'failed','request_id':'a','kind':'tool_call','name':'act',
           'at':'2026-10-22T00:00:00+00:00','arguments':{'primitive':'grasp'}},
          {'request_id':'a','kind':'tool_result','name':'act','at':'2026-10-22T00:00:01+00:00',
           'result':{'ok':False,'actions_used':1,'error':{'code':'out_of_reach'}}},
          {'id':'pending','request_id':'b','kind':'tool_call','name':'act',
           'at':'2026-10-22T00:00:02+00:00','arguments':{'primitive':'grasp'}}]
    (run/'events.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    candidate=load_analyzer()(tmp_path)['recovery_review_candidates'][0]
    assert candidate['next_same_primitive_success_call_id'] is None
    assert candidate['following_request_count']==1
    assert candidate['following_budget_actions']==0
    assert candidate['following_tool_wall_seconds']==0


def test_skill_exposure_requires_successful_response_and_keeps_resource_identity(tmp_path):
    run=tmp_path/'run';run.mkdir()
    rows=[]
    for i,(arguments,result) in enumerate([
        ({}, {'ok':False,'error':{'code':'invalid_arguments'}}),
        ({'name':'failure-recovery','resource':'SKILL.md'},
         {'ok':True,'name':'failure-recovery','resource':'SKILL.md','bundle_sha256':'frozen'}),
        ({'name':'pick-and-place','resource':'references/staging-and-doors.md'},None),
    ]):
        rows.append({'id':f'read{i}','request_id':str(i),'kind':'tool_call','name':'read_skill',
                     'at':f'2026-10-22T00:00:0{i*2}+00:00','arguments':arguments})
        if result is not None:
            rows.append({'id':f'result{i}','request_id':str(i),'kind':'tool_result','name':'read_skill',
                         'at':f'2026-10-22T00:00:0{i*2+1}+00:00','result':result})
    (run/'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    report=load_analyzer()(tmp_path)
    assert report['skill_read_counts']=={'successful':1,'rejected':1,'unanswered':1}
    exposure=report['skill_read_timeline']
    assert [row['status'] for row in exposure]==['rejected','successful','unanswered']
    assert exposure[0]['error']=='invalid_arguments'
    assert exposure[1]['name']=='failure-recovery' and exposure[1]['bundle_sha256']=='frozen'
    assert exposure[1]['call_id']=='read1' and exposure[1]['response_id']=='result1'
    assert exposure[2]['resource']=='references/staging-and-doors.md'
    assert report['actions']==0


def test_goal_progress_distinguishes_initial_conditions_and_missing_evidence(tmp_path):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'events.jsonl').write_text('')
    initial = [[True, True, False], [False, False, False]]
    (run / 'run.json').write_text(json.dumps({'evaluation': {
        'initial_goal_options': initial, 'goal_options': initial}}))
    progress = load_analyzer()(tmp_path)['goal_progress_review']
    assert progress['initial_best_binding_fraction'] == 2 / 3
    assert progress['final_best_binding_fraction'] == 2 / 3
    assert progress['net_best_fraction_change'] == 0
    assert progress['goal_options_unchanged'] is True
    (run / 'run.json').write_text(json.dumps({'evaluation': {
        'goal_options': [[True, True, True]]}}))
    progress = load_analyzer()(tmp_path)['goal_progress_review']
    assert progress['final_best_binding_fraction'] == 1
    assert progress['initial_best_binding_fraction'] is None
    assert progress['net_best_fraction_change'] is None
    assert progress['goal_options_unchanged'] is None


def test_finish_review_preserves_rejected_and_accepted_closure(tmp_path):
    run = tmp_path / 'run'; run.mkdir()
    rows = []
    for i, result in enumerate(({'ok': False, 'error': {'code': 'invalid_arguments'}},
                                {'ok': True, 'closed': True, 'time_remaining_seconds': 40.5})):
        rows.extend([
            {'id': f'finish{i}', 'request_id': str(i), 'name': 'finish', 'kind': 'tool_call',
             'at': '2026-10-25T00:00:00+00:00',
             'arguments': {'outcome': 'blocked', 'reason': 'No target visible'}},
            {'request_id': str(i), 'name': 'finish', 'kind': 'tool_result',
             'at': '2026-10-25T00:00:01+00:00', 'result': result}])
    (run / 'events.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    (run / 'run.json').write_text(json.dumps({
        'budget': {'max_actions': 120, 'max_sim_steps': 20000},
        'actions': 11, 'sim_steps': 1059, 'deadline_expired_at_finish': False,
        'execution_limit_policy': {'legacy_limits_active': True}}))
    review = load_analyzer()(tmp_path)['termination_review']
    assert review['finish_requests'][0]['closed'] is None
    assert review['finish_requests'][0]['error'] == 'invalid_arguments'
    assert review['finish_requests'][1]['closed'] is True
    assert review['finish_requests'][1]['declared_outcome'] == 'blocked'
    assert review['finish_requests'][1]['time_remaining_seconds'] == 40.5
    assert review['remaining_active_limits_at_episode_end'] == {'actions': 109, 'sim_steps': 18941}


def test_absent_finish_and_inactive_limits_are_not_assumed(tmp_path):
    run = tmp_path / 'run'; run.mkdir()
    (run / 'events.jsonl').write_text('')
    for policy in ({}, {'legacy_limits_active': False}):
        (run / 'run.json').write_text(json.dumps({
            'budget': {'max_actions': 120, 'max_sim_steps': 20000},
            'actions': 11, 'sim_steps': 1059, 'execution_limit_policy': policy}))
        review = load_analyzer()(tmp_path)['termination_review']
        assert review['finish_requests'] == []
        assert review['deadline_expired_at_finish'] is None
        assert review['remaining_active_limits_at_episode_end'] == {'actions': None, 'sim_steps': None}
