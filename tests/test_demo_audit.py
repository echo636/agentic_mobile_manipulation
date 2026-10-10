"""A geometrically satisfied goal can still leave a demo object in hand."""
from scripts.audit_demo_trial import hand_empty_after_events


def result(primitive, ok=True):
    return {'kind': 'tool_result', 'name': 'act',
            'result': {'ok': ok, 'effect': {'primitive': primitive}}}


def test_failed_final_placement_does_not_count_as_releasing_the_item():
    events = [result('grasp'), result('place_inside', False),
              result('place_inside', False), {'kind': 'tool_call', 'name': 'finish'}]
    assert hand_empty_after_events(events) is False


def test_successful_placement_or_release_clears_the_carry():
    assert hand_empty_after_events([result('grasp'), result('place_inside')]) is True
    assert hand_empty_after_events([result('grasp'), result('release')]) is True
    assert hand_empty_after_events([result('grasp'), result('place_inside'),
                                    result('grasp')]) is False
