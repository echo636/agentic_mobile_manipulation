"""Harness options --place-names / --place-return (2026-11-21, default off): simulator-free tests."""
import json
from copy import deepcopy

import numpy as np

from manipulation_agent.agent.place_options import apply_name, place_catalog, prepare
from manipulation_agent.agent.state_memory import StateMemory
from manipulation_agent.executors import place_return as PR
from manipulation_agent.tools import tool_specs


def nav_result(revision, travel_m=3.0):
    return {'ok': True, 'effect': {'primitive': 'navigate_to', 'status': 'completed', 'navigation_status': 'reached'},
            'observation': {'revision': revision, 'images': [{'image_ref': f'rgb-{revision:05d}-front'}]},
            'robot_time': {'total_s': revision * 5.0, 'this_call': {'travel_m': travel_m, 'travel_s': 5.0}},
            'execution_budget': {'steps_used': revision}}


def nav_args(rev):
    return {'primitive': 'navigate_to', 'target': {'image_ref': f'rgb-{rev:05d}-front', 'point': [0.5, 0.8]}, 'revision': rev}


# ---------------------------------------------------------------- catalog
def test_catalog_unchanged_when_off():
    cat = tool_specs('skills')
    assert place_catalog(cat) is cat
    before = json.dumps(cat, sort_keys=True)
    place_catalog(deepcopy(cat), names=True, ret=True)
    assert json.dumps(cat, sort_keys=True) == before


def test_catalog_with_options():
    bridge = deepcopy(tool_specs('skills'))
    act = next(s for s in bridge if s['name'] == 'act')
    act['inputSchema']['properties']['return_revision'] = {'type': ['integer', 'null']}   # bridge-only field
    act['inputSchema']['properties']['primitive']['enum'].append('return_to')
    out = {s['name']: s for s in place_catalog(bridge, names=True, ret=True)}
    props = out['act']['inputSchema']['properties']
    assert 'name_here' in props and 'place' in props and 'return_revision' not in props
    assert props['primitive']['enum'].count('return_to') == 1
    assert 'name_here' in out['look']['inputSchema']['properties']
    names_only = {s['name']: s for s in place_catalog(deepcopy(tool_specs('skills')), names=True)}
    assert 'return_to' not in names_only['act']['inputSchema']['properties']['primitive']['enum']
    assert 'place' not in names_only['act']['inputSchema']['properties']


# ---------------------------------------------------------------- naming
def test_summary_identical_without_names_and_names_listed():
    a, b = StateMemory(), StateMemory()
    for rev in range(1, 9):
        a.observe('act', nav_args(rev), nav_result(rev)); b.observe('act', nav_args(rev), nav_result(rev))
    assert a.render_memory_summary() == b.render_memory_summary() and 'named_places' not in a.public_view()
    note = b.name_location('loc_2', 'the guest bedroom door')
    assert note['message'] == "loc_2 is now named 'the guest bedroom door'"
    assert 'loc_2 "the guest bedroom door"' in b.render_memory_summary(max_chars=2000)   # older than the last 6, still shown
    assert b.public_view()['named_places'] == {'the guest bedroom door': 'loc_2'}
    assert b.location_for_name('Guest  Bedroom door') == 'loc_2'                          # articles / case / spaces
    assert 'Named places:' in b.build_handoff_context('task')


def test_name_moves_and_renames():
    m = StateMemory()
    for rev in (1, 2):
        m.observe('act', nav_args(rev), nav_result(rev))
    m.name_location('loc_1', 'laundry')
    assert m.name_location('loc_2', 'laundry')['message'] == "name 'laundry' moved from loc_1 to loc_2"
    assert 'name' not in m.visited_locations['loc_1']
    assert m.name_location('loc_2', 'laundry room')['message'] == "loc_2 renamed 'laundry' -> 'laundry room'"


def test_prepare_name_here_and_navigate_merge():
    m = StateMemory()
    m.observe('act', nav_args(1), nav_result(1)); m.name_location('loc_1', 'guest room')
    m.observe('act', nav_args(2), nav_result(2))
    args = {**nav_args(3), 'name_here': 'guest room'}
    fwd, obs, name, early = prepare('act', args, m, names=True)
    assert early is None and 'name_here' not in fwd and obs['location'] == 'loc_1' and name == 'guest room'
    res = nav_result(3); m.observe('act', obs, res); apply_name(res, m, name)
    assert m.current_location == 'loc_1' and res['place_name']['recorded']


def test_bad_name_never_blocks_the_action():
    m = StateMemory(); m.observe('act', nav_args(1), nav_result(1))
    res = nav_result(1)
    apply_name(res, m, 'x' * 41)
    assert res['place_name']['recorded'] is False and 'name_here must be' in res['place_name']['message']
    assert res['ok'] is True


def test_prepare_untouched_when_off():
    args = {**nav_args(1)}
    assert prepare('act', args, StateMemory()) == (args, args, None, None)


# ---------------------------------------------------------------- return_to (proxy)
def test_return_to_resolves_place_or_refuses():
    m = StateMemory()
    m.observe('act', nav_args(1), nav_result(1)); m.name_location('loc_1', 'guest room')
    m.observe('act', nav_args(4), nav_result(4))
    fwd, obs, _, early = prepare('act', {'primitive': 'return_to', 'target': None, 'revision': 4, 'place': 'guest room'}, m, ret=True)
    assert early is None and fwd['return_revision'] == 1 and 'place' not in fwd
    assert obs['primitive'] == 'navigate_to' and obs['location'] == 'loc_1'
    fwd, _, _, early = prepare('act', {'primitive': 'return_to', 'target': None, 'revision': 4, 'place': 'loc_1'}, m, ret=True)
    assert early is None and fwd['return_revision'] == 1
    _, _, _, early = prepare('act', {'primitive': 'return_to', 'target': None, 'revision': 4, 'place': 'attic'}, m, ret=True)
    assert early['error']['action_step_used'] is False and '"guest room"' in early['error']['message']


# ---------------------------------------------------------------- visited-area planner (bridge, private)
def test_astar_and_restricted_grid():
    free = np.ones((40, 40), dtype=bool); free[:, 20] = False; free[30:, 20] = True   # wall with a gap at the bottom
    path = PR.astar(free, (5, 5), (5, 35))
    assert path is not None and max(r for r, _ in path) >= 30
    visited = [(5, c) for c in range(5, 18)]                                       # only the left room was visited
    grid = PR.restricted_grid(free, visited, 3)
    assert PR.astar(grid, (5, 5), (5, 15)) is not None
    assert PR.astar(grid, (5, 5), (5, 35)) is None                                  # never been on the right side
    assert PR.nearest_free(grid, (5, 19), 3) is not None and PR.nearest_free(grid, (35, 35), 3) is None


def test_visited_points_from_route_records(tmp_path):
    (tmp_path / 'navigation_plans.jsonl').write_text('\n'.join(json.dumps(r) for r in (
        {'status': 'planned', 'path': [[0, 0], [1, 0], [2, 0]]},
        {'status': 'failed', 'path': [[9, 9]]})) + '\n')
    (tmp_path / 'base_motion.jsonl').write_text('\n'.join(json.dumps(r) for r in (
        {'actual_position': [2.0, 0.0, 0.0], 'commanded_position': [2.0, 0.0, 0.0]},
        {'actual_position': [2.5, 0.3, 0.0], 'commanded_position': [2.5, 0.3, 0.0]})) + '\n')
    pts, unrouted = PR.visited_points(tmp_path, start_xy=(-1, 0))
    assert [-1, 0] in pts and [1.0, 0.0] in pts and [9.0, 9.0] not in pts
    assert unrouted == 1                                                             # the local move without a route
    assert 'Use navigate_to on what you see' in PR.UNREACHABLE_MESSAGE
    assert not any(ch.isdigit() for ch in PR.UNREACHABLE_MESSAGE)                    # no coordinates / lengths


# ---------------------------------------------------------------- bridge install / perform_return (fake harness)
class _Robot:
    def __init__(self): self.xy = [1.0, 2.0]
    def get_position_orientation(self):
        import torch
        return torch.tensor([self.xy[0], self.xy[1], 0.0]), torch.tensor([0.0, 0.0, 0.0, 1.0])


class _Recorder:
    def __init__(self): self.events = []
    def event(self, kind, data): self.events.append((kind, data))


class _Deadline:
    expired = stop_requested = managed = False
    def check(self): pass


class _Harness:
    def __init__(self):
        from types import SimpleNamespace
        self.backend = SimpleNamespace(robot=_Robot(), steps=0, _active_request_id=None)
        self.recorder, self.deadline = _Recorder(), _Deadline()
        self.budget = SimpleNamespace(max_sim_steps=10 ** 6)
        self.unrestricted_physics = True
        self.revision, self.actions = 0, 0
        self.catalog = {s['name']: deepcopy(s) for s in tool_specs('skills')}
        self.snapshot = {'revision': 0, 'images': []}
    def refresh(self):
        self.snapshot = {'revision': self.revision, 'images': []}
        return deepcopy(self.snapshot)
    def _public_hand_state(self): return 'empty'


def test_install_and_perform_return(monkeypatch):
    from manipulation_agent.runtime import place_return as RPR
    h = _Harness()
    RPR.install(h)
    props = h.catalog['act']['inputSchema']['properties']
    assert 'return_to' in props['primitive']['enum'] and 'return_revision' in props
    h.revision = 1; h.backend.robot.xy = [5.0, 6.0]; h.refresh()                 # robot moved; revision 1 recorded
    assert h._place_poses[1][:2] == (5.0, 6.0) and h._place_poses[0][:2] == (1.0, 2.0)
    calls = []
    monkeypatch.setattr(PR, 'return_to_pose', lambda backend, goal, yaw, limit, **kw: calls.append((goal, kw)) or {'nav_status': 'reached'})
    out = RPR.perform_return(h, 1, 0)
    assert calls[0][0] == (1.0, 2.0) and calls[0][1]['visited_only'] is True
    assert out['effect']['navigation_status'] == 'reached' and h.revision == 2 and h.actions == 1
    assert 'pose' not in json.dumps(out) and '1.0' not in json.dumps(out['effect'])  # no coordinates in the receipt
    import pytest
    from manipulation_agent.contracts import SkillError
    with pytest.raises(SkillError):
        RPR.perform_return(h, 2, 99)                                                 # unknown revision
    def unreachable(*a, **k): raise SkillError('navigation_unreachable', PR.UNREACHABLE_MESSAGE)
    monkeypatch.setattr(PR, 'return_to_pose', unreachable)
    out = RPR.perform_return(h, 2, 0)
    assert out['ok'] is False and 'Use navigate_to on what you see' in out['error']['message']


# ---------------------------------------------------------------- proxy memory is the model-visible one (2026-11-21 probe)
def test_return_to_moves_proxy_memory_and_memory_survives_restart(tmp_path):
    from manipulation_agent.agent.place_options import load_memory, save_memory
    m = StateMemory()
    m.observe('act', nav_args(1), nav_result(1)); apply_name(nav_result(1), m, 'guest room')
    for rev in (2, 3):
        m.observe('act', nav_args(rev), nav_result(rev))
    fwd, obs, _, early = prepare('act', {'primitive': 'return_to', 'target': None, 'revision': 3, 'place': 'guest room'}, m, ret=True)
    res = nav_result(4, travel_m=15.0); res['effect']['primitive'] = 'return_to'      # the bridge receipt says return_to
    m.observe('act', obs, res)
    assert m.current_location == 'loc_1'
    path = tmp_path / 'place_state_memory.pkl'
    save_memory(m, path)
    again = load_memory(path)                                                        # a restarted proxy
    assert again.current_location == 'loc_1' and again.public_view() == m.public_view()
    assert again.resolve_place('guest room') == ('loc_1', 4)
    assert load_memory(tmp_path / 'missing.pkl') is None


def test_bad_name_message_does_not_claim_the_action():
    m = StateMemory(); res = {'ok': False, 'error': {'code': 'invalid_arguments', 'message': 'x'}}
    apply_name(res, m, 'y' * 41)
    assert res['place_name']['message'].startswith('The name was not recorded.')
