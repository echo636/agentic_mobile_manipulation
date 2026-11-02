"""Placement failure must retain the one real release and its side effects."""
import importlib.util
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.placement import CheckedPlacement


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires torch')
class NextToSingleRelease(unittest.TestCase):
    def backend(self, valid=True):
        import torch
        marker = type('NextTo', (), {})
        states = types.ModuleType('omnigibson.object_states'); states.NextTo = marker
        context = patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                          'omnigibson.object_states': states})
        context.start(); self.addCleanup(context.stop)
        world = {'held': True, 'writes': 0, 'settles': 0, 'commits': 0}
        b = CheckedPlacement(); b.torch = torch
        def place(position, orientation):
            world.update(writes=world['writes']+1, candidate=int(position[0]))
        held = SimpleNamespace(name='item', states={marker: object()},
            set_position_orientation=place, keep_still=lambda: None, wake=lambda: None)
        target = SimpleNamespace(name='reference'); support = SimpleNamespace(name='floor')
        b._carry_contents = []; b._carry_dependencies = []
        b._get_held = lambda: held if world['held'] else None
        b._carry_detach = lambda: world.update(held=False)
        b._relocate_contents = lambda *args: None
        b._placement_record = lambda row: None
        b._next_to_candidates = lambda *args: [
            dict(index=i, pose=(torch.tensor([float(i), 0., 0.]), None),
                 support=support, support_position=[0., 0., 0.]) for i in range(2)]
        b._inside_pose_overlap_ranker = lambda *args, **kwargs: lambda p, q: 1-int(p[0])
        b._settle_surface_contacts = lambda *args: world.update(settles=world['settles']+1,
                                                               neighbor='moved_by_physics')
        b._next_to_check = lambda *args, **kwargs: (valid, {})
        b._verify_payload = lambda *args: None
        b._step = lambda action: world.update(commits=world['commits']+1)
        b.primitives = SimpleNamespace(_empty_action=lambda: None)
        return b, target, world

    def test_geometry_selects_one_release_without_trial_physics(self):
        b, target, world = self.backend()
        result = b._checked_place_next_to(target, 100, b.torch.zeros(3), 90.)
        self.assertEqual(result['requested_yaw_degrees'], 90.)
        self.assertEqual(world, dict(held=False, writes=1, settles=1, commits=1,
                                    candidate=1, neighbor='moved_by_physics'))

    def test_failed_result_keeps_release_and_does_not_restore_neighbors(self):
        b, target, world = self.backend(valid=False)
        with self.assertRaises(SkillError) as error:
            b._checked_place_next_to(target, 100, b.torch.zeros(3))
        self.assertEqual(error.exception.code, 'postcondition_error')
        self.assertEqual(world, dict(held=False, writes=1, settles=1, commits=1,
                                    candidate=1, neighbor='moved_by_physics'))

    def test_missing_support_does_not_release(self):
        b, target, world = self.backend(); b._next_to_candidates = lambda *args: []
        with self.assertRaises(SkillError) as error:
            b._checked_place_next_to(target, 100, b.torch.zeros(3))
        self.assertEqual(error.exception.code, 'placement_surface_unavailable')
        self.assertEqual(world, dict(held=True, writes=0, settles=0, commits=0))


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires torch')
class NextToShelfSupport(unittest.TestCase):
    def test_actual_shelf_support_and_adjacency_are_both_required(self):
        import torch
        states = types.ModuleType('omnigibson.object_states')
        for name in ('NextTo', 'OnTop', 'Touching', 'VerticalAdjacency'):
            setattr(states, name, type(name, (), {}))
        b = CheckedPlacement(); b.torch = torch
        support = SimpleNamespace(name='shelf_with_back_and_upper_rail')
        target = SimpleNamespace(name='first_shoe')
        point = torch.tensor([1., 2., .6])
        state = {'adjacent': True, 'supported': True, 'on_top': False, 'released': True, 'speed': 0.}
        value = lambda fn: SimpleNamespace(get_value=fn)
        held = SimpleNamespace(states={
            states.NextTo: value(lambda _: state['adjacent']),
            states.OnTop: value(lambda _: state['on_top']),
            states.Touching: value(lambda _: True),
            states.VerticalAdjacency: value(lambda: SimpleNamespace(
                negative_neighbors=[support], positive_neighbors=[support]))},
            get_linear_velocity=lambda: torch.tensor([state['speed'], 0., 0.]),
            get_angular_velocity=lambda: torch.zeros(3),
            get_position_orientation=lambda: (torch.tensor([1., 2., .65]), torch.tensor([0., 0., 0., 1.])))
        b._get_held = lambda: None if state['released'] else held
        b._placement_contacts = lambda *args: ['shelf/base_link']
        b._placement_contact_report = lambda *args, **kwargs: {'admissible': True}
        def physical_support(item, furniture, selected_point, touching, payload, *, require_neighborhood):
            self.assertIs(item, held); self.assertIs(furniture, support)
            self.assertTrue(torch.equal(selected_point, point))
            self.assertFalse(require_neighborhood)
            return state['supported'], {'checks': {'actual_support_patch': state['supported']}}
        b._selected_surface_support = physical_support
        cases = [({}, True), ({'supported': False, 'on_top': True}, False),
                 ({'adjacent': False}, False), ({'released': False}, False), ({'speed': .2}, False)]
        initial = dict(state)
        with patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                     'omnigibson.object_states': states}):
            for changes, expected in cases:
                with self.subTest(changes=changes):
                    state.update(initial); state.update(changes)
                    valid, details = b._next_to_check(held, target, support, support_point=point)
                    self.assertEqual(valid, expected)
                    self.assertEqual(details['official_on_top'], state['on_top'])
                    self.assertEqual(details['checks']['supported'], state['supported'])


if __name__ == '__main__': unittest.main()
