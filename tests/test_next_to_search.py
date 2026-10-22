"""Regression: a valid NextTo candidate with invalid support must not end search."""
import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.placement import CheckedPlacement


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Requires torch')
class NextToCandidateRetry(unittest.TestCase):
    def backend(self, successful_candidate):
        import torch
        marker = type('NextTo', (), {})
        states = types.ModuleType('omnigibson.object_states')
        states.NextTo = marker
        patches = patch.dict(sys.modules, {'omnigibson': types.ModuleType('omnigibson'),
                                          'omnigibson.object_states': states})
        patches.start(); self.addCleanup(patches.stop)
        world = {'candidate': None, 'pose_writes': 0, 'prior_item': 'placed', 'grasp': True}
        attempts = []; restored = []; checks = []; records = []
        b = CheckedPlacement()
        held = SimpleNamespace(name='new_item', states={marker: object()},
            set_position_orientation=lambda candidate, orientation: world.update(
                candidate=candidate, pose_writes=world['pose_writes']+1),
            keep_still=lambda: None, wake=lambda: None)
        target = SimpleNamespace(name='reference')
        b.ideal_carry = True; b._carry_contents = []; b._carry_dependencies = []
        b._get_held = lambda: held if world['grasp'] else None
        b._carry_detach = lambda: world.update(grasp=False)
        b._relocate_contents = lambda *args: None
        b._placement_record = records.append
        def load(state, **kwargs):
            restored.append(dict(state)); world.clear(); world.update(state)
        b.og = SimpleNamespace(sim=SimpleNamespace(dump_state=lambda **kw: dict(world),
            load_state=load, get_physics_dt=lambda: .1))
        @contextmanager
        def context(*args, **kwargs):
            saved = dict(world)
            try: yield
            except Exception:
                world.clear(); world.update(saved); raise
        b._placement_context = context
        b._next_to_candidates = lambda *args: [
            {'index': n, 'pose': (n, None), 'support': SimpleNamespace(name='countertop'),
             'radius_m': .12, 'angle_deg': 45*n} for n in (0, 1)]
        def step():
            # The existing item can be disturbed by a failed hypothetical pose.
            if world['candidate'] == 0: world['prior_item'] = 'disturbed'
            attempts.append((world['candidate'], world['pose_writes']))
        b._placement_physics_step = step
        b._placement_contacts = lambda *args: []
        def check(*args):
            checks.append(world['candidate'])
            return world['candidate'] == successful_candidate, {
                'checks': {'next_to': True, 'on_top': world['candidate'] == successful_candidate}}
        b._next_to_check = check
        b._verify_payload = lambda *args: None
        b.robot = SimpleNamespace(get_joint_positions=lambda: (), q_to_action=lambda q: None)
        b._step = lambda action: None
        return b, held, target, world, attempts, restored, checks, records

    def test_failed_support_tries_next_candidate_without_repeated_pose_projection(self):
        b, held, target, world, steps, restored, checks, records = self.backend(1)
        result = b._checked_place_next_to(target, 100, None)
        self.assertEqual(result['primitive'], 'place_next_to')
        self.assertEqual(checks, [0, 1, 1])
        self.assertEqual(world['prior_item'], 'placed')
        self.assertFalse(world['grasp'])
        self.assertEqual(len(restored), 1)
        self.assertTrue(all(writes == 1 for _, writes in steps))
        self.assertEqual([r['status'] for r in records], ['candidate_rejected', 'next_to_verified'])

    def test_exhaustion_restores_held_item_and_previously_placed_item(self):
        b, held, target, world, steps, restored, checks, records = self.backend(None)
        with self.assertRaises(SkillError) as error:
            b._checked_place_next_to(target, 100, None)
        self.assertEqual(error.exception.code, 'sampling_error')
        self.assertEqual(checks, [0, 1])
        self.assertEqual(world['prior_item'], 'placed')
        self.assertIs(b._get_held(), held)

    def test_oriented_pair_placement_still_requires_support_and_nextto(self):
        b, held, target, world, steps, restored, checks, records = self.backend(1)
        original=b._next_to_candidates
        requests=[]
        def candidates(*args):
            requests.append(args[-1])
            return original(*args)
        b._next_to_candidates=candidates
        result=b._checked_place_next_to(target,100,None,90.)
        self.assertEqual(requests,[90.])
        self.assertEqual(checks,[0,1,1])
        self.assertEqual(result['requested_yaw_degrees'],90.)
        self.assertEqual(world['prior_item'],'placed')


if __name__ == '__main__': unittest.main()
