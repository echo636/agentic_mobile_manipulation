"""The planning profile requires a public decision at every robot tool boundary."""
import json

from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.vision_policy import system_prompt
from scripts.run_demo_trial import agent_profile


DECISION = {
    'state': 'The current RGB shows an accessible appliance; other items remain uncertain',
    'active_subgoal': 'Inspect the work area',
    'reason': 'This call tests one visible target while preserving the current goal plan',
    'expected': 'A fresh RGB and bounded action result',
}


def test_planning_profile_keeps_pixel_actions_and_requires_public_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv('MAS_LONG_HORIZON_PLANNING_SKILL','1')
    output=tmp_path/'episode'
    backend=MockRGBBackend(output)
    harness=VisionHarness(backend,Recorder(output,{}),profile='planning')
    specs={row['name']:row for row in harness.tool_specs()}
    assert set(specs)=={'initialize','look','act','finish','list_skills','read_skill',
                        'update_plan','remember','recall'}
    assert {'image_ref','point'} <= set(specs['act']['inputSchema']['properties']['target']['properties'])
    assert all('decision' in row['inputSchema']['required'] for row in specs.values())
    assert 'long-horizon-planning' in {row['name'] for row in harness.skills.catalog()}
    assert 'task-decomposition' not in {row['name'] for row in harness.skills.catalog()}

    missing=harness.call('initialize',{},'initialize-without-decision')
    assert missing['error']['code']=='invalid_arguments'
    first=harness.call('initialize',{'decision':DECISION},'initialize')
    assert first['ok']
    plan=[{'id':'inspect','description':'Inspect the work area',
           'dependencies':[],'status':'active','evidence':None},
          {'id':'serve','description':'Complete the requested arrangement',
           'dependencies':['inspect'],'status':'pending','evidence':None}]
    stored=harness.call('update_plan',{'reason':'The instruction has dependent outcomes',
                                      'subgoals':plan,'decision':DECISION},'plan')
    assert stored['ok']
    target={'image_ref':first['observation']['images'][0]['image_ref'],'point':[.5,.5]}
    args={'primitive':'toggle_on','target':target,'revision':first['observation']['revision'],
          'placement_yaw_degrees':None,'wait_seconds':None,'confirm_bulk_grasp':False}
    rejected=harness.call('act',args,'act-without-decision')
    assert rejected['error']['code']=='invalid_arguments'
    assert harness.actions==0
    accepted=harness.call('act',{**args,'decision':DECISION},'act')
    assert accepted['ok'] and harness.actions==1
    events=[json.loads(line) for line in (output/'events.jsonl').read_text().splitlines()]
    action=next(row for row in events if row.get('kind')=='tool_call' and row.get('request_id')=='act')
    assert action['arguments']['decision']==DECISION


def test_supervisor_can_close_planning_episode_without_model_decision(tmp_path, monkeypatch):
    monkeypatch.setenv('MAS_LONG_HORIZON_PLANNING_SKILL','1')
    output=tmp_path/'episode'
    harness=VisionHarness(MockRGBBackend(output),Recorder(output,{}),profile='planning')
    arguments={'outcome':'aborted','reason':'Model ended without formal finish'}
    harness.request_finish(arguments,'demo-supervisor-finish')
    result=harness.call('finish',arguments,'demo-supervisor-finish')
    assert result['ok'] and result['closed']


def test_planning_skill_handles_natural_language_and_bddl(monkeypatch):
    monkeypatch.setenv('MAS_HIGH_LEVEL_HARNESS','1')
    assert agent_profile(high_level_harness=True,long_horizon_planning_skill=True)=='planning'
    prompt=system_prompt('planning')
    assert 'long-horizon-planning/SKILL.md' in prompt
    assert 'Every tool call requires a short public decision object' in prompt
    assert 'First call initialize({})' not in prompt
    assert 'long-horizon-planning/references/bddl-goals.md' in prompt
    assert 'task-decomposition/SKILL.md' not in prompt
