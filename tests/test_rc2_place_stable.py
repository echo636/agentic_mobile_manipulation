"""rc2 MAS_PLACE_STABLE_CHECK (2026-11-22 10:59): simulator-free tests of the release checks split out of
MAS_PLACE_TRANSACTIONAL, the settled-counts decision, the extra settling loop and the switch combinations."""
import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import placement_stable as ST
from manipulation_agent.executors import placement_transaction as TX
from manipulation_agent.observations.boundary import factual_execution_error


def test_switch_default_on(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_STABLE_CHECK', raising=False)
    assert ST.enabled() is True and ST.extra_settle_steps() == 100 and ST.settled_counts() is True
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'off')
    assert ST.enabled() is False and ST.extra_settle_steps() == 0 and ST.settled_counts() is False
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    assert ST.enabled() is True and ST.extra_settle_steps() == 100 and ST.settled_counts() is True


def test_independent_of_transaction_switch(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on'); monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'off')
    assert ST.before_release(object(), object(), object(), []) is None          # TX on alone adds no checks
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'off'); monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    assert TX.enabled() is False


def test_off_adds_nothing():
    assert ST.evaluate(90., [90.], [False], None) == {}


def test_standing_item_that_falls_fails():
    before = {'held_standing': True, 'neighbours': [], 'contents': []}
    assert ST.evaluate(80., [], [], before) == {'upright_kept': False}
    assert ST.evaluate(10., [], [], before) == {'upright_kept': True}


def test_item_lying_when_picked_is_not_checked():
    before = {'held_standing': False, 'neighbours': [], 'contents': []}   # hotdog, log, fork lie by design
    assert ST.evaluate(90., [], [], before) == {'upright_kept': True}


def test_knocking_over_a_neighbour_fails():
    before = {'held_standing': False, 'neighbours': ['cup'], 'contents': []}
    assert ST.evaluate(0., [70.], [], before) == {'upright_kept': False}
    assert ST.evaluate(0., [5.], [], before) == {'upright_kept': True}


def test_carrier_contents_must_stay_on_it():
    before = {'held_standing': False, 'neighbours': [], 'contents': ['mug', 'ketchup']}
    assert ST.evaluate(0., [], [True, False], before)['carried_contents_kept'] is False
    assert ST.evaluate(0., [], [True, True], before)['carried_contents_kept'] is True


def test_settled_counts_only_when_on(monkeypatch):
    checks = {'released': True, 'upward_support': True, 'settled': False}
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'off')
    assert ST.passes(checks) is True                                     # 1d19707: residual motion is diagnostic
    monkeypatch.delenv('MAS_PLACE_STABLE_CHECK', raising=False)
    assert ST.passes(checks) is False
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    assert ST.passes(checks) is False
    assert ST.passes({**checks, 'settled': True}) is True
    assert ST.passes({**checks, 'settled': True, 'upright_kept': False}) is False


class _Window:
    """Stable after `after` samples."""
    def __init__(self, after):
        self.n, self.after = 0, after

    def add(self, *a):
        self.n += 1

    def report(self):
        return {'stable': self.n >= self.after}


class _Obj:
    name = 'tea'
    def __init__(self):
        self.states = {}
    def wake(self): pass
    def get_position_orientation(self): return ([0., 0., 0.], [0., 0., 0., 1.])


class _Sim:
    def get_sim_step_dt(self): return 1 / 30
    def get_physics_dt(self): return 1 / 30
    def sync_physx_to_fabric(self): pass


class _Settler:
    def __init__(self):
        self.og = type('OG', (), {'sim': _Sim()})()
        self.physics, self.records = 0, []
    def _placement_physics_step(self): self.physics += 1
    def _placement_record(self, r): self.records.append(r)


def _settle(window, extra):
    from manipulation_agent.executors.placement import CheckedPlacement as Placement
    b = _Settler()
    held, target = _Obj(), _Obj()
    Placement._settle_surface_contacts(b, held, target, [], 50, resting=window, extra_until_rest=extra)
    return b


def test_extra_settle_runs_until_rest_then_stops():
    b = _settle(_Window(after=70), extra=100)
    assert b.physics == 70                                               # 50 normal + 20 until the window is stable
    assert b.records[-1] == {'status': 'stable_check_extra_settle', 'held': 'tea', 'target': 'tea',
                             'extra_steps': 20, 'stable': True}


def test_extra_settle_is_capped_and_reports_unstable():
    b = _settle(_Window(after=10 ** 6), extra=100)
    assert b.physics == 150 and b.records[-1]['stable'] is False


def test_extra_settle_off_is_the_fixed_duration():
    b = _settle(_Window(after=10 ** 6), extra=0)
    assert b.physics == 50 and b.records == []
    b = _settle(_Window(after=1), extra=100)                              # already stable: nothing extra
    assert b.physics == 50 and b.records == []


@pytest.mark.parametrize('stable,tx,expected', [('off', 'off', 'passed'), ('on', 'off', 'result_unconfirmed'),
                                                ('off', 'on', 'passed'), ('on', 'on', 'not_placed')])
def test_four_switch_combinations_fallen_item(monkeypatch, stable, tx, expected):
    """The tea bottle case: every support check passes, the bottle fell over (upright_kept False)."""
    from manipulation_agent.executors.carry import ControlledCarry
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', stable); monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', tx)
    monkeypatch.setattr(TX, 'snapshot', lambda b: 'snap')
    monkeypatch.setattr(TX, 'restore', lambda b, s: (True, None))
    before = ST.before_release(None, None, None, []) if stable == 'off' else \
        {'held_standing': True, 'neighbours': [], 'contents': []}

    class Fake:
        records = []
        primitives = type('P', (), {'_empty_action': staticmethod(lambda: None)})()
        def _get_held(self): return 'tea'
        _carry_contents = []
        def _placement_record(self, r): self.records.append(r)
        def _step(self, a): pass
        def _place_state_once(self, *a, **k):
            checks = {'released': True, 'upward_support': True, 'settled': True, **ST.evaluate(90., [], [], before)}
            if not ST.passes(checks):
                raise SkillError('postcondition_error', 'x', changed=True, stage='released_placement_unconfirmed')
            return 'placed'
    try:
        ControlledCarry._place_state(Fake(), 'place_on_top', 'tray', 10, None)
        got = 'passed'
    except SkillError as exc:
        got = factual_execution_error(exc, 'place_on_top', 'holding_item' if tx == 'on' else 'empty')['code']
    assert got == expected


def _patch_og_states(monkeypatch):
    import sys
    from types import ModuleType
    og = ModuleType('omnigibson')
    og_states = ModuleType('omnigibson.object_states')
    og_states.OnTop = 'OnTop'
    og_states.Inside = 'Inside'
    monkeypatch.setitem(sys.modules, 'omnigibson', og)
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', og_states)


class _PoseObj:
    def __init__(self, name, pos, ori, *, on_target=True, inside_target=False, aabb=None,
                 fixed_base=False, has_keep_still=True):
        self.name = name
        self.pos = pos
        self.ori = ori
        self.fixed_base = fixed_base
        self.aabb = aabb or ([0.0, 0.0, 0.8], [0.1, 0.1, 1.0])
        self.links = {}
        self.keep_still_calls = 0
        self.set_pose_calls = 0
        self.states = {
            'OnTop': type('S', (), {'get_value': staticmethod(lambda t: on_target)})(),
            'Inside': type('S', (), {'get_value': staticmethod(lambda t: inside_target)})(),
        }
        if has_keep_still:
            self.keep_still = self._keep_still

    def _keep_still(self):
        self.keep_still_calls += 1

    def get_position_orientation(self):
        return (self.pos, self.ori)

    def set_position_orientation(self, pos, ori):
        self.set_pose_calls += 1
        self.pos = pos
        self.ori = ori

    def wake(self):
        pass


def test_before_release_clones_neighbour_poses_and_restore_recovers_and_keeps_still(monkeypatch):
    import torch
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')

    t_pos = torch.tensor([1.0, 2.0, 0.85])
    t_ori = torch.tensor([0.0, 0.0, 0.0, 1.0])
    l_pos = [1.1, 2.0, 0.85]
    l_ori = [0.0, 0.0, 0.0, 1.0]
    u_pos = (1.2, 2.0, 0.85)
    u_ori = (0.0, 0.0, 0.0, 1.0)

    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.8, 1.8, 0.78], [1.4, 2.3, 0.85]))
    held = _PoseObj('fork', [0.0, 0.0, 1.0], [0., 0., 0., 1.], aabb=([0., 0., 1.], [0.1, 0.1, 1.1]))
    robot = _PoseObj('robot', [0.0, 0.0, 0.0], [0., 0., 0., 1.])
    n_tensor = _PoseObj('tea_bottle', t_pos, t_ori, aabb=([0.98, 1.98, 0.82], [1.04, 2.04, 1.0]))
    n_list = _PoseObj('mug', l_pos, l_ori, aabb=([1.08, 1.98, 0.82], [1.14, 2.04, 0.95]))
    n_tuple = _PoseObj('bulb', u_pos, u_ori, aabb=([1.18, 1.98, 0.82], [1.24, 2.04, 0.9]), has_keep_still=False)

    backend = type('B', (), {
        'robot': robot,
        'env': type('E', (), {'scene': type('S', (), {'objects': [target, held, robot, n_tensor, n_list, n_tuple]})()})(),
        '_pregrasp_orientations': {},
    })()

    st_before = ST.before_release(backend, held, target, [])
    assert st_before is not None
    assert [o.name for o in st_before['neighbours']] == ['tea_bottle', 'mug', 'bulb']
    assert len(st_before['neighbour_poses']) == 3

    # Mutate original pose buffers in-place and tip all three neighbours 90 deg
    s = 2 ** -.5
    t_pos[0] = 9.9
    t_ori[:] = torch.tensor([s, 0.0, 0.0, s])
    l_pos[0] = 8.8
    l_ori[:] = [s, 0.0, 0.0, s]
    n_tuple.pos = (7.7, 2.0, 0.85)
    n_tuple.ori = (s, 0.0, 0.0, s)

    assert ST.tilt_deg(n_tensor.get_position_orientation()[1]) == pytest.approx(90., abs=0.1)
    assert ST.tilt_deg(n_list.get_position_orientation()[1]) == pytest.approx(90., abs=0.1)
    assert ST.tilt_deg(n_tuple.get_position_orientation()[1]) == pytest.approx(90., abs=0.1)

    ST.restore_neighbours(st_before)
    assert n_tensor.pos.tolist() == pytest.approx([1.0, 2.0, 0.85])
    assert n_tensor.ori.tolist() == pytest.approx([0.0, 0.0, 0.0, 1.0])
    assert n_tensor.keep_still_calls == 1
    assert n_list.pos == pytest.approx([1.1, 2.0, 0.85])
    assert n_list.ori == pytest.approx([0.0, 0.0, 0.0, 1.0])
    assert n_list.keep_still_calls == 1
    assert n_tuple.pos == (1.2, 2.0, 0.85)
    assert n_tuple.ori == (0.0, 0.0, 0.0, 1.0)
    assert n_tuple.keep_still_calls == 0

    # Mutating restored buffers in-place must not corrupt the snapshot stored in st_before
    n_tensor.pos[1] = -5.0
    n_list.pos[1] = -5.0
    ST.restore_neighbours(st_before)
    assert n_tensor.pos.tolist() == pytest.approx([1.0, 2.0, 0.85])
    assert n_list.pos == pytest.approx([1.1, 2.0, 0.85])
    assert n_tensor.keep_still_calls == 2 and n_list.keep_still_calls == 2


def test_off_before_release_none_and_restore_neighbours_noop(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'off')
    assert ST.before_release(object(), object(), object(), []) is None
    assert ST.restore_neighbours(None) is None
    assert ST.upright_failures(object(), None) == []


def test_upright_failures_reports_tilted_held_and_neighbours():
    import math
    q_up = [0.0, 0.0, 0.0, 1.0]
    q_90 = [math.sin(math.radians(45.)), 0.0, 0.0, math.cos(math.radians(45.))]
    q_60 = [math.sin(math.radians(30.)), 0.0, 0.0, math.cos(math.radians(30.))]
    q_10 = [math.sin(math.radians(5.)), 0.0, 0.0, math.cos(math.radians(5.))]

    held = _PoseObj('cup', [0, 0, 0], q_90)
    n_bad = _PoseObj('tea_bottle', [0, 0, 0], q_60)
    n_ok = _PoseObj('mug', [0, 0, 0], q_10)

    before_standing = {'held_standing': True, 'neighbours': [n_bad, n_ok], 'contents': []}
    fails = ST.upright_failures(held, before_standing)
    assert len(fails) == 2
    assert fails[0]['name'] == 'cup' and fails[0]['role'] == 'held' and fails[0]['tilt_deg'] == pytest.approx(90., abs=0.1)
    assert fails[1]['name'] == 'tea_bottle' and fails[1]['role'] == 'neighbour' and fails[1]['tilt_deg'] == pytest.approx(60., abs=0.1)

    # Lying held item is excluded even when tilted 90 deg
    before_lying = {'held_standing': False, 'neighbours': [n_bad, n_ok], 'contents': []}
    fails_lying = ST.upright_failures(held, before_lying)
    assert len(fails_lying) == 1 and fails_lying[0]['name'] == 'tea_bottle'

    # All upright -> empty
    n_bad.ori = q_up
    assert ST.upright_failures(held, before_lying) == []


class _Vec:
    def __init__(self, vals):
        self._v = list(vals)

    def __iter__(self):
        return iter(self._v)

    def __getitem__(self, idx):
        return self._v[idx]

    def tolist(self):
        return list(self._v)

    def clone(self):
        return _Vec(self._v)


class _A5PlaceBackend:
    """Minimal CPU harness for CheckedPlacement._try_place_on_top / _checked_place_inside A5 neighbour recovery."""

    def __init__(self, held, target, neighbour, candidates, *, bad_xs, primitive='place_on_top'):
        self.held = held
        self.target = target
        self.neighbour = neighbour
        self.robot = _PoseObj('robot', [0., 0., 0.], [0., 0., 0., 1.])
        self.env = type('E', (), {'scene': type('S', (), {'objects': [target, held, self.robot, neighbour]})()})()
        self.og = type('OG', (), {'sim': _Sim()})()
        self.primitives = type('P', (), {'_empty_action': staticmethod(lambda: None)})()
        self._carry_contents = []
        self._pregrasp_orientations = {}
        self.sampling_physics_steps = 0
        self.frames_revision = 0
        self.records = []
        self.steps = 0
        self._candidates = list(candidates)
        self._bad_xs = set(bad_xs)
        self._primitive = primitive
        self.selector_neighbour_tilts = []
        self.settle_keep_still_counts = []

    def _get_held(self):
        return self.held

    def _carry_detach(self):
        self.held = None

    def _relocate_contents(self, held, contents):
        pass

    def _placement_record(self, row):
        self.records.append(row)

    def _step(self, action):
        self.steps += 1

    def _container_payload(self, target):
        return []

    def _select_surface_candidate(self, held, target, point, yaw_degrees, payload, *, roll_degrees=None,
                                  exclude_positions=()):
        self.selector_neighbour_tilts.append(ST.tilt_deg(self.neighbour.get_position_orientation()[1]))
        tried_x = {round(float(p.tolist()[0] if hasattr(p, 'tolist') else p[0]), 4) for p in exclude_positions}
        for c in self._candidates:
            if round(float(c['pose'][0].tolist()[0]), 4) not in tried_x:
                return c
        raise SkillError('placement_surface_unavailable', 'none left')

    def _settle_surface_contacts(self, held, target, payload, max_steps, *, resting=None, extra_until_rest=0):
        self.settle_keep_still_counts.append(self.neighbour.keep_still_calls)
        x = round(float(held.get_position_orientation()[0].tolist()[0]), 4)
        if x in self._bad_xs:
            # Simulate first/bad candidate knocking the standing neighbour over to 90 deg
            s = 2 ** -.5
            self.neighbour.pos = [1.2, 2.1, 0.78]
            self.neighbour.ori = [s, 0.0, 0.0, s]
        if resting is not None:
            for _ in range(10):
                resting.add([x, 2.0, 0.85], [0., 0., 0., 1.])

    def _placement_contacts(self, held, payload=()):
        return []

    def _selected_surface_support(self, held, target, vpoint, touching, payload=(), *, resting=None):
        return True, {'checks': {'released': True, 'support_ray_hits_selected_object': True,
                                 'upward_support': True, 'bottom_near_support': True,
                                 'selected_shelf_height': True, 'selected_surface_neighborhood': True,
                                 'selected_contact_consistent': True, 'settled': True,
                                 'not_supported_by_robot': True},
                      'contact_bodies': []}


def test_try_place_on_top_a5_restores_neighbours_and_recovers(monkeypatch):
    from manipulation_agent.executors.placement import CheckedPlacement
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    monkeypatch.setenv('MAS_PLACEMENT_SETTLE', 'on')
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'off')

    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.8, 1.8, 0.78], [1.4, 2.3, 0.85]))
    held = _PoseObj('fork', _Vec([0.0, 0.0, 1.0]), _Vec([0., 0., 0., 1.]), on_target=True)
    tea = _PoseObj('tea_bottle', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=([0.98, 1.98, 0.82], [1.04, 2.04, 1.0]))
    c0 = {'index': 0, 'pose': (_Vec([1.01, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}
    c1 = {'index': 1, 'pose': (_Vec([1.15, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}

    b = _A5PlaceBackend(held, target, tea, [c0, c1], bad_xs={1.01})
    out = CheckedPlacement._try_place_on_top(b, held, target, 50, _Vec([1.0, 2.0, 0.8]))
    assert out == 'selected_surface_collision_support_after_free_settling'

    # Selector was called twice (initial + A5 next_1), and saw tea upright both times (restored before overlap check)
    assert len(b.selector_neighbour_tilts) == 2
    assert all(t < 1.0 for t in b.selector_neighbour_tilts)

    # First normal release did not call restore_neighbours; A5 retries did
    assert b.settle_keep_still_counts[0] == 0 and b.settle_keep_still_counts[1] >= 1

    # Tea bottle ended upright at its pre-release pose and had keep_still invoked during A5 recovery
    assert tea.pos == pytest.approx([1.0, 2.0, 0.85])
    assert ST.tilt_deg(tea.ori) < 1.0
    assert tea.keep_still_calls >= 2

    # Private diagnostics: failed attempts carry upright_failures; final passing c1 does not
    post = [r for r in b.records if r['status'] == 'postcondition_check']
    assert len(post) == 3                                       # first c0 + same_pose c0 + next_1 c1
    assert post[0]['supported'] is False
    assert post[0]['upright_failures'] == [{'name': 'tea_bottle', 'role': 'neighbour', 'tilt_deg': 90.0}]
    assert post[1]['supported'] is False
    assert post[1]['upright_failures'] == [{'name': 'tea_bottle', 'role': 'neighbour', 'tilt_deg': 90.0}]
    assert post[2]['supported'] is True
    assert 'upright_failures' not in post[2]

    fb = [r for r in b.records if r['status'] == 'settle_fallback']
    assert len(fb) == 1 and fb[0]['result'] == 'passed'
    assert [a['candidate'] for a in fb[0]['attempts']] == ['same_pose', 'next_1']


def test_try_place_on_top_all_a5_fail_retains_last_physical_state_and_no_receipt_leak(monkeypatch):
    from manipulation_agent.executors.placement import CheckedPlacement
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    monkeypatch.setenv('MAS_PLACEMENT_SETTLE', 'on')
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'off')
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'off')

    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.8, 1.8, 0.78], [1.4, 2.3, 0.85]))
    held = _PoseObj('fork', _Vec([0.0, 0.0, 1.0]), _Vec([0., 0., 0., 1.]), on_target=True)
    tea = _PoseObj('tea_bottle', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=([0.98, 1.98, 0.82], [1.04, 2.04, 1.0]))
    c0 = {'index': 0, 'pose': (_Vec([1.01, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}
    c1 = {'index': 1, 'pose': (_Vec([1.05, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}
    c2 = {'index': 2, 'pose': (_Vec([1.09, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}

    b = _A5PlaceBackend(held, target, tea, [c0, c1, c2], bad_xs={1.01, 1.05, 1.09})
    with pytest.raises(SkillError) as info:
        CheckedPlacement._try_place_on_top(b, held, target, 50, _Vec([1.0, 2.0, 0.8]))
    assert info.value.code == 'postcondition_error'
    assert info.value.stage == 'released_placement_unconfirmed'
    assert 'tea_bottle' not in str(info.value) and 'upright' not in str(info.value)
    receipt = factual_execution_error(info.value, 'place_on_top', 'empty')
    assert receipt['code'] == 'result_unconfirmed'
    assert 'tea_bottle' not in str(receipt) and 'upright_failures' not in receipt

    # Non-transactional semantics: after all retries fail, last attempt's physical state is retained (not rewound)
    assert ST.tilt_deg(tea.ori) == pytest.approx(90., abs=0.1)
    assert tea.pos == pytest.approx([1.2, 2.1, 0.78])


def test_checked_place_inside_a5_restores_neighbours_and_recovers(monkeypatch):
    from manipulation_agent.executors import inside_sampling
    from manipulation_agent.executors.placement import CheckedPlacement
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    monkeypatch.setenv('MAS_PLACEMENT_SETTLE', 'on')
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'off')

    volume = type('L', (), {'is_meta_link': True, 'meta_link_type': 'openfillable'})()
    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.8, 1.8, 0.78], [1.4, 2.3, 0.85]))
    target.links = {'vol': volume}
    held = _PoseObj('brush', _Vec([0.0, 0.0, 1.0]), _Vec([0., 0., 0., 1.]), inside_target=True)
    bulb = _PoseObj('ggpp_bulb1', [1.0, 2.0, 0.85], [0., 0., 0., 1.], inside_target=True,
                    aabb=([0.98, 1.98, 0.82], [1.04, 2.04, 0.90]))
    c0 = {'index': 0, 'pose': (_Vec([1.01, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}
    c1 = {'index': 1, 'pose': (_Vec([1.15, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}

    b = _A5PlaceBackend(held, target, bulb, [c0, c1], bad_xs={1.01}, primitive='place_inside')

    def fake_select(backend, held_, target_, contents, point=None, residents=(), exclude_positions=()):
        backend.selector_neighbour_tilts.append(ST.tilt_deg(bulb.get_position_orientation()[1]))
        tried_x = {round(float(p.tolist()[0] if hasattr(p, 'tolist') else p[0]), 4) for p in exclude_positions}
        for c in (c0, c1):
            if round(float(c['pose'][0].tolist()[0]), 4) not in tried_x:
                return c
        raise SkillError('placement_surface_unavailable', 'none left')

    monkeypatch.setattr(inside_sampling, 'select_inside_release', fake_select)
    res = CheckedPlacement._checked_place_inside(b, target, 50, _Vec([1.0, 2.0, 0.8]))
    assert res['primitive'] == 'place_inside'
    assert len(b.selector_neighbour_tilts) == 2 and all(t < 1.0 for t in b.selector_neighbour_tilts)
    assert bulb.pos == pytest.approx([1.0, 2.0, 0.85]) and ST.tilt_deg(bulb.ori) < 1.0
    assert bulb.keep_still_calls >= 2

    inside_recs = [r for r in b.records if r['status'] == 'inside_result']
    assert len(inside_recs) == 3
    assert inside_recs[0]['upright_failures'] == [{'name': 'ggpp_bulb1', 'role': 'neighbour', 'tilt_deg': 90.0}]
    assert inside_recs[1]['upright_failures'] == [{'name': 'ggpp_bulb1', 'role': 'neighbour', 'tilt_deg': 90.0}]
    assert 'upright_failures' not in inside_recs[2]
    assert inside_recs[2]['transaction_checks']['upright_kept'] is True


def _aabb_of(hx, hy, hz, *, cx=1.0, cy=2.0, z0=0.82):
    return ([cx - hx / 2, cy - hy / 2, z0], [cx + hx / 2, cy + hy / 2, z0 + hz])


def test_cloth_exempt_in_held_and_neighbours(monkeypatch):
    import math
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')

    target = _PoseObj('prep_counter', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.5, 1.5, 0.78], [1.5, 2.5, 0.85]))
    robot = _PoseObj('robot', [0., 0., 0.], [0., 0., 0., 1.])

    rag_cat = _PoseObj('cdkr_rag', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.008, 0.372, 0.373))
    rag_cat.category = 'rag'

    cloth_pt = _PoseObj('towel_pt', [1.05, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.30, 0.30, 0.15))
    cloth_pt.prim_type = 1

    cloth_particles = _PoseObj('cloth_part', [1.10, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.30, 0.30, 0.15))
    cloth_particles.root_link = type('RL', (), {'compute_particle_positions': lambda self: None})()

    mug = _PoseObj('mug', [1.15, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.09, 0.09, 0.11, cx=1.15))

    for held_cloth in (rag_cat, cloth_pt, cloth_particles):
        assert ST._is_cloth(held_cloth) is True
        assert ST._upright_meaningful(held_cloth) is False
        backend = type('B', (), {
            'robot': robot,
            'env': type('E', (), {'scene': type('S', (), {'objects': [target, robot, rag_cat, cloth_pt, cloth_particles, mug]})()})(),
            '_pregrasp_orientations': {id(held_cloth): [0.0, 0.0, 0.0, 1.0]},
        })()
        st_before = ST.before_release(backend, held_cloth, target, [])
        assert st_before is not None
        assert st_before['held_standing'] is False
        assert [o.name for o in st_before['neighbours']] == ['mug']

        # Released with root_link stuck at 92.11 deg (T5 r7 cdkr_rag failure mode) still passes upright_kept
        half = math.radians(92.11) / 2.0
        held_cloth.ori = [math.sin(half), 0.0, 0.0, math.cos(half)]
        assert ST.after_settle(backend, held_cloth, st_before) == {'upright_kept': True}
        assert ST.upright_failures(held_cloth, st_before) == []


def test_thin_flat_tools_exempt_while_upright_objects_retained(monkeypatch):
    import math
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')

    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.5, 1.5, 0.78], [1.5, 2.5, 0.85]))
    robot = _PoseObj('robot', [0., 0., 0.], [0., 0., 0., 1.])

    brush = _PoseObj('ggpp_brush', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.240, 0.123, 0.048))
    fork = _PoseObj('hl2_fork1', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.175, 0.080, 0.034))
    half_log = _PoseObj('half_ggpp_log_1', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.453, 0.194, 0.077))
    tea = _PoseObj('tea_bottle', [1.05, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.07, 0.07, 0.22, cx=1.05))
    bowl = _PoseObj('bowl', [1.10, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.153, 0.136, 0.080, cx=1.10))
    tissue_box = _PoseObj('box_of_tissues', [1.12, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.24, 0.12, 0.10, cx=1.12))
    alarm_clock = _PoseObj('alarm_clock', [1.13, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.14, 0.09, 0.047, cx=1.13))
    bulb = _PoseObj('ggpp_bulb1', [1.15, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.08, 0.08, 0.10, cx=1.15))
    small_cube = _PoseObj('small_cube', [1.20, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.05, 0.05, 0.05, cx=1.20))
    no_aabb = type('MockNoAabb', (), {'name': 'mock_upright', 'get_position_orientation': lambda self: ([0., 0., 0.], [0., 0., 0., 1.])})()

    assert ST._upright_meaningful(brush) is False
    assert ST._upright_meaningful(fork) is False
    assert ST._upright_meaningful(half_log) is False
    for obj in (tea, bowl, tissue_box, alarm_clock, bulb, small_cube, no_aabb):
        assert ST._upright_meaningful(obj) is True, obj.name

    # Flat brush & half log held upright before grasp -> exempted from held_standing; rolling/flipping still passes
    backend = type('B', (), {
        'robot': robot,
        'env': type('E', (), {'scene': type('S', (), {'objects': [target, robot, brush, fork, half_log, tea, bowl, tissue_box, bulb]})()})(),
        '_pregrasp_orientations': {id(o): [0.0, 0.0, 0.0, 1.0] for o in (brush, half_log, tea, bowl, tissue_box, bulb)},
    })()
    st_brush = ST.before_release(backend, brush, target, [])
    assert st_brush['held_standing'] is False
    assert [o.name for o in st_brush['neighbours']] == ['tea_bottle', 'bowl', 'box_of_tissues', 'ggpp_bulb1']
    half_179 = math.radians(179.0) / 2.0
    brush.ori = [math.sin(half_179), 0.0, 0.0, math.cos(half_179)]
    assert ST.after_settle(backend, brush, st_brush) == {'upright_kept': True}

    st_log = ST.before_release(backend, half_log, target, [])
    assert st_log['held_standing'] is False
    half_167 = math.radians(167.0) / 2.0
    half_log.ori = [math.sin(half_167), 0.0, 0.0, math.cos(half_167)]
    assert ST.after_settle(backend, half_log, st_log) == {'upright_kept': True}

    # Tea bottle, bowl, tissue box, and bulb all keep held_standing=True
    for upright_obj in (tea, bowl, tissue_box, bulb):
        st_obj = ST.before_release(backend, upright_obj, target, [])
        assert st_obj['held_standing'] is True

    # Flat carrier tray still checks carried_contents_kept even though tray itself is thin
    tray_carrier = _PoseObj('mhhoga_tray', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.76, 0.71, 0.025))
    assert ST._upright_meaningful(tray_carrier) is False
    st_carrier = ST.before_release(backend, tray_carrier, target, [(tea, None)])
    assert st_carrier['held_standing'] is False and st_carrier['contents'] == [tea]
    assert ST.evaluate(0.0, [], [False], st_carrier)['carried_contents_kept'] is False


def test_before_release_records_neighbour_aabbs_and_restore_skips_ineffective_physx_sync(monkeypatch):
    import torch
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')

    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.5, 1.5, 0.78], [1.5, 2.5, 0.85]))
    held = _PoseObj('cup', [0., 0., 1.], [0., 0., 0., 1.], aabb=_aabb_of(0.08, 0.08, 0.10))
    robot = _PoseObj('robot', [0., 0., 0.], [0., 0., 0., 1.])
    lo_t = torch.tensor([0.96, 1.96, 0.82])
    hi_t = torch.tensor([1.04, 2.04, 0.92])
    bulb = _PoseObj('ggpp_bulb1', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=(lo_t, hi_t))

    update_calls = []
    fake_sim = type('FakeSim', (), {
        'current_time': 12.5,
        'pi': type('PI', (), {'update_simulation': staticmethod(lambda *a: update_calls.append(a))})(),
    })()
    backend = type('B', (), {
        'robot': robot,
        'og': type('OG', (), {'sim': fake_sim})(),
        'env': type('E', (), {'scene': type('S', (), {'objects': [target, held, robot, bulb]})()})(),
        '_pregrasp_orientations': {},
    })()

    assert ST.restored_neighbour_aabbs(None) == ()
    st_before = ST.before_release(backend, held, target, [])
    assert st_before is not None
    expected_aabb = ((pytest.approx(0.96), pytest.approx(1.96), pytest.approx(0.82)),
                     (pytest.approx(1.04), pytest.approx(2.04), pytest.approx(0.92)))
    aabbs = ST.restored_neighbour_aabbs(st_before)
    assert len(aabbs) == 1
    assert aabbs[0][0] == expected_aabb[0] and aabbs[0][1] == expected_aabb[1]

    # Mutating original AABB tensors in-place must not affect the cloned tuples
    lo_t[:] = torch.tensor([9.0, 9.0, 9.0])
    hi_t[:] = torch.tensor([9.5, 9.5, 9.5])
    assert ST.restored_neighbour_aabbs(st_before)[0][0] == expected_aabb[0]

    # restore_neighbours resets pose & velocity without calling ineffective pi.update_simulation(0.0, t)
    bulb.pos = [1.3, 2.2, 0.78]
    bulb.ori = [2 ** -.5, 0., 0., 2 ** -.5]
    ST.restore_neighbours(st_before, backend)
    assert bulb.pos == pytest.approx([1.0, 2.0, 0.85])
    assert bulb.keep_still_calls == 1
    assert update_calls == []
    assert not hasattr(ST, '_sync_physx_scene_query')


def test_a5_next_candidate_sets_and_clears_restored_neighbour_aabbs_on_top_and_inside(monkeypatch):
    from manipulation_agent.executors import inside_sampling
    from manipulation_agent.executors.placement import CheckedPlacement
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    monkeypatch.setenv('MAS_PLACEMENT_SETTLE', 'on')
    monkeypatch.setenv('MAS_SUPPORT_JUDGE', 'off')

    # 1) place_on_top path (both recovery and SkillError cleanup)
    target = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.8, 1.8, 0.78], [1.4, 2.3, 0.85]))
    held = _PoseObj('cup', _Vec([0.0, 0.0, 1.0]), _Vec([0., 0., 0., 1.]), on_target=True)
    bulb = _PoseObj('ggpp_bulb1', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.08, 0.08, 0.10))
    c0 = {'index': 0, 'pose': (_Vec([1.01, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}
    c1 = {'index': 1, 'pose': (_Vec([1.15, 2.0, 0.85]), _Vec([0., 0., 0., 1.]))}

    b = _A5PlaceBackend(held, target, bulb, [c0, c1], bad_xs={1.01})
    seen_aabbs = []
    orig_select = b._select_surface_candidate

    def tracked_select(*a, **k):
        seen_aabbs.append(getattr(b, '_a5_restored_neighbour_aabbs', ()))
        return orig_select(*a, **k)

    b._select_surface_candidate = tracked_select
    out = CheckedPlacement._try_place_on_top(b, held, target, 50, _Vec([1.0, 2.0, 0.8]))
    assert out == 'selected_surface_collision_support_after_free_settling'
    assert seen_aabbs[0] == ()
    assert len(seen_aabbs[1]) == 1
    assert seen_aabbs[1][0][0] == pytest.approx((0.96, 1.96, 0.82))
    assert seen_aabbs[1][0][1] == pytest.approx((1.04, 2.04, 0.92))
    assert b._a5_restored_neighbour_aabbs == ()

    # When next_candidate selector raises SkillError, finally still clears _a5_restored_neighbour_aabbs
    held2 = _PoseObj('cup2', _Vec([0.0, 0.0, 1.0]), _Vec([0., 0., 0., 1.]), on_target=True)
    bulb2 = _PoseObj('ggpp_bulb2', [1.0, 2.0, 0.85], [0., 0., 0., 1.], aabb=_aabb_of(0.08, 0.08, 0.10))
    b_err = _A5PlaceBackend(held2, target, bulb2, [c0], bad_xs={1.01})
    with pytest.raises(SkillError):
        CheckedPlacement._try_place_on_top(b_err, held2, target, 50, _Vec([1.0, 2.0, 0.8]))
    assert b_err._a5_restored_neighbour_aabbs == ()

    # 2) place_inside path
    volume = type('L', (), {'is_meta_link': True, 'meta_link_type': 'openfillable'})()
    target_in = _PoseObj('tray', [1.0, 2.0, 0.8], [0., 0., 0., 1.], aabb=([0.8, 1.8, 0.78], [1.4, 2.3, 0.85]))
    target_in.links = {'vol': volume}
    held_in = _PoseObj('brush', _Vec([0.0, 0.0, 1.0]), _Vec([0., 0., 0., 1.]), inside_target=True)
    bulb_in = _PoseObj('ggpp_bulb1', [1.0, 2.0, 0.85], [0., 0., 0., 1.], inside_target=True,
                       aabb=_aabb_of(0.08, 0.08, 0.10))
    b_in = _A5PlaceBackend(held_in, target_in, bulb_in, [c0, c1], bad_xs={1.01}, primitive='place_inside')
    seen_inside_aabbs = []

    def fake_select(backend, held_, target_, contents, point=None, residents=(), exclude_positions=()):
        seen_inside_aabbs.append(getattr(backend, '_a5_restored_neighbour_aabbs', ()))
        tried_x = {round(float(p.tolist()[0] if hasattr(p, 'tolist') else p[0]), 4) for p in exclude_positions}
        for c in (c0, c1):
            if round(float(c['pose'][0].tolist()[0]), 4) not in tried_x:
                return c
        raise SkillError('placement_surface_unavailable', 'none left')

    monkeypatch.setattr(inside_sampling, 'select_inside_release', fake_select)
    res = CheckedPlacement._checked_place_inside(b_in, target_in, 50, _Vec([1.0, 2.0, 0.8]))
    assert res['primitive'] == 'place_inside'
    assert seen_inside_aabbs[0] == ()
    assert len(seen_inside_aabbs[1]) == 1
    assert seen_inside_aabbs[1][0][0] == pytest.approx((0.96, 1.96, 0.82))
    assert b_in._a5_restored_neighbour_aabbs == ()


class _GraphObj:
    """Minimal object with explicit pairwise OnTop/Inside relations for multi-layer stack tests."""

    def __init__(self, name, *, on_top=(), inside=()):
        self.name = name
        self._on_top = set(on_top)
        self._inside = set(inside)
        self.states = {
            'OnTop': type('S', (), {'get_value': lambda _self, sup: sup in self._on_top})(),
            'Inside': type('S', (), {'get_value': lambda _self, sup: sup in self._inside})(),
        }

    def get_position_orientation(self):
        return ([0.0, 0.0, 0.8], [0.0, 0.0, 0.0, 1.0])


def test_carried_contents_kept_supports_multi_layer_stack_and_detects_any_drop(monkeypatch):
    """B1 (rc2 P0): apple -> plate -> tray (2 layers) and garnish -> apple -> bowl -> tray (3 layers)
    must pass carried_contents_kept even though OmniGibson OnTop is non-transitive, while any layer
    detaching from the carrier chain must fail."""
    _patch_og_states(monkeypatch)
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')

    # 1) 2-layer stack: two apple halves OnTop plate, plate OnTop tray; OnTop(apple, tray) is False
    tray = _GraphObj('hl2_tray')
    plate = _GraphObj('hl2_plate1', on_top=[tray])
    apple0 = _GraphObj('half_hl2_apple_0', on_top=[plate])
    apple1 = _GraphObj('half_hl2_apple_1', on_top=[plate])
    before_2 = {'held_standing': False, 'neighbours': [], 'contents': [plate, apple0, apple1]}
    assert ST.after_settle(None, tray, before_2) == {'upright_kept': True, 'carried_contents_kept': True}

    # Order of contents in list must not matter (apple listed before plate)
    before_2_rev = {'held_standing': False, 'neighbours': [], 'contents': [apple1, apple0, plate]}
    assert ST.after_settle(None, tray, before_2_rev) == {'upright_kept': True, 'carried_contents_kept': True}

    # 2) 3-layer mixed OnTop + Inside stack: garnish OnTop apple Inside bowl OnTop tray
    bowl = _GraphObj('bowl', on_top=[tray])
    apple_in = _GraphObj('apple_in', inside=[bowl])
    garnish = _GraphObj('garnish', on_top=[apple_in])
    before_3 = {'held_standing': False, 'neighbours': [], 'contents': [garnish, apple_in, bowl]}
    assert ST.after_settle(None, tray, before_3) == {'upright_kept': True, 'carried_contents_kept': True}

    # 3) Top item drops off onto the table/floor -> False
    apple1._on_top.clear()
    assert ST.after_settle(None, tray, before_2) == {'upright_kept': True, 'carried_contents_kept': False}

    # 4) Middle layer (plate) drops off tray while apples still sit on the fallen plate -> False
    apple1._on_top.add(plate)
    plate._on_top.clear()
    assert ST.after_settle(None, tray, before_2) == {'upright_kept': True, 'carried_contents_kept': False}

    # 5) Detached cycle between two dropped items never loops and still returns False
    a = _GraphObj('a')
    b = _GraphObj('b', on_top=[a])
    a._on_top.add(b)
    before_cycle = {'held_standing': False, 'neighbours': [], 'contents': [a, b]}
    assert ST.after_settle(None, tray, before_cycle) == {'upright_kept': True, 'carried_contents_kept': False}
