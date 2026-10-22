"""Endpoint search must never integrate a hypothetical colliding door pose."""
import random
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.articulation_goal import checked_open_goal
from manipulation_agent.executors.articulation_clearance import moving_link_clearance


class EndpointTests(unittest.TestCase):
    def exercise(self, mode):
        state = [torch.tensor([.2])]
        initial_rng = random.getstate()
        open_type = object()
        class Link:
            prim_path = '/door/leaf'
            is_meta_link = False
            def get_position_orientation(self):
                return torch.tensor([float(state[0][0]), 0., 0.]), torch.tensor([0., 0., 0., 1.])
        link = Link()
        def setter(value, fully):
            random.random()
            state[0] = torch.tensor([float(value)])
            return True
        obj = SimpleNamespace(name='door', prim_path='/door', links={'leaf': link},
            get_joint_positions=lambda: state[0].clone(),
            set_joint_positions=lambda value: state.__setitem__(0, value.clone()), keep_still=lambda: None,
            states={open_type: SimpleNamespace(set_value=setter, clear_cache=lambda: None,
                                               get_value=lambda: float(state[0][0]) > .05)})
        scene = SimpleNamespace(objects=[obj])
        sim = SimpleNamespace(scenes=[scene], dump_state=lambda **kw: state[0].clone(),
            load_state=lambda value, **kw: state.__setitem__(0, value.clone()),
            step_physics=lambda: self.fail('Hypothetical endpoint advanced the live physics world'))
        records = []
        backend = SimpleNamespace(og=SimpleNamespace(sim=sim), env=SimpleNamespace(scene=scene),
            torch=torch, robot=SimpleNamespace(links={}), frames_revision=0,
            _container_payload=lambda obj: [], _relocate_container_payload=lambda payload: None,
            _placement_record=records.append)
        attempted = []
        def query(*args):
            attempted.append(float(state[0][0]))
            if mode == 'query_error':
                raise RuntimeError('native query failed')
            return {'clear': mode == 'alternate' and float(state[0][0]) < .9}
        module = ModuleType('omnigibson.object_states')
        module.Open = open_type
        with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'), 'omnigibson.object_states': module}), \
             patch('manipulation_agent.executors.particle_payload.snapshot_particle_state', return_value=[]), \
             patch('manipulation_agent.executors.particle_payload.restore_particle_state'), \
             patch('manipulation_agent.executors.articulation_clearance.moving_link_clearance', side_effect=query):
            if mode == 'alternate':
                self.assertEqual(checked_open_goal(backend, obj, lambda: None).tolist(), [.875])
            else:
                with self.assertRaises(RuntimeError if mode == 'query_error' else SkillError):
                    checked_open_goal(backend, obj, lambda: None)
        self.assertTrue(torch.equal(state[0], torch.tensor([.2])))
        self.assertEqual(random.getstate(), initial_rng)
        self.assertEqual(backend.frames_revision, -1)
        return attempted

    def test_blocked_full_angle_uses_smaller_clear_angle_without_physics(self):
        self.assertEqual(self.exercise('alternate'), [1., .875])

    def test_no_clear_endpoint_restores_scene(self):
        self.assertEqual(len(self.exercise('all_blocked')), 8)

    def test_query_failure_restores_scene_and_rng(self):
        self.assertEqual(self.exercise('query_error'), [1.])

    def test_clearance_uses_transformed_collision_piece_and_ignores_self(self):
        points = torch.tensor([[x, y, z] for x in (-.5, .5) for y in (-1., 1.) for z in (-.05, .05)])
        matrix = torch.eye(4)
        matrix[:3, 3] = torch.tensor([5., 6., 7.])
        mesh = SimpleNamespace(points=points, scaled_transform=matrix, prim_path='/door/leaf/collision')
        link = SimpleNamespace(prim_path='/door/leaf', collision_meshes={'leaf': mesh})
        calls = []
        obstacle = [False]
        def overlap(half, center, quat, callback, any_hit):
            calls.append((half, center))
            callback(SimpleNamespace(rigid_body='/door/leaf'))
            if obstacle[0]:
                callback(SimpleNamespace(rigid_body='/wall'))
        lazy = ModuleType('omnigibson.lazy')
        lazy.omni = SimpleNamespace(physx=SimpleNamespace(get_physx_scene_query_interface=lambda:
                                   SimpleNamespace(overlap_box=overlap)))
        transforms = SimpleNamespace(mat2quat=lambda value: torch.tensor([0., 0., 0., 1.]))
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils = transforms
        with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'),
                                     'omnigibson.lazy': lazy, 'omnigibson.utils': utils}):
            backend = SimpleNamespace(torch=torch)
            result = moving_link_clearance(backend, [link], {'/door/leaf'}, {'/door/leaf'}, lambda: None)
            self.assertTrue(result['clear'])
            np.testing.assert_allclose(calls[0][1], [5., 6., 7.])
            obstacle[0] = True
            result = moving_link_clearance(backend, [link], {'/door/leaf'}, {'/door/leaf'}, lambda: None)
            self.assertFalse(result['clear'])
            self.assertEqual(result['overlapping_enclosures'], [('/door/leaf', '/wall')])
            link.collision_meshes = {}
            self.assertFalse(moving_link_clearance(backend, [link], {'/door/leaf'}, set(), lambda: None)['clear'])


if __name__ == '__main__':
    unittest.main()
