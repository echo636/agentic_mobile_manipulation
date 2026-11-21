"""rc1 A6 (2026-11-20) carrier contents registry: simulator-free unit tests. Physical checks (loaded tray carried and
placed, today's left-behind cases with the switch on and off) run as probes on the workstation."""
from types import SimpleNamespace

import pytest

from manipulation_agent.executors import carrier_registry as CR
from manipulation_agent.executors.carry import ControlledCarry


class Obj:
    def __init__(self, name, lo=(0, 0, 0), hi=(1, 1, 1), fixed=False):
        self.name, self.aabb, self.fixed_base, self.states = name, (list(lo), list(hi)), fixed, {}
        self.links = {'base_link': SimpleNamespace(prim_path=f'/World/{name}/base_link')}


class Backend:
    def __init__(self, objects=()):
        self.records = []
        self.output = 'unused'
        self.env = SimpleNamespace(scene=SimpleNamespace(objects=list(objects)))

    def _carry_record(self, **data):
        self.records.append(data)


@pytest.fixture(autouse=True)
def switch_on(monkeypatch):
    monkeypatch.setenv('MAS_CARRIER_REGISTRY', 'on')     # default is off since 2026-11-20 21:00; these test the on path


def test_default_is_off(monkeypatch):
    monkeypatch.delenv('MAS_CARRIER_REGISTRY', raising=False)
    assert CR.enabled() is False
    b = Backend(); cup, tray = Obj('cup'), Obj('tray')
    assert CR.register_after_place(b, cup, tray, 'place_on_top') is None and b.records == []


def test_pose_hold_default_on_and_independent_of_registry(monkeypatch):
    # rc2 (20:47): the put-down pose hold is its own switch (default on); the A6 registry stays default off
    monkeypatch.delenv('MAS_CARRIER_REGISTRY', raising=False)
    monkeypatch.delenv('MAS_CARRIER_POSE_HOLD', raising=False)
    assert CR.pose_hold_enabled() is True and CR.enabled() is False
    monkeypatch.setenv('MAS_CARRIER_POSE_HOLD', 'off')
    assert CR.pose_hold_enabled() is False and CR.enabled() is False


def test_registry_register_unregister_descendants():
    r = CR.Registry(); tray, plate, apple = Obj('tray'), Obj('plate'), Obj('apple')
    assert r.register(plate, tray) == 'registered'
    assert r.register(apple, plate) == 'registered'
    assert [(c.name, p.name) for c, p in r.descendants(tray)] == [('plate', 'tray'), ('apple', 'plate')]
    assert r.unregister(plate) and r.parent(plate) is None
    assert r.descendants(tray) == []
    assert [(c.name, p.name) for c, p in r.descendants(plate)] == [('apple', 'plate')]


def test_registry_refuses_self_and_cycle_and_reports_move():
    r = CR.Registry(); a, b, c = Obj('a'), Obj('b'), Obj('c')
    assert r.register(a, a) == 'refused_self'
    r.register(b, a); r.register(c, b)
    assert r.register(a, c) == 'refused_cycle'           # a is an ancestor of c
    assert r.parent(a) is None
    assert r.register(c, a) == 'moved'
    assert r.parent(c) is a


def test_registry_prune_drops_dead_objects():
    r = CR.Registry(); tray, cup, gone = Obj('tray'), Obj('cup'), Obj('gone')
    r.register(cup, tray); r.register(gone, tray)
    assert r.prune({id(tray), id(cup)}) == 1
    assert [c.name for c, _ in r.descendants(tray)] == ['cup']


@pytest.mark.parametrize('lo,hi,official,expected', [
    ((0.4, 0.4, 0.05), (0.6, 0.6, 0.20), False, True),     # resting on top, centre over the tray
    ((0.4, 0.4, 0.13), (0.6, 0.6, 0.30), False, True),     # 9 cm above the top: still within the band
    ((0.4, 0.4, 0.20), (0.6, 0.6, 0.30), False, False),    # 16 cm above the top
    ((1.5, 1.5, 0.05), (1.7, 1.7, 0.20), False, False),    # beside the tray (fell off)
    ((1.5, 1.5, -0.5), (1.7, 1.7, -0.3), True, True),      # official OnTop/Inside wins
    ((0.95, 0.4, 0.0), (1.07, 0.6, 0.1), False, True),     # centre 1.01: inside the 2 cm margin
])
def test_still_on(lo, hi, official, expected):
    assert CR.still_on(lo, hi, (0, 0, 0), (1, 1, 0.04), official) is expected


def test_register_after_place_movable_only_and_switch(monkeypatch):
    b = Backend(); cup, tray, table = Obj('cup'), Obj('tray'), Obj('table', fixed=True)
    assert CR.register_after_place(b, cup, table, 'place_on_top') is None
    assert CR.register_after_place(b, cup, tray, 'place_on_top') == 'registered'
    assert b.records[-1]['event'] == 'carrier_register' and b.records[-1]['carrier'] == 'tray'
    monkeypatch.setenv('MAS_CARRIER_REGISTRY', 'off')
    b2 = Backend()
    assert CR.register_after_place(b2, cup, tray, 'place_on_top') is None and b2.records == []
    assert CR.grasp_contents(b2, tray) == []


def test_unregister_on_grasp_records():
    b = Backend(); cup, tray = Obj('cup'), Obj('tray')
    CR.register_after_place(b, cup, tray, 'place_on_top')
    assert CR.unregister_on_grasp(b, cup) is True
    assert b.records[-1] == {'component': 'rc1_A6_carrier_registry', 'event': 'carrier_unregister',
                             'reason': 'grasped_on_its_own', 'child': 'cup', 'carrier': 'tray'}
    assert CR.unregister_on_grasp(b, cup) is False


def test_grasp_contents_keeps_on_drops_left_and_nested():
    tray = Obj('tray', (0, 0, 0.9), (0.5, 0.5, 0.93))
    plate = Obj('plate', (0.1, 0.1, 0.93), (0.3, 0.3, 0.95))
    apple = Obj('apple', (0.15, 0.15, 0.95), (0.25, 0.25, 1.0))
    mug = Obj('mug', (1.5, 1.5, 0.0), (1.6, 1.6, 0.1))          # fell to the floor beside the counter
    crumb = Obj('crumb', (1.5, 1.5, 0.1), (1.52, 1.52, 0.11))   # registered on the mug
    b = Backend([tray, plate, apple, mug, crumb])
    for child, parent in ((plate, tray), (apple, plate), (mug, tray), (crumb, mug)):
        CR.register_after_place(b, child, parent, 'place_on_top')
    kept = CR.grasp_contents(b, tray)
    assert [(c.name, p.name, k) for c, p, k in kept] == [('plate', 'tray', 'Registered'), ('apple', 'plate', 'Registered')]
    left = [r for r in b.records if r['event'] == 'carrier_unregister']
    assert [r['child'] for r in left] == ['mug'] and left[0]['reason'] == 'left_carrier'
    assert CR.registry(b).parent(mug) is None
    assert CR.registry(b).parent(crumb) is mug          # stays with the mug, not carried with the tray


class Carry:
    """Just enough of the backend for ControlledCarry._grasp_carried_dependencies."""
    def __init__(self, contacts):
        self.robot = Obj('robot'); self.contacts = contacts; self.records = []; self.output = 'unused'

    def _placement_contacts(self, child, payload=()):
        return self.contacts.get(child.name, [])

    def _contact_riders(self, obj, kept, rejected=()):
        return []

    def _carry_record(self, **data):
        self.records.append(data)


def test_external_contact_filter_only_for_unregistered():
    tray, ketchup, mug, apple = Obj('tray'), Obj('ketchup'), Obj('mug'), Obj('apple')
    # ketchup and mug touch each other (and the tray); apple (initial scene, unregistered) touches the counter
    c = Carry({'ketchup': ['/World/tray/base_link', '/World/mug/base_link'],
               'mug': ['/World/tray/base_link', '/World/ketchup/base_link'],
               'apple': ['/World/tray/base_link', '/World/counter/base_link']})
    physical = [(ketchup, tray, 'OnTop'), (mug, tray, 'OnTop'), (apple, tray, 'OnTop')]
    registered = [(ketchup, tray, 'Registered'), (mug, tray, 'Registered')]
    out = ControlledCarry._grasp_carried_dependencies(c, tray, physical, registered)
    assert [(x.name, k) for x, _, k in out] == [('ketchup', 'Registered'), ('mug', 'Registered')]
    assert c.records[-1]['event'] == 'grasp_payload_left_in_place'
    assert [r['object'] for r in c.records[-1]['left']] == ['apple']


def test_external_contact_filter_unchanged_without_registry():
    tray, ketchup, mug = Obj('tray'), Obj('ketchup'), Obj('mug')
    c = Carry({'ketchup': ['/World/tray/base_link', '/World/counter/base_link'], 'mug': ['/World/tray/base_link']})
    out = ControlledCarry._grasp_carried_dependencies(c, tray, [(ketchup, tray, 'OnTop'), (mug, tray, 'OnTop')])
    assert [(x.name, k) for x, _, k in out] == [('mug', 'OnTop')]


def test_pin_contents_noop_paths(monkeypatch):
    b = Backend()
    assert CR.pin_contents(b) == 0                                   # nothing held
    b._grasped_payload = (Obj('tray'), [], []); b._grasped_registered_ids = set()
    assert CR.pin_contents(b) == 0                                   # nothing registered
    monkeypatch.setenv('MAS_CARRIER_REGISTRY', 'off')
    b._grasped_registered_ids = {1}
    assert CR.pin_contents(b) == 0


# ---------- rc2 carrier pose hold (2026-11-21) ----------
import sys
import types


def _fake_og(monkeypatch):
    """Minimal omnigibson.utils.transform_utils + particle_payload stand-ins: poses are (xyz, quat) tuples and
    pose_transform(parent, relative) = parent position + relative position, relative orientation (identity frames)."""
    tu = types.ModuleType('omnigibson.utils.transform_utils')
    tu.pose_transform = lambda p, q, rp, rq: ([p[i] + rp[i] for i in range(3)], list(rq))
    og = types.ModuleType('omnigibson'); ut = types.ModuleType('omnigibson.utils'); ut.transform_utils = tu
    og.utils = ut
    monkeypatch.setitem(sys.modules, 'omnigibson', og)
    monkeypatch.setitem(sys.modules, 'omnigibson.utils', ut)
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.transform_utils', tu)
    from manipulation_agent.executors import particle_payload as PP
    monkeypatch.setattr(PP, 'snapshot_particles', lambda *a, **k: [])
    monkeypatch.setattr(PP, 'relocate_particles', lambda *a, **k: None)


class Body(Obj):
    def __init__(self, name, pos, quat):
        super().__init__(name); self.pose = (list(pos), list(quat)); self.still = 0

    def get_position_orientation(self):
        return self.pose

    def set_position_orientation(self, p, q):
        self.pose = (list(p), list(q))

    def keep_still(self):
        self.still += 1


def test_tilt_degrees():
    assert CR.tilt_degrees([0, 0, 0, 1]) == pytest.approx(0)
    s = 2 ** -.5
    assert CR.tilt_degrees([s, 0, 0, s]) == pytest.approx(90, abs=1e-6)      # lying on its side


def test_hold_pose_after_place_restores_toppled_child_and_records(monkeypatch):
    _fake_og(monkeypatch)
    monkeypatch.delenv('MAS_CARRIER_POSE_HOLD', raising=False)
    s = 2 ** -.5
    tray = Body('tray', [10, 0, 0.3], [0, 0, 0, 1])
    tea = Body('tea', [10.1, 0.05, 0.31], [s, 0, 0, s])                         # toppled after the tray was put down
    cup = Body('cup', [9.9, 0.0, 0.35], [0, 0, 0, 1])                           # still where it should be
    b = Backend([tray, tea, cup])
    contents = [(tea, ([0.1, 0.0, 0.08], [0, 0, 0, 1])), (cup, ([-0.1, 0.0, 0.05], [0, 0, 0, 1]))]
    assert CR.hold_pose_after_place(b, tray, contents) == 1
    assert tea.pose[0] == pytest.approx([10.1, 0.0, 0.38]) and tea.pose[1] == [0, 0, 0, 1] and tea.still == 1
    assert cup.still == 0                                                       # within tolerance: untouched
    rec = b.records[-1]
    assert rec['event'] == 'carrier_put_down_pose_hold' and rec['corrected'][0][0] == 'tea'
    assert rec['tilts_before_deg']['tea'] == pytest.approx(90, abs=0.1) and rec['tilts_after_deg']['tea'] == 0


def test_hold_pose_after_place_off_is_rc1(monkeypatch):
    _fake_og(monkeypatch)
    monkeypatch.setenv('MAS_CARRIER_POSE_HOLD', 'off')
    s = 2 ** -.5
    tray = Body('tray', [0, 0, 0], [0, 0, 0, 1]); tea = Body('tea', [0, 0, 0], [s, 0, 0, s])
    b = Backend([tray, tea])
    assert CR.hold_pose_after_place(b, tray, [(tea, ([0, 0, 0.1], [0, 0, 0, 1]))]) == 0
    assert tea.pose[1] == [s, 0, 0, s] and b.records == []
    CR.record_tilts(b, 'carrier_grasp_tilts', tray, [tea])
    assert b.records == []


def test_record_tilts_on(monkeypatch):
    monkeypatch.delenv('MAS_CARRIER_POSE_HOLD', raising=False)
    tray = Body('tray', [0, 0, 0], [0, 0, 0, 1]); cup = Body('cup', [0, 0, 0], [0, 0, 0, 1])
    b = Backend()
    CR.record_tilts(b, 'carrier_grasp_tilts', tray, [cup])
    assert b.records[-1] == {'component': 'rc1_A6_carrier_registry', 'event': 'carrier_grasp_tilts', 'carrier': 'tray',
                             'tilts_deg': {'cup': 0.0}}
