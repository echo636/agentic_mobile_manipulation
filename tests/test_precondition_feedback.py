"""Recorded high-level preparation errors keep their type without leaking state."""
from manipulation_agent.contracts import SkillError
from manipulation_agent.observations.boundary import public_execution_error


def test_recorded_boiling_precondition_is_not_reported_as_execution_failure():
    # Actual private error at hot-drink actions 24 and 25.
    detail = 'Place a kettle containing water on the stove first'
    result = public_execution_error(SkillError('invalid_precondition', detail))
    assert result['code'] == 'invalid_precondition'
    assert 'preparation' in result['message']
    assert detail not in result['message']
    assert result['world_may_have_changed'] is False
