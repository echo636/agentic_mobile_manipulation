"""Exercise the real placement sampler with captured articulated-target geometry.

Physics/rays are controlled fixtures. Physical relation retention is separately
validated by the frozen simulator probes; this test locks down candidate choice.
"""
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import sys

import torch
import pytest

from manipulation_agent.executors.placement import CheckedPlacement, placement_transaction
from manipulation_agent.contracts import SkillError


BODY = ([4.663988, -.997822, -.002518], [5.711789, -.119061, 2.038391])
OPEN = ([4.197576, -.997822, -.002518], [6.086658, .233166, 2.040092])
CLOSED = ([4.663988, -.997822, -.002518], [5.711789, -.046904, 2.040089])
DOORS = [([5.657828, -.230132, .054922], [6.086658, .167613, 2.039295]),
         ([4.197576, -.224695, .054880], [4.683464, .233166, 2.040092])]


def box(bounds):
    return tuple(torch.tensor(v) for v in bounds)


def distance_gate(first, second):
    lo, hi = first
    other_lo, other_hi = second
    gaps = torch.clamp(torch.maximum(lo, other_lo)-torch.minimum(hi, other_hi), min=0)
    return bool(torch.linalg.norm(gaps) <= torch.mean(hi-lo+other_hi-other_lo)/6)


@pytest.mark.parametrize("preserve_egress", [False, True, "settled_blocker"],
                         ids=["baseline", "reject_first_exit_blocker", "reject_settled_exit_blocker"])
@pytest.mark.parametrize("articulated", [True, False, "empty_root"], ids=["open_leaves", "rigid_target", "empty_root"])
def test_sampler_avoids_adjacency_only_to_open_leaf(monkeypatch, tmp_path, articulated, preserve_egress):
    states = ModuleType('omnigibson.object_states')
    for name in ('NextTo', 'OnTop', 'Touching', 'VerticalAdjacency'):
        setattr(states, name, type(name, (), {}))
    sampling = ModuleType('omnigibson.utils.sampling_utils')
    for name in ('omnigibson', 'omnigibson.utils'):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', states)
    monkeypatch.setitem(sys.modules, 'omnigibson.utils.sampling_utils', sampling)

    class Item:
        name = 'held_bag'
        def __init__(self):
            self.position = torch.tensor([0., 0., .18])
            self.orientation = torch.tensor([0., 0., 0., 1.])
            self.links = {'body': SimpleNamespace(prim_path='/bag/body')}
        @property
        def aabb(self):
            half = torch.tensor([.11, .11, .18])
            return self.position-half, self.position+half
        def get_position_orientation(self):
            return self.position.clone(), self.orientation.clone()
        def set_position_orientation(self, position, orientation):
            self.position, self.orientation = position.clone(), orientation.clone()
        def keep_still(self):
            pass

    item = Item()
    target = SimpleNamespace(name='articulated_fixture', category='fridge',
                             aabb=box(OPEN if articulated is True else CLOSED), fixed_base=True)
    target.links = {str(i): SimpleNamespace(prim_path=f'/target/link{i}', aabb=box(b))
                    for i, b in enumerate([BODY, *DOORS] if articulated is True else
                        [([0., 0., 0.], [0., 0., 0.]), CLOSED] if articulated == "empty_root" else [CLOSED])}
    target.root_link = target.links['0']
    floor = SimpleNamespace(name='floor', category='floor',
                            links={'body': SimpleNamespace(prim_path='/floor/body')})
    item.states = {
        states.NextTo: SimpleNamespace(get_value=lambda other: distance_gate(item.aabb, other.aabb)),
        states.OnTop: SimpleNamespace(get_value=lambda other: other is floor),
    }
    sampling.raytest = lambda start, end, **kwargs: {
        'hit': True, 'normal': [0, 0, 1], 'rigidBody': '/floor/body',
        'position': torch.tensor([float(start[0]), float(start[1]), 0.])}
    motor = CheckedPlacement()
    motor.output, motor.steps, motor.torch = tmp_path, 0, torch
    motor.robot = SimpleNamespace(links={}, get_joint_positions=lambda: [], q_to_action=lambda q: q)
    motor.env = SimpleNamespace(scene=SimpleNamespace(objects=[item, target, floor]))
    motor.ideal_carry = False
    motor._get_held = lambda: item
    motor._placement_context = lambda target: nullcontext()
    motor._carry_detach = lambda: None
    motor._relocate_contents = lambda *args: None
    motor._step = lambda action: None
    motor._verify_payload = lambda dependencies: None
    checked=[]
    if preserve_egress:
        def accepts(bounds):
            checked.append(tuple(v.clone() for v in bounds))
            return len(checked)==1 if preserve_egress=='settled_blocker' else len(checked)>1
        motor._next_to_egress_checker=lambda: accepts
    if preserve_egress=='settled_blocker':
        initial=item.get_position_orientation()
        sim=SimpleNamespace(dump_state=lambda serialized: item.get_position_orientation(),
                            load_state=lambda pose,serialized: item.set_position_orientation(*pose))
        motor._placement_context=lambda target: placement_transaction(sim,motor._placement_record)
        with pytest.raises(SkillError,match='Settled placement blocks the robot exit'):
            motor._checked_place_next_to(target,50,torch.tensor([4.727143,-.427705,.970486]))
        assert len(checked)==2, 'Candidate accepted before settling, rejected afterwards'
        assert all(torch.equal(a,b) for a,b in zip(item.get_position_orientation(),initial))
        import json
        records=[json.loads(line) for line in (tmp_path/'placement_diagnostics.jsonl').read_text().splitlines()]
        assert records[-1]['status']=='rolled_back'
        assert not any(row.get('status') in ('committed','next_to_verified') for row in records)
        return
    motor._checked_place_next_to(target, 50, torch.tensor([4.727143, -.427705, .970486]))
    assert distance_gate(item.aabb, target.aabb), 'Original open-target condition must hold'
    assert distance_gate(item.aabb, box(CLOSED)), 'Chosen pose must also pass the closed-body distance gate'

    if preserve_egress:
        assert len(checked)>=3, 'Reject first candidate, accept another, and recheck after settling'
        assert not all(torch.equal(a,b) for a,b in zip(item.aabb,checked[0]))
    else:
        # Repeat the real search with a base link occupying the previously
        # accepted pose. Releasing into the robot must select another pose.
        occupied=tuple(v.clone() for v in item.aabb)
        motor.robot.links['base']=SimpleNamespace(prim_path='/robot/base',aabb=occupied)
        item.position=torch.tensor([0.,0.,.18])
        motor._checked_place_next_to(target,50,torch.tensor([4.727143,-.427705,.970486]))
        lower,upper=item.aabb
        assert not bool(((upper>occupied[0]+.005)&(lower<occupied[1]-.005)).all())
        assert distance_gate(item.aabb,target.aabb)
        # A small support patch near the body's edge is missed by the old
        # three-fraction perimeter and click-centered rings.
        motor.robot.links.clear()
        item.position=torch.tensor([0.,0.,.18])
        def narrow_support(start,end,**kwargs):
            valid=4.49<float(start[0])<4.56 and -.98<float(start[1])<-.92
            return {'hit':valid,'normal':[0,0,1],'rigidBody':'/floor/body',
                    'position':torch.tensor([float(start[0]),float(start[1]),0.])}
        sampling.raytest=narrow_support
        motor._checked_place_next_to(target,50,torch.tensor([4.727143,-.427705,.970486]))
        assert 4.49<float(item.position[0])<4.56
        assert -.98<float(item.position[1])<-.92
