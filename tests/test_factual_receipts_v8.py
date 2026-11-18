"""v8: shared failure receipts state only the physical outcome (benchmark side, every arm), and three
default-off harness options: failure look-again crops, failure memory, general operation checks."""
from __future__ import annotations

import io
import json

import pytest

from manipulation_agent.contracts import Budget, SkillError
from manipulation_agent.agent.failure_memory import FailureMemory
from manipulation_agent.observations import boundary as B
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.records import Recorder
from manipulation_agent.runtime.vision_harness import ACTION_FEEDBACK_HINTS, VisionHarness

SIM_WORDS = ('fixed', 'furniture', 'counter', 'wall', 'floor', 'container', 'is closed', 'support', 'cut',
             'particle', 'dispenser', 'volume', 'simulator', 'other doors', 'relation')


# ---- receipt mapping (pure) ----

@pytest.mark.parametrize('code,primitive,hand,expected', [
    ('fixed_object', 'grasp', 'empty', 'nothing_lifted'),
    ('particle_target', 'grasp', 'empty', 'nothing_lifted'),
    ('container_closed', 'grasp', 'empty', 'nothing_lifted'),
    ('invalid_visual_target', 'grasp', 'empty', 'nothing_lifted'),
    ('container_closed', 'place_inside', 'holding_item', 'not_placed'),
    ('unsupported_relation', 'place_inside', 'holding_item', 'not_placed'),
    ('unsupported_relation', 'hang', 'holding_item', 'not_placed'),
    ('pre_condition_error', 'open', 'empty', 'no_visible_change'),
    ('cut_target_unsupported', 'cut', 'holding_item', 'no_visible_change'),
    ('cutting_tool_required', 'cut', 'holding_item', 'no_visible_change'),
    ('material_source_empty', 'pour', 'holding_item', 'no_visible_change'),
    ('no_removable_material', 'wipe', 'holding_item', 'no_visible_change'),
    ('target_changed', 'grasp', 'empty', 'result_unconfirmed'),
    ('physics_instability', 'place_on_top', 'empty', 'result_unconfirmed'),
    ('postcondition_error', 'close', 'empty', 'result_unconfirmed'),
])
def test_simulator_knowledge_codes_become_physical_outcomes(code, primitive, hand, expected):
    out = B.factual_execution_error(SkillError(code, 'PRIVATE /World/secret'), primitive, hand)
    assert out['code'] == expected
    text = out['message'].lower()
    assert not any(w in text for w in SIM_WORDS), text
    assert 'PRIVATE' not in json.dumps(out)


def test_unsupported_object_action_stage_is_hidden():
    out = B.factual_execution_error(SkillError('pre_condition_error', 'x', stage='unsupported_object_action'),
                                    'toggle_on', 'empty')
    assert out['code'] == 'no_visible_change' and 'stage' not in out


def test_changed_semantic_failure_is_unconfirmed():
    out = B.factual_execution_error(SkillError('container_closed', 'x', changed=True), 'place_inside', 'holding_item')
    assert out['code'] == 'result_unconfirmed' and out['world_may_have_changed'] is True


def test_released_stages_keep_only_hand_fact():
    for stage in B.RELEASED_STAGES:
        out = B.factual_execution_error(SkillError('postcondition_error', 'x', changed=True, stage=stage),
                                        'place_on_top', 'empty')
        assert out['code'] == 'result_unconfirmed' and 'hand is empty' in out['message']
        assert 'receiving surface' not in out['message'] and 'inside the selected container' not in out['message']


@pytest.mark.parametrize('code', ['hand_occupied', 'empty_hand', 'navigation_stalled', 'out_of_reach',
                                  'no_surface_at_point', 'placement_surface_unavailable', 'robot_surface_selected',
                                  'same_placement_object', 'sampling_error', 'action_timeout', 'look_rotation_blocked'])
def test_robot_perceivable_codes_are_unchanged(code):
    exc = SkillError(code, 'x')
    assert B.factual_execution_error(exc, 'grasp', 'empty') == B.public_execution_error(exc)


def test_route_a_hints_are_keyed_on_factual_codes_only():
    assert set(ACTION_FEEDBACK_HINTS) <= set(B.FACTUAL_CODES) | {'hand_occupied'}


# ---- harness wiring (mock backend) ----

def _harness(tmp_path, fail=None, **switches):
    out = tmp_path / 'ep'
    backend = MockRGBBackend(out)
    if fail is not None:
        original = backend.execute_visual

        def failing(primitive, *a, **k):
            if primitive == fail[0]:
                raise SkillError(fail[1], 'PRIVATE_OBJECT_NAME')
            return original(primitive, *a, **k)
        backend.execute_visual = failing
    return VisionHarness(backend, Recorder(out, {'output': str(out)}), Budget(wall_seconds=None),
                         profile='minimal', agent_step_limit=20, goal_skill='none', **switches)


def test_harness_returns_factual_code_and_keeps_private_code(tmp_path):
    h = _harness(tmp_path, fail=('grasp', 'fixed_object'))
    init = h.call('initialize', {}, 'i')
    ref = init['observation']['images'][0]['image_ref']
    res = h.call('act', {'primitive': 'grasp', 'target': {'image_ref': ref, 'point': [0.5, 0.5]},
                         'revision': init['observation']['revision']}, 'a')
    assert res['error']['code'] == 'nothing_lifted'
    assert 'fixed' not in json.dumps(res['error'])
    events = [json.loads(l) for l in (tmp_path / 'ep/events.jsonl').read_text().splitlines()]
    private = [e for e in events if e['kind'] == 'private_executor_error']
    assert private and private[0]['code'] == 'fixed_object'


def test_operation_tips_only_with_switch(tmp_path):
    from manipulation_agent.agent.skill_runtime import SkillLibrary, skill_root
    plain = SkillLibrary(tmp_path / 'a')
    tips = SkillLibrary(tmp_path / 'b', appendix={('visual-manipulation', 'SKILL.md'):
                                                 (skill_root() / '_options' / 'operation-checks.md').read_text()})
    assert 'general operation checks' not in plain.resources[('visual-manipulation', 'SKILL.md')]
    assert 'general operation checks' in tips.resources[('visual-manipulation', 'SKILL.md')]
    assert '_options' not in {e['name'] for e in plain.entries}
    assert plain.digest != tips.digest
    text = (skill_root() / '_options' / 'operation-checks.md').read_text().lower()
    for banned in ('batch', 'order', 'schedule', 'first do', 'tray', 'microwave', 'fridge', 'lunch'):
        assert banned not in text, banned


# ---- failure memory (proxy side, pure) ----

def _act(prim, ref='rgb-00003-front', point=(0.5, 0.5)):
    return {'primitive': prim, 'target': {'image_ref': ref, 'point': list(point)}}


def test_failure_memory_warns_on_repeat_in_same_stance_only():
    m = FailureMemory()
    assert m.observe('act', _act('grasp'), {'ok': True, 'effect': {}}) is None
    s = m.observe('act', _act('grasp'), {'ok': False, 'error': {'code': 'nothing_lifted'}})
    assert len(s['recent_failed_attempts']) == 1 and 'repeat_warning' not in s
    s = m.observe('act', _act('grasp', point=(0.52, 0.51)), {'ok': False, 'error': {'code': 'nothing_lifted'}})
    assert 'repeat_warning' in s and '2 times' in s['repeat_warning']
    m.observe('act', _act('navigate_to', point=(0.5, 0.9)), {'ok': True, 'effect': {}})
    s = m.observe('act', _act('grasp'), {'ok': False, 'error': {'code': 'nothing_lifted'}})
    assert 'repeat_warning' not in s
    s = m.observe('act', _act('grasp', point=(0.9, 0.1)), {'ok': False, 'error': {'code': 'nothing_lifted'}})
    assert 'repeat_warning' not in s


def test_failure_memory_ignores_unused_steps_and_bounds_size():
    m = FailureMemory()
    for i in range(20):
        m.observe('act', _act('open', point=(0.05 * (i % 10), 0.5)), {'ok': False, 'error': {'code': 'no_visible_change'}})
    assert len(m.failures) == 8
    before = m.step
    m.observe('act', _act('open'), {'ok': False, 'error': {'code': 'invalid_arguments', 'action_step_used': False}})
    assert m.step == before


def _jpeg(colour, patch=None):
    from PIL import Image
    img = Image.new('RGB', (512, 512), colour)
    if patch:
        img.paste(patch, (200, 200, 312, 312))
    buf = io.BytesIO(); img.save(buf, format='JPEG'); return buf.getvalue()


def test_look_again_gives_pair_only_when_view_changed():
    from PIL import Image
    from manipulation_agent.mcp_server import ECHO_HALF_PX, ECHO_SCALE, look_again_images
    out = look_again_images(_jpeg((200, 0, 0)), _jpeg((200, 0, 0), patch=(0, 0, 200)), [0.5, 0.5],
                            close_already_shown=False)
    assert [k for k, _, _ in out] == ['pair']
    img = Image.open(io.BytesIO(out[0][1]))
    assert img.width == 2 * 2 * ECHO_HALF_PX * ECHO_SCALE + 8


def test_look_again_unchanged_view_gives_two_scales_not_two_equal_images():
    from manipulation_agent.mcp_server import look_again_images
    same = _jpeg((90, 120, 60))
    assert [k for k, _, _ in look_again_images(same, same, [0.5, 0.5], close_already_shown=False)] == ['close', 'mid']
    # with the click echo already showing the close crop, only the wider view is added
    assert [k for k, _, _ in look_again_images(same, same, [0.5, 0.5], close_already_shown=True)] == ['mid']


@pytest.mark.parametrize('code,primitive,hand,expected', [
    ('fixed_object', 'grasp', 'holding_item', 'hand_occupied'),
    ('pre_condition_error', 'open', 'holding_item', 'hand_occupied'),
    ('invalid_visual_target', 'place_on_top', 'empty', 'empty_hand'),
    ('invalid_visual_target', 'cut', 'empty', 'empty_hand'),
])
def test_hand_state_outcomes_take_priority_over_semantic_checks(code, primitive, hand, expected):
    out = B.factual_execution_error(SkillError(code, 'x'), primitive, hand)
    assert out == B.public_execution_error(SkillError(expected, ''))


def test_wording_does_not_claim_the_action_was_done():
    for code in ('no_visible_change', 'result_unconfirmed', 'nothing_lifted'):
        text = B.FACTUAL_MESSAGES[code].lower()
        assert 'completed' not in text and 'carried out' not in text and 'was made' not in text
