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
from manipulation_agent.executors.articulation_clearance import moving_link_clearance, collision_piece_enclosures


class EndpointTests(unittest.TestCase):
    def exercise(self, mode):
        initial = torch.tensor([.2, 0.] if mode == 'independent' else [.2])
        state = [initial.clone()]
        initial_rng = random.getstate()
        open_type = object()
        class Link:
            is_meta_link = False
            def __init__(self, index):
                self.index = index
                self.prim_path = '/door/leaf' + str(index)
            def get_position_orientation(self):
                return torch.tensor([float(state[0][self.index]), 0., 0.]), torch.tensor([0., 0., 0., 1.])
        links = {str(i): Link(i) for i in range(len(initial))}
        def setter(value, fully):
            random.random()
            state[0] = torch.full_like(initial, float(value))
            return True
        obj = SimpleNamespace(name='door', prim_path='/door', links=links,
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
            if mode == 'independent':
                return {'clear': args[2] == {'/door/leaf1'}}
            return {'clear': mode == 'alternate' and float(state[0][0]) < .9}
        module = ModuleType('omnigibson.object_states')
        module.Open = open_type
        with patch.dict(sys.modules, {'omnigibson': ModuleType('omnigibson'), 'omnigibson.object_states': module}), \
             patch('manipulation_agent.executors.particle_payload.snapshot_particle_state', return_value=[]), \
             patch('manipulation_agent.executors.particle_payload.restore_particle_state'), \
             patch('manipulation_agent.executors.articulation_clearance.moving_link_clearance', side_effect=query):
            if mode == 'independent':
                goal = checked_open_goal(backend, obj, lambda: None)
                torch.testing.assert_close(goal, torch.tensor([.2, 1.]))
                self.assertEqual(records[-1]['opening_policy'], 'independent_leaf')
                self.assertEqual(records[-1]['joint_index'], 1)
            elif mode == 'alternate':
                self.assertEqual(checked_open_goal(backend, obj, lambda: None).tolist(), [.875])
            else:
                with self.assertRaises(RuntimeError if mode == 'query_error' else SkillError):
                    checked_open_goal(backend, obj, lambda: None)
        self.assertTrue(torch.equal(state[0], initial))
        self.assertEqual(random.getstate(), initial_rng)
        self.assertEqual(backend.frames_revision, -1)
        return attempted

    def test_blocked_full_angle_uses_smaller_clear_angle_without_physics(self):
        self.assertEqual(self.exercise('alternate'), [1., .875])

    def test_blocked_leaf_can_stay_at_initial_pose_while_other_leaf_opens_without_physics(self):
        self.exercise('independent')

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

    def test_asymmetric_panel_does_not_treat_principal_box_floor_hit_as_mesh_hit(self):
        from scipy.spatial.transform import Rotation
        # Flat lower edge above the floor; an asymmetric upper edge tilts PCA.
        vertices = np.array([[x, y, z] for x, z in [(0., .01), (1., .01),
                             (1., 2.), (0., 1.)] for y in (-.02, .02)])
        transform = np.eye(4)
        enclosures = collision_piece_enclosures(vertices, transform)
        for _, (center, half, axes) in enclosures:
            self.assertTrue((np.abs((vertices-center) @ axes) <= half+1e-8).all())
        principal = dict(enclosures)['principal_axes']
        self.assertLess(principal[0][2] - (np.abs(principal[2]) @ principal[1])[2], 0.)
        mesh = SimpleNamespace(points=torch.tensor(vertices), scaled_transform=torch.eye(4),
                               prim_path='/door/leaf/collision')
        link = SimpleNamespace(prim_path='/door/leaf', collision_meshes={'leaf':mesh})
        wall = [False]
        def overlap(half, center, quat, callback, any_hit):
            if np.asarray(center)[2] - (np.abs(Rotation.from_quat(quat).as_matrix()) @ half)[2] < 0.:
                callback(SimpleNamespace(rigid_body='/floor'))
            if wall[0]:
                callback(SimpleNamespace(rigid_body='/wall'))
        lazy = ModuleType('omnigibson.lazy')
        lazy.omni = SimpleNamespace(physx=SimpleNamespace(get_physx_scene_query_interface=lambda:
                                   SimpleNamespace(overlap_box=overlap)))
        utils = ModuleType('omnigibson.utils')
        utils.transform_utils = SimpleNamespace(mat2quat=lambda value:
            torch.tensor(Rotation.from_matrix(value.numpy()).as_quat()))
        with patch.dict(sys.modules, {'omnigibson':ModuleType('omnigibson'),
                                     'omnigibson.lazy':lazy, 'omnigibson.utils':utils}):
            args = (SimpleNamespace(torch=torch), [link], {'/door/leaf'}, set(), lambda:None)
            self.assertTrue(moving_link_clearance(*args)['clear'])
            wall[0] = True
            result = moving_link_clearance(*args)
            self.assertFalse(result['clear'])
            self.assertEqual(result['overlapping_enclosures'], [('/door/leaf', '/wall')])


if __name__ == '__main__':
    unittest.main()
