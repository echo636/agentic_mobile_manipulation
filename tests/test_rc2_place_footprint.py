"""rc2 MAS_PLACE_FOOTPRINT (2026-11-22 10:59): simulator-free tests of the footprint geometry, the hit judgement, the
shift search, record mode (behaviour identical to off) and the pre-release refusal."""
import math

import pytest
import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors import placement_footprint as FP


def test_modes(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_FOOTPRINT', raising=False)
    assert FP.mode() == 'off'
    for m in ('record', 'on'):
        monkeypatch.setenv('MAS_PLACE_FOOTPRINT', m)
        assert FP.mode() == m
    monkeypatch.setenv('MAS_PLACE_FOOTPRINT', 'yes')
    assert FP.mode() == 'off'
    monkeypatch.delenv('MAS_PLACE_FOOTPRINT_NORMAL_DEG', raising=False)
    assert FP.normal_max_deg() == 10.
    monkeypatch.setenv('MAS_PLACE_FOOTPRINT_NORMAL_DEG', '5')
    assert FP.normal_max_deg() == 5.


def _cylinder(r, h, n=32):
    out = []
    for k in range(n):
        a = 2 * math.pi * k / n
        out += [(r * math.cos(a), r * math.sin(a), -h / 2), (r * math.cos(a), r * math.sin(a), h / 2)]
    return out


def test_hull_and_samples_use_the_bottom_band_only():
    samples, zmin = FP.footprint_samples(_cylinder(.035, .22))
    assert zmin == pytest.approx(-.11)
    hull = samples[:FP.MAX_HULL]
    assert len(samples) == 2 * FP.MAX_HULL + 1                       # 16 hull + 16 halfway + centroid
    assert max(math.hypot(*p) for p in hull) == pytest.approx(.035, abs=1e-6)
    assert math.hypot(*samples[-1]) == pytest.approx(0, abs=1e-6)


def test_lying_item_uses_the_face_it_lies_on():
    # a fork lying flat: 18 x 2 cm, 1 cm tall; the projected hull is the long thin shape, not a circle
    fork = [(x, y, z) for x in (-.09, .09) for y in (-.01, .01) for z in (-.005, .005)]
    samples, _ = FP.footprint_samples(fork)
    xs = [p[0] for p in samples]; ys = [p[1] for p in samples]
    assert max(xs) == pytest.approx(.09) and max(ys) == pytest.approx(.01)


def test_convex_hull_drops_interior_points():
    assert sorted(FP.convex_hull([(0, 0), (1, 0), (1, 1), (0, 1), (.5, .5)])) == [(0, 0), (0, 1), (1, 0), (1, 1)]


def _hit(owner='tray', nz=1., z=1., distance=.03, hit=True):
    return {'hit': hit, 'distance': distance, 'owner': owner, 'normal_z': nz, 'z': z}


def test_judge_hits():
    acc = lambda o: o == 'tray'
    assert FP.judge_hits([_hit()] * 5, 1., 10., accept=acc) == (True, {})
    slope = math.cos(math.radians(18))                                    # T1 tray rim, 15-20 deg
    assert FP.judge_hits([_hit(), _hit(nz=slope)], 1., 10., accept=acc) == (False, {'not_flat': 1})
    assert FP.judge_hits([_hit(), _hit(nz=slope)], 1., 20., accept=acc)[0] is True
    assert FP.judge_hits([_hit(owner='mug')], 1., 10., accept=acc) == (False, {'other_object': 1})
    assert FP.judge_hits([_hit(hit=False)], 1., 10., accept=acc) == (False, {'off_support': 1})
    assert FP.judge_hits([_hit(distance=0.)], 1., 10., accept=acc) == (False, {'off_support': 1})
    assert FP.judge_hits([_hit(z=1.015)], 1., 10., accept=acc) == (False, {'height_step': 1})


def test_shift_rings_increase_and_stop_at_the_a5_limit():
    rings = FP.shift_rings()
    d = [math.hypot(*s) for s in rings]
    assert all(b >= a - 1e-9 for a, b in zip(d, d[1:])) and d[0] == pytest.approx(.01) and d[-1] == pytest.approx(.12)
    assert len(rings) == 12 * 16


# ---------------------------------------------------------------- apply() with a fake world

class _Obj:
    def __init__(self, name, category=None):
        self.name, self.category, self.links = name, category, {}


class _Backend:
    torch = torch
    def __init__(self):
        self.robot = _Obj('robot'); self.records = []
        self.env = type('E', (), {'scene': type('S', (), {'object_registry': None})()})()
    def _placement_record(self, r): self.records.append(r)


def _candidate(x=0.):
    return {'index': 0, 'pose': (torch.tensor([x, 0., 1.1]), torch.tensor([0., 0., 0., 1.])),
            'support_position': [x, 0., 1.], 'bottom_anchor_offset': [0., 0., -.1],
            'relative_vertices': _cylinder(.035, .2), 'proposal_overlap_score': 0}


def _world(monkeypatch, edge_x):
    """A flat tray for x <= edge_x, a rim slope beyond it."""
    def rays(backend, rxy, samples, bz, cz, ignored):
        return [_hit(nz=1. if rxy[0] + sx <= edge_x else math.cos(math.radians(18)), z=cz) for sx, sy in samples]
    monkeypatch.setattr(FP, '_rays', rays)
    monkeypatch.setattr(FP, '_centre_support', lambda b, xy, z, ig, acc: z)
    monkeypatch.setattr(FP, '_acceptor', lambda b, t: (lambda o: True))


def test_off_returns_the_same_candidate_without_any_record(monkeypatch):
    monkeypatch.delenv('MAS_PLACE_FOOTPRINT', raising=False)
    b, c = _Backend(), _candidate()
    assert FP.apply(b, _Obj('tea'), _Obj('tray'), torch.tensor([0., 0., 1.]), c, [], lambda x: 0) is c
    assert b.records == []


def test_on_shifts_by_the_smallest_displacement(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_FOOTPRINT', 'on'); _world(monkeypatch, edge_x=.03)   # bottom reaches x = .035
    b = _Backend()
    got = FP.apply(b, _Obj('tea'), _Obj('tray'), torch.tensor([0., 0., 1.]), _candidate(), [], lambda x: 0)
    # the first ring (1 cm) is the smallest displacement that fits; any direction in it that clears the rim is minimal
    assert math.hypot(*got['footprint_shift_m']) == pytest.approx(.01, abs=1e-3)
    assert float(got['pose'][0][0]) <= -.005
    r = b.records[-1]
    assert r['status'] == 'footprint_check' and r['first_ok'] is False and r['shift_m'] == .01 and not r['refused']


def test_on_refuses_before_release_when_nothing_fits(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_FOOTPRINT', 'on'); _world(monkeypatch, edge_x=-1.)    # no flat region at all
    b = _Backend()
    with pytest.raises(SkillError) as info:
        FP.apply(b, _Obj('tea'), _Obj('tray'), torch.tensor([0., 0., 1.]), _candidate(), [], lambda x: 0)
    assert info.value.code == 'placement_surface_unavailable'
    assert str(info.value.message if hasattr(info.value, 'message') else info.value).endswith(FP.REFUSAL_TEXT) or \
        FP.REFUSAL_TEXT in str(info.value)
    assert not getattr(info.value, 'changed', False)                       # nothing moved: the item is still held
    assert b.records[-1]['refused'] is True


def test_on_occupied_pose_is_moved_or_refused(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_FOOTPRINT', 'on'); _world(monkeypatch, edge_x=1.)     # all flat
    c = _candidate(); c['proposal_overlap_score'] = .3                    # crowded corner: envelope overlaps a cup
    b = _Backend()
    got = FP.apply(b, _Obj('tea'), _Obj('tray'), torch.tensor([0., 0., 1.]), c, [],
                   lambda s: 0 if float(s['pose'][0][1]) > .025 else .3)  # free space starts 3 cm to +y
    assert float(got['pose'][0][1]) > .025 and b.records[-1]['first_failures'] == {'overlap': .3}
    with pytest.raises(SkillError):
        FP.apply(_Backend(), _Obj('tea'), _Obj('tray'), torch.tensor([0., 0., 1.]), c, [], lambda s: .3)


def test_record_mode_is_behaviour_identical_to_off(monkeypatch):
    """coordinator 10:59 I.3: record only writes the private record; the release pose (and so everything the model
    sees) is the one off would use, even where on would refuse or move it."""
    for edge, overlap in ((-1., 0), (.03, 0), (1., .3)):
        _world(monkeypatch, edge_x=edge)
        c = _candidate(); c['proposal_overlap_score'] = overlap
        monkeypatch.setenv('MAS_PLACE_FOOTPRINT', 'record')
        b = _Backend()
        got = FP.apply(b, _Obj('tea'), _Obj('tray'), torch.tensor([0., 0., 1.]), c, [], lambda s: overlap)
        assert got is c and torch.equal(got['pose'][0], _candidate()['pose'][0])
        assert [r['status'] for r in b.records] == ['footprint_check'] and b.records[0]['mode'] == 'record'


def test_shift_beyond_the_neighbourhood_is_not_used(monkeypatch):
    monkeypatch.setenv('MAS_PLACE_FOOTPRINT', 'on'); _world(monkeypatch, edge_x=-.10)   # flat only 13.5+ cm away
    with pytest.raises(SkillError):
        FP.apply(_Backend(), _Obj('tea'), _Obj('tray'), torch.tensor([.1, 0., 1.]), _candidate(), [], lambda s: 0)


def test_floor_target_accepts_rugs_and_other_floor_pieces():
    b = _Backend()
    objs = {'floors_a': _Obj('floors_a', 'floors'), 'floors_b': _Obj('floors_b', 'floors'),
            'rug_1': _Obj('rug_1', 'rug'), 'shoe': _Obj('shoe', 'shoe')}
    b.env.scene.object_registry = lambda key, name: objs.get(name)
    acc = FP._acceptor(b, objs['floors_a'])
    path = lambda n: f'/World/scene_0/{n}/base_link/collisions'
    assert acc(path('floors_a')) and acc(path('floors_b')) and acc(path('rug_1'))
    assert not acc(path('shoe'))                                          # an item lying on the floor occupies it
    tray = _Obj('tray', 'tray')
    acc = FP._acceptor(b, tray)
    assert not acc(path('floors_a'))
