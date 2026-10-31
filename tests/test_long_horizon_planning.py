"""The planning profile requires a public decision at every robot tool boundary."""
import json
import pytest

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


@pytest.mark.parametrize("profile", ["planning", "planning_no_skills"])
def test_planning_profile_keeps_pixel_actions_and_requires_public_decisions(tmp_path, monkeypatch, profile):
    monkeypatch.setenv('MAS_LONG_HORIZON_PLANNING_SKILL','1')
    output=tmp_path/'episode'
    backend=MockRGBBackend(output)
    harness=VisionHarness(backend,Recorder(output,{}),profile=profile)
    specs={row['name']:row for row in harness.tool_specs()}
    expected={'initialize','look','act','finish','update_plan','remember','recall'}
    assert set(specs)==expected | ({'list_skills','read_skill'} if profile=='planning' else set())
    assert {'image_ref','point'} <= set(specs['act']['inputSchema']['properties']['target']['properties'])
    assert all('decision' in row['inputSchema']['required'] for row in specs.values())
    if profile=='planning':
        assert 'long-horizon-planning' in {row['name'] for row in harness.skills.catalog()}
    else:
        assert harness.skills is None
        assert harness.recorder.run['skill_bundle_sha256'] is None

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
    remembered=harness.call('remember',{'key':'visible','text':'An appliance is visible',
        'revision':first['observation']['revision'],'decision':DECISION},'remember')
    assert remembered['ok']
    assert harness.call('recall',{'decision':DECISION},'recall')['ok']
    if profile=='planning_no_skills':
        assert harness.call('read_skill',{'name':'visual-manipulation','resource':'SKILL.md',
            'decision':DECISION},'denied-skill')['error']['code']=='unknown_tool'
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


@pytest.mark.parametrize("profile", ["planning", "planning_no_skills"])
def test_supervisor_can_close_planning_episode_without_model_decision(tmp_path, monkeypatch, profile):
    monkeypatch.setenv('MAS_LONG_HORIZON_PLANNING_SKILL','1')
    output=tmp_path/'episode'
    harness=VisionHarness(MockRGBBackend(output),Recorder(output,{}),profile=profile)
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


def test_no_skills_retains_planning_contract():
    from manipulation_agent.tools import tool_specs
    baseline={t['name']:t for t in tool_specs('planning_no_skills')}
    treatment={t['name']:t for t in tool_specs('planning')}
    for name, spec in baseline.items():
        assert spec['inputSchema']==treatment[name]['inputSchema']
        assert spec['description']==treatment[name]['description']
    prompt=system_prompt('planning_no_skills')
    assert 'Every tool call requires a short public decision object' in prompt
    assert 'read_skill' not in prompt and 'SKILL.md' not in prompt
    assert 'remember/recall' in prompt
    assert agent_profile(long_horizon_planning_skill=True, without_skills=True)=='planning_no_skills'
