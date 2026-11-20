"""v9 harness options (default off): --place-look-again, --finish-evidence, and one more general operation check."""
from __future__ import annotations

import io
import json

from manipulation_agent.tools.finish_evidence import FinishEvidence, parse_checklist, MAX_ROUNDS


def _jpeg(colour=(120, 90, 60)):
    from PIL import Image
    buf = io.BytesIO(); Image.new('RGB', (512, 512), colour).save(buf, format='JPEG'); return buf.getvalue()


def _obs(rev, remaining):
    return {'ok': True, 'observation': {'revision': rev}, 'execution_budget': {'steps_remaining': remaining}}


def test_parse_checklist_lines():
    items = parse_checklist('Done.\nITEM: plate x2 -> table | rgb-00041-front | 0.42, 0.55\n'
                            '- ITEM 2: cup -> table | none\nbowl | rgb-00041-left | [0.1,0.9]\nrandom text')
    assert [i['item'] for i in items] == ['plate x2 -> table', 'cup -> table', 'bowl']
    assert items[0]['point'] == [0.42, 0.55] and items[1]['image_ref'] is None and items[2]['point'] == [0.1, 0.9]


def _ev(verdict='yes', tmp_path=None, judge=True):
    calls = []

    def judge_fn(text, images):
        calls.append((text, len(images)))
        idx = [int(l.split()[1].rstrip(':')) for l in text.splitlines()]
        return {i: (verdict, 'seen') for i in idx}, {'input_tokens': 100, 'output_tokens': 7}, 1.5
    from manipulation_agent.mcp_server import evidence_crops
    ev = FinishEvidence(judge=judge_fn if judge else None, log_dir=tmp_path, crop=evidence_crops)
    return ev, calls


def test_first_achieved_requests_checklist_then_confirmed_items_pass(tmp_path):
    ev, calls = _ev('yes', tmp_path)
    ev.observe('act', _obs(40, 30))
    fwd, payload, _ = ev.on_finish({'outcome': 'achieved', 'reason': 'all done'}, lambda r: _jpeg())
    assert not fwd and payload['error']['code'] == 'finish_evidence_required'
    assert payload['error']['action_step_used'] is False
    ev.observe('look', _obs(41, 29))
    fwd, payload, _ = ev.on_finish({'outcome': 'achieved',
                                    'reason': 'ITEM: plate -> table | rgb-00041-front | 0.5, 0.5'}, lambda r: _jpeg())
    assert fwd and payload['finish_evidence']['decision'] == 'accepted'
    assert calls and calls[0][1] == 2          # close + wider crop for one item
    rows = [json.loads(l) for l in (tmp_path / 'finish_evidence.jsonl').read_text().splitlines()]
    assert rows[-1]['judge_usage_total']['input_tokens'] == 100 and rows[-1]['judge_usage_total']['calls'] == 1


def test_not_confirmed_is_sent_back_with_crops_then_unverified_when_rounds_run_out(tmp_path):
    ev, _ = _ev('no', tmp_path)
    ev.observe('act', _obs(10, 50))
    reason = 'ITEM: cup -> table | rgb-00010-front | 0.5, 0.5'
    fwd, payload, images = ev.on_finish({'outcome': 'achieved', 'reason': reason}, lambda r: _jpeg())
    assert not fwd and payload['error']['code'] == 'finish_evidence_unconfirmed' and len(images) == 2
    while ev.rounds < MAX_ROUNDS - 1:
        ev.on_finish({'outcome': 'achieved', 'reason': reason}, lambda r: _jpeg())
    fwd, payload, _ = ev.on_finish({'outcome': 'achieved', 'reason': reason}, lambda r: _jpeg())
    assert fwd and payload['finish_evidence']['decision'] == 'accepted_unverified'


def test_stale_image_and_dropped_items_are_not_confirmed(tmp_path):
    ev, calls = _ev('yes', tmp_path)
    ev.observe('act', _obs(20, 50))
    fwd, *_ = ev.on_finish({'outcome': 'achieved', 'reason': 'ITEM: a -> t | rgb-00020-front | 0.5,0.5\n'
                                                            'ITEM: b -> t | rgb-00020-left | 0.5,0.5'}, lambda r: _jpeg())
    assert fwd
    ev2, _ = _ev('yes', tmp_path)
    ev2.observe('act', _obs(20, 50))
    fwd, payload, _ = ev2.on_finish({'outcome': 'achieved', 'reason': 'ITEM: a -> t | rgb-00019-front | 0.5,0.5'},
                                    lambda r: _jpeg())
    assert not fwd and 'stale image' in payload['error']['message']
    fwd, payload, _ = ev2.on_finish({'outcome': 'achieved', 'reason': 'ITEM: c -> t | rgb-00020-front | 0.5,0.5'},
                                    lambda r: _jpeg())
    assert not fwd and 'missing from this checklist' in payload['error']['message']


def test_other_outcomes_pass_through_and_reminder_once(tmp_path):
    ev, _ = _ev('yes', tmp_path)
    assert ev.on_finish({'outcome': 'blocked', 'reason': 'x'}, lambda r: _jpeg())[0]
    assert ev.observe('act', _obs(3, 50)) is None
    note = ev.observe('act', _obs(4, 10))
    assert note and '10 act/look steps remain' in note
    assert ev.observe('act', _obs(5, 9)) is None


def test_without_judge_fresh_evidence_is_shown_not_judged_and_accepted(tmp_path):
    ev, _ = _ev(judge=False, tmp_path=tmp_path)
    ev.observe('act', _obs(5, 50))
    fwd, payload, _ = ev.on_finish({'outcome': 'achieved', 'reason': 'ITEM: a -> t | rgb-00006-front | 0.5,0.5'},
                                   lambda r: _jpeg())
    assert fwd and payload['finish_evidence']['decision'] == 'accepted'


def test_place_look_again_caption_does_not_suggest_a_scoring_rule():
    from manipulation_agent.mcp_server import place_look_again_caption
    for kind in ('pair', 'close', 'mid'):
        text = place_look_again_caption(kind, 'rgb-00001-front', 'rgb-00002-front', 160).lower()
        assert 'where you meant to put it' in text
        for banned in ('other item', 'another item', 'floor', 'stack', 'wrong'):
            assert banned not in text


def test_operation_tip_for_enclosures_is_generic():
    from manipulation_agent.agent.skill_runtime import skill_root
    text = (skill_root() / '_options' / 'operation-checks.md').read_text().lower()
    assert 'stand in front of the open door so the item itself is visible' in text
    for banned in ('fridge', 'refrigerator', 'microwave', 'oven', 'hotdog', 'tray', 'batch', 'order'):
        assert banned not in text, banned


def test_switches_default_off_in_proxy_cli():
    import inspect
    from manipulation_agent import mcp_server
    sig = inspect.signature(mcp_server.create_server)
    assert sig.parameters['place_look_again'].default is False
    assert sig.parameters['finish_evidence'].default is False


# ---- evaluator v9 (benchmark side): stacked items count as on the surface ----

class _O:
    def __init__(self, name):
        self.name = name


def _backend(on, footprint=True):
    from manipulation_agent.executors.custom_task import CustomTaskBackend
    b = CustomTaskBackend.__new__(CustomTaskBackend)
    b._direct_on_top = lambda a, c: (a.name, c.name) in on
    b._within_footprint = lambda a, c: footprint
    return b


def test_stacked_chain_of_movable_items_counts():
    apple, hotdog, plate, plate2, table = map(_O, ('apple', 'hotdog', 'plate1', 'plate2', 'table'))
    b = _backend({('apple', 'hotdog'), ('hotdog', 'plate1'), ('plate1', 'table')})
    assert b._stacked_on_top(apple, plate, [apple, hotdog, plate, plate2])          # apple on hotdog on plate1
    assert b._stacked_on_top(apple, table, [apple, hotdog, plate, plate2])          # via two movable items
    assert not b._stacked_on_top(apple, plate2, [apple, hotdog, plate, plate2])     # wrong plate stays wrong


def test_furniture_breaks_the_chain_and_footprint_is_required():
    cup, counter, table = map(_O, ('cup', 'counter', 'table'))
    b = _backend({('cup', 'counter'), ('counter', 'table')})
    assert not b._stacked_on_top(cup, table, [cup])        # counter is not a movable task item
    apple, hotdog, plate = map(_O, ('apple', 'hotdog', 'plate1'))
    b = _backend({('apple', 'hotdog'), ('hotdog', 'plate1')}, footprint=False)
    assert not b._stacked_on_top(apple, plate, [apple, hotdog, plate])


def test_rc1_a7_fixed_supports_never_form_a_stacking_layer():
    lotion, nightstand, floor, apple, plate = map(_O, ('lotion', 'nightstand', 'floor', 'apple', 'plate'))
    nightstand.fixed_base = False                       # scene-authored furniture without fixed_base
    b = _backend({('lotion', 'nightstand'), ('nightstand', 'floor'), ('apple', 'plate'), ('plate', 'floor')})
    b.fixed_support_names = frozenset({'nightstand'})
    movable = b._stacking_movable([lotion, nightstand, floor, apple, plate])
    assert [o.name for o in movable] == ['lotion', 'floor', 'apple', 'plate']
    assert not b._stacked_on_top(lotion, floor, movable)   # on a fixed support: not on the floor
    assert b._stacked_on_top(apple, floor, movable)        # a movable plate still forms a layer
    b.fixed_support_names = frozenset()                    # before A7 / no declaration: old behaviour
    assert b._stacked_on_top(lotion, floor, b._stacking_movable([lotion, nightstand, floor]))


def test_rc1_a7_fixed_base_objects_excluded():
    counter = _O('counter'); counter.fixed_base = True
    cup = _O('cup')
    b = _backend(set())
    assert [o.name for o in b._stacking_movable([counter, cup, None])] == ['cup']


def test_rc1_a7_cut_counter_records_but_never_changes_values(tmp_path):
    lotion, nightstand, floor = map(_O, ('lotion', 'nightstand', 'floor'))
    nightstand.fixed_base = False
    b = _backend({('lotion', 'nightstand'), ('nightstand', 'floor')})
    b.fixed_support_names = frozenset({'nightstand'}); b.output = tmp_path; b.steps = 7
    movable = b._stacking_movable([lotion, nightstand, floor])
    cut = [nightstand]
    assert b._note_a7_cut('lotion_ontop_floor', lotion, floor, movable, cut) is False
    assert b._note_a7_cut('lotion_ontop_floor', lotion, floor, movable, cut) is False   # same situation: one record
    rows = [json.loads(l) for l in (tmp_path / 'evaluator_diagnostics.jsonl').read_text().splitlines()]
    assert len(rows) == 1 and rows[0]['event'] == 'a7_stacking_chain_cut' and rows[0]['fixed_layers'] == ['nightstand']
    assert b.a7_cut_counts == {'lotion_ontop_floor': 1}
    assert b._note_a7_cut('x', lotion, floor, movable, []) is False                        # nothing cut: no record


# ---- benchmark side: simulator wall-clock values never reach the model ----

def test_model_bound_payloads_drop_wall_clock_fields():
    from manipulation_agent.observations.boundary import without_wall_clock
    result = {'ok': True, 'runtime': {'tool_seconds': 167.2, 'observed_simulation_seconds': 0.07},
              'observation': {'capture': {'capture_id': 'capture-00003', 'captured_at': '2026-11-18T12:00:00+00:00',
                                          'sim_step': 4, 'sim_time_seconds': 0.13}, 'revision': 3},
              'planning': {'execution': {'tool_seconds': 300.0, 'failed_actions': 0}},
              'robot_time': {'this_call': {'travel_m': 3.1, 'travel_s': 4.4, 'manipulation_s': 5.0, 'wait_s': 0.0},
                             'total_s': 42.0}}
    clean = without_wall_clock(result)
    text = str(clean)
    for key in ('tool_seconds', 'captured_at', 'sim_time_seconds', 'observed_simulation_seconds', 'sim_step'):
        assert key not in text, key
    assert clean['robot_time'] == result['robot_time']             # the robot's own clock stays
    assert clean['observation']['capture'] == {'capture_id': 'capture-00003'}
    assert result['runtime']['tool_seconds'] == 167.2          # private copy untouched


def test_state_memory_reads_travel_time_only_from_robot_clock():
    from manipulation_agent.agent.state_memory import StateMemory
    import inspect
    src = inspect.getsource(StateMemory)
    assert 'observed_simulation_seconds' not in src and 'tool_seconds' not in src


def test_mcp_server_serializes_through_wall_clock_filter():
    import inspect
    from manipulation_agent import mcp_server
    src = inspect.getsource(mcp_server)
    assert 'json.dumps(result,' not in src and 'json.dumps(payload,' not in src
    assert src.count('json.dumps(without_wall_clock(') >= 3


# ---- v10 benchmark side: executor wall time is a stall guard, never a work budget ----

def test_local_wall_guard_is_a_distinct_private_event_and_unconfirmed_receipt(tmp_path):
    from types import SimpleNamespace
    import pytest
    from manipulation_agent.contracts import SkillError
    from manipulation_agent.executors.local_manipulation import LocalLimits, LocalOperation
    from manipulation_agent.observations.boundary import factual_execution_error
    assert LocalLimits().wall_seconds >= 1800
    t = [0.]
    backend = SimpleNamespace(local_limits=LocalLimits(), steps=0, sampling_physics_steps=0, output=tmp_path,
                              deadline=SimpleNamespace(check=lambda changed=False: None),
                              _local_pose=lambda: (0., 0., 0.))
    op = LocalOperation(backend, 'place_on_top', None, 600, {}, clock=lambda: t[0])
    t[0] = 400.
    op.check()                                   # 400 s of slow host is no longer a failure
    t[0] = LocalLimits().wall_seconds
    with pytest.raises(SkillError) as err:
        op.check()
    assert err.value.code == 'executor_wall_guard'
    row = json.loads((tmp_path / 'wall_guard.jsonl').read_text())
    assert row['audience'] == 'executor_private' and row['primitive'] == 'place_on_top'
    public = factual_execution_error(err.value, 'place_on_top', 'holding_item')
    assert public['code'] == 'result_unconfirmed' and 'wall' not in public['message'].lower()


def test_agy_client_timeout_exceeds_executor_guard_with_margin():
    from manipulation_agent.clients.agy import MCP_TOOL_TIMEOUT_SECONDS
    from manipulation_agent.executors.local_manipulation import LocalLimits
    assert MCP_TOOL_TIMEOUT_SECONDS > LocalLimits().wall_seconds + 300


# ---- v10b: finish-evidence pre-close exception and the finish guard's evidence mode ----

def _act(primitive, rev, remaining, view='front', point=(0.5, 0.5)):
    args = {'primitive': primitive, 'target': {'image_ref': f'rgb-{rev - 1:05d}-{view}', 'point': list(point)}}
    result = {'ok': True, 'effect': {'primitive': primitive, 'status': 'completed'},
              'observation': {'revision': rev, 'images': [{'image_ref': f'rgb-{rev:05d}-{view}'}]},
              'execution_budget': {'steps_remaining': remaining, 'steps_used': 160 - remaining}}
    return args, result


def _enclosure_run(tmp_path, reopen=False, verdict='yes'):
    ev, calls = _ev(verdict, tmp_path)
    seq = [('open', 10), ('place_inside', 11), ('close', 12)]
    seq += [('open', 13), ('navigate_to', 14)] if reopen else [('navigate_to', 13)]   # reopen: same stance
    for i, (prim, rev) in enumerate(seq):
        args, res = _act(prim, rev, 60 - i)
        ev.observe('act', res, args)
    ev.observe('look', {'ok': True, 'observation': {'revision': 20}, 'execution_budget': {'steps_remaining': 40}})
    return ev, calls


def test_preclose_image_accepted_only_with_a_fresh_closed_view(tmp_path):
    ev, calls = _enclosure_run(tmp_path)
    reason = 'ITEM: knife -> cabinet | rgb-00011-front | 0.5, 0.5 | rgb-00020-front | 0.4, 0.6'
    fwd, payload, _ = ev.on_finish({'outcome': 'achieved', 'reason': reason}, lambda r: _jpeg())
    assert fwd and payload['finish_evidence']['decision'] == 'accepted'
    assert calls and calls[0][1] == 4                      # item crops + closed-now crops
    row = [json.loads(l) for l in (tmp_path / 'finish_evidence.jsonl').read_text().splitlines()][-1]
    assert row['checks'][0]['verified_by_preclose_image'] and row['verified_by_preclose_image_total'] == 1
    ev2, _ = _enclosure_run(tmp_path / 'b')
    fwd, payload, _ = ev2.on_finish({'outcome': 'achieved', 'reason': 'ITEM: knife -> cabinet | rgb-00011-front | 0.5, 0.5'},
                                    lambda r: _jpeg())
    assert not fwd and 'stale image' in payload['error']['message']   # no fresh closed view -> not accepted


def test_preclose_exception_void_after_reopen_or_outside_window(tmp_path):
    ev, _ = _enclosure_run(tmp_path, reopen=True)
    reason = 'ITEM: knife -> cabinet | rgb-00011-front | 0.5, 0.5 | rgb-00020-front | 0.4, 0.6'
    fwd, payload, _ = ev.on_finish({'outcome': 'achieved', 'reason': reason}, lambda r: _jpeg())
    assert not fwd and 'stale image' in payload['error']['message']
    ev2, _ = _enclosure_run(tmp_path / 'b')
    late = 'ITEM: knife -> cabinet | rgb-00012-front | 0.5, 0.5 | rgb-00020-front | 0.4, 0.6'   # close's own view
    fwd, payload, _ = ev2.on_finish({'outcome': 'achieved', 'reason': late}, lambda r: _jpeg())
    assert not fwd


def test_preclose_rule_text_is_generic():
    from manipulation_agent.tools.finish_evidence import REQUEST
    low = REQUEST.lower()
    assert 'before closing' in low
    for banned in ('fridge', 'cabinet', 'microwave', 'knife', 'drawer', 'score', 'evaluator'):
        assert banned not in low, banned


def test_finish_guard_evidence_mode_keeps_only_unresolved_deferral_for_achieved(tmp_path):
    from manipulation_agent.tools.finish_guard import FinishGuard
    g = FinishGuard(tmp_path / 'g.json')
    g.observe({'ok': True, 'execution_budget': {'steps_remaining': 100}}, 'act', {'primitive': 'grasp'})
    assert g.deferral({'outcome': 'achieved', 'reason': 'x'}, evidence_mode=True) is None
    assert g.deferral({'outcome': 'blocked', 'reason': 'x'}, evidence_mode=True) is not None   # anti-early-quit kept
    g2 = FinishGuard(tmp_path / 'g2.json')
    g2.observe({'ok': True, 'execution_budget': {'steps_remaining': 100}}, 'act', {'primitive': 'grasp'})
    assert g2.deferral({'outcome': 'achieved', 'reason': 'x'}) is not None                      # v10 behaviour


# ---- v10c: judge relay (proxy inside the client sandbox -> worker in the controller) ----

def test_judge_relay_round_trip(tmp_path, monkeypatch):
    import threading
    from manipulation_agent.tools import finish_evidence as fe
    calls = []

    def fake_factory(config):
        def judge(text, images):
            calls.append((text, len(images)))
            return {1: ('yes', 'a plate'), 1001: ('no', 'door open')}, {'input_tokens': 5}, 0.1
        return judge
    monkeypatch.setattr(fe, 'codex_judge', fake_factory)
    stop = threading.Event()
    cfg = {'client': 'codex', 'queue': str(tmp_path / 'q')}
    fe.serve_judge_queue(cfg, stop)
    verdicts, usage, seconds = fe.relay_judge(cfg)('Claim 1: plate', [('close', _jpeg()), ('wider', _jpeg())])
    stop.set()
    assert verdicts == {1: ('yes', 'a plate'), 1001: ('no', 'door open')} and usage == {'input_tokens': 5}
    assert calls == [('Claim 1: plate', 2)]


def test_proxy_uses_relay_when_queue_configured():
    import inspect
    from manipulation_agent import mcp_server
    assert "relay_judge if judge_config.get('queue')" in inspect.getsource(mcp_server)
