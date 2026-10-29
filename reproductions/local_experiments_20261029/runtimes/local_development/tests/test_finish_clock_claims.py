"""Native grocery finish must not cancel before a false-clock correction."""
import time
import pytest
from manipulation_agent.contracts import Budget
from manipulation_agent.deadline import EpisodeDeadline
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness

REASON = 'Unable to complete full transfer within this session; only one paper bag is held, groceries and kitchen destinations remain unhandled.'


def make_harness(tmp_path, monkeypatch, enabled, remaining=3600):
    monkeypatch.setenv('MAS_REJECT_FALSE_TIME_CLAIMS', '1' if enabled else '0')
    tmp_path = tmp_path / 'episode'
    recorder = Recorder(tmp_path, {})
    backend = MockRGBBackend(tmp_path)
    backend.demo_motion = False
    backend.deadline = EpisodeDeadline(time.time() + remaining)
    return VisionHarness(backend, recorder, Budget(max_actions=120), profile='minimal')


def test_native_false_session_limit_is_corrected_and_action_can_continue(tmp_path, monkeypatch):
    harness = make_harness(tmp_path, monkeypatch, True)
    harness.actions = 10
    args = dict(outcome='blocked', reason=REASON)
    harness.request_finish(args, 'finish')
    assert not harness.deadline.stop_requested
    result = harness.call('finish', args, 'finish')
    assert result['error']['code'] == 'time_budget_available'
    assert result['time_remaining_seconds'] > 3500
    assert not harness.closed
    obs = harness.snapshot
    continued = harness.call('act', dict(primitive='toggle_on', target={
        'image_ref': obs['images'][0]['image_ref'], 'point': [.5, .5]},
        revision=obs['revision']), 'continue')
    assert continued['ok'] and continued['actions_used'] == 11
    assert harness.recorder.run['termination_contract']['false_time_claims'] == 'clock_claim_correction_v1'


@pytest.mark.parametrize('case', ['disabled', 'real_blocker', 'action_limit', 'step_limit', 'call_limit', 'near_deadline'])
def test_native_genuine_or_unchecked_finish_is_preserved(tmp_path, monkeypatch, case):
    h = make_harness(tmp_path, monkeypatch, case != 'disabled', 30 if case == 'near_deadline' else 3600)
    if case == 'action_limit': h.actions = h.budget.max_actions
    if case == 'step_limit': h.backend.steps = h.budget.max_sim_steps
    if case == 'call_limit': h.calls = h.budget.max_calls
    args = dict(outcome='blocked', reason='Navigation executor has an invalid start.' if case == 'real_blocker' else REASON)
    h.request_finish(args, 'finish')
    assert h.deadline.stop_requested
    result = h.call('finish', args, 'finish')
    assert result['ok'] and result['closed']
