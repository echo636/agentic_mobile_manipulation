"""Both arms record intent; only planning requires an active subgoal."""
import pytest

from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.vision_policy import system_prompt
from manipulation_agent.tools import tool_specs


@pytest.mark.parametrize('profile', ['skills', 'planning'])
def test_decision_is_enforced_through_real_harness(tmp_path, monkeypatch, profile):
    monkeypatch.setenv('MAS_REQUIRE_DECISION', '1')
    monkeypatch.setenv('MAS_LONG_HORIZON_PLANNING_SKILL', '1' if profile == 'planning' else '0')
    output = tmp_path / profile
    harness = VisionHarness(MockRGBBackend(output), Recorder(output, {}), profile=profile)
    assert all('decision' in t['inputSchema']['required'] for t in harness.tool_specs())
    assert harness.call('initialize', {}, 'missing')['error']['code'] == 'invalid_arguments'
    fields = {'state', 'reason', 'expected'}
    if profile == 'planning':
        fields.add('active_subgoal')
    for tool in harness.tool_specs():
        schema = tool['inputSchema']['properties']['decision']
        assert set(schema['properties']) == fields
        assert set(schema['required']) == fields
    decision = {k: 'Inspect current RGB' for k in fields}
    assert harness.call('initialize', {'decision': decision}, 'valid')['ok']
    assert 'Every tool call requires a short public decision object' in system_prompt(profile)
    assert 'First call initialize({})' not in system_prompt(profile)
    assert ('active_subgoal' in system_prompt(profile)) == (profile == 'planning')
    assert harness.recorder.run['public_decision_contract']['required_fields_by_tool']['initialize'] == sorted(fields)
    names = {t['name'] for t in harness.tool_specs()}
    assert ({'update_plan', 'remember', 'recall'} <= names) == (profile == 'planning')
    assert ('long-horizon-planning' in {s['name'] for s in harness.skills.catalog()}) == (profile == 'planning')
    args = {'outcome': 'aborted', 'reason': 'Controller cleanup'}
    harness.request_finish(args, 'demo-supervisor-finish')
    assert harness.call('finish', args, 'demo-supervisor-finish')['closed']


def test_archived_skills_default_is_preserved(monkeypatch):
    monkeypatch.delenv('MAS_REQUIRE_DECISION', raising=False)
    assert all('decision' not in t['inputSchema']['required'] for t in tool_specs('skills'))
