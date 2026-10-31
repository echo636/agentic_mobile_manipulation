"""Reproduce budget-burning pressure at the actual finish boundary on CPU."""
import json

import pytest

from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness


@pytest.mark.parametrize('profile', ['skills', 'planning'])
def test_reported_blocker_can_close_without_waiting_or_changing_score(tmp_path, monkeypatch, profile):
    monkeypatch.delenv('MAS_HIGH_LEVEL_BLOCKED_POLICY', raising=False)
    monkeypatch.setenv('MAS_REQUIRE_DECISION', '1')
    output = tmp_path / 'episode'
    backend = MockRGBBackend(output)
    backend.high_level_harness = True
    harness = VisionHarness(backend, Recorder(output, {}), profile=profile)
    fields = harness.catalog['finish']['inputSchema']['properties']['decision']['required']
    args = {'outcome': 'blocked', 'reason': 'Repeated visible-target attempts failed',
            'decision': {key: 'Report the observed blocker' for key in fields}}
    harness._last_action_error = 'invalid_visual_target'
    harness.request_finish(args, 'reported-blocker')
    assert harness.deadline.stop_requested
    result = harness.call('finish', args, 'reported-blocker')
    assert result['ok'] and result['closed']
    assert 'evaluation' not in result and 'task_success' not in result
    assert harness.actions == backend.steps == 0
    run = json.loads((output / 'run.json').read_text())
    assert run['task_success'] is False
    assert run['termination_contract']['blocked_policy'] == 'accept'
    assert run['termination_contract']['early_blocked_guard'] is False
    assert harness.call('finish', args, 'reported-blocker') == result


def test_invalid_blocked_policy_is_rejected_before_render(tmp_path, monkeypatch):
    monkeypatch.setenv('MAS_HIGH_LEVEL_BLOCKED_POLICY', 'typo')
    output = tmp_path / 'episode'
    backend = MockRGBBackend(output)
    with pytest.raises(ValueError, match='MAS_HIGH_LEVEL_BLOCKED_POLICY'):
        VisionHarness(backend, Recorder(output, {}))
    assert backend.capture == 0
