"""rc2 MAS_PLACE_STABLE_CHECK (2026-11-22 10:59): simulator-free tests of the release checks split out of
MAS_PLACE_TRANSACTIONAL, the settled-counts decision, the extra settling loop and the switch combinations."""
import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import placement_stable as ST
from manipulation_agent.executors import placement_transaction as TX
from manipulation_agent.observations.boundary import factual_execution_error


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_STABLE_CHECK', raising=False)
    assert ST.enabled() is False and ST.extra_settle_steps() == 0 and ST.settled_counts() is False
    monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
    assert ST.enabled() is True and ST.extra_settle_steps() == 100 and ST.settled_counts() is True


def test_independent_of_transaction_switch(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_TRANSACTIONAL', 'on'); monkeypatch.delenv('MAS_PLACE_STABLE_CHECK', raising=False)
    assert ST.before_release(object(), object(), object(), []) is None          # TX on alone adds no checks
    monkeypatch.delenv('MAS_PLACE_TRANSACTIONAL', raising=False); monkeypatch.setenv('MAS_PLACE_STABLE_CHECK', 'on')
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
    monkeypatch.delenv('MAS_PLACE_STABLE_CHECK', raising=False)
    assert ST.passes(checks) is True                                     # 1d19707: residual motion is diagnostic
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
