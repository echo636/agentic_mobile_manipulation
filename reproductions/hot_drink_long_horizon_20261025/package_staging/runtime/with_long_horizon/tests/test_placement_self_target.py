"""Self-container attempts must fail before invoking the volume sampler."""
from types import SimpleNamespace

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.placement import CheckedPlacement


def test_carried_container_cannot_be_placed_inside_itself():
    box = object()
    motor = SimpleNamespace(_get_held=lambda: box)
    with pytest.raises(SkillError) as error:
        CheckedPlacement._checked_place_inside(motor, box, 100)
    assert error.value.code == 'unsupported_relation'
