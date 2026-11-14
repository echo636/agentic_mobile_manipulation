"""Regression tests for grasped open-carrier payload retention and closed-door latching."""
from contextlib import nullcontext
import types
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import torch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.carry import ControlledCarry
from manipulation_agent.executors.omnigibson_backend import OmniGibsonBackend
from manipulation_agent.executors.omnigibson_rgb import RGBBackend
from manipulation_agent.executors.placement import CheckedPlacement


def _fake_transform_module():
    mod = types.ModuleType('omnigibson.utils.transform_utils')
    mod.relative_pose_transform = lambda cp, cq, pp, pq: (cp - pp, cq.clone())
    mod.pose_transform = lambda pp, pq, rp, rq: (pp + rp, rq.clone())
    mod.quat2mat = lambda q: torch.eye(3)
    mod.euler2quat = lambda e: torch.tensor([0., 0., 0., 1.])
    mod.quat_multiply = lambda a, b: b.clone()
    return mod


def _fake_open_modules():
    og_mod = types.ModuleType('omnigibson')
    states_mod = types.ModuleType('omnigibson.object_states')
    open_mod = types.ModuleType('omnigibson.object_states.open_state')

    class Open:
        pass

    class ToggledOn:
        pass

    class Inside:
        pass

    def _compute_joint_threshold(joint, direction):
        closed_end = joint.lower_limit if direction == 1 else joint.upper_limit
        open_end = joint.upper_limit if direction == 1 else joint.lower_limit
        threshold = 0.95 * closed_end + 0.05 * open_end
        return threshold, open_end, closed_end

    def _is_in_range(pos, threshold, range_end):
        return pos > threshold if range_end > threshold else pos < threshold

    states_mod.Open = Open
    states_mod.ToggledOn = ToggledOn
    states_mod.Inside = Inside
    open_mod._compute_joint_threshold = _compute_joint_threshold
    open_mod._is_in_range = _is_in_range
    utils_mod = types.ModuleType('omnigibson.utils')
    t_mod = _fake_transform_module()
    utils_mod.transform_utils = t_mod
    og_mod.object_states = states_mod
    og_mod.utils = utils_mod
    og_mod.__path__ = []
    states_mod.__path__ = []
    utils_mod.__path__ = []
    return {
        'omnigibson': og_mod,
        'omnigibson.object_states': states_mod,
        'omnigibson.object_states.open_state': open_mod,
        'omnigibson.utils': utils_mod,
        'omnigibson.utils.transform_utils': t_mod,
    }, Open, ToggledOn


class FakeJoint:
    def __init__(self, name, pos=0.0, vel=0.0, lower=0.0, upper=1.5):
        self.name = name
        self.joint_name = name
        self.pos = float(pos)
        self.vel = float(vel)
        self.lower_limit = float(lower)
        self.upper_limit = float(upper)
        self.dof_indices = [0]

    def get_state(self):
        return torch.tensor([self.pos]), torch.tensor([self.vel])

    def set_pos(self, value):
        self.pos = float(value)

    def set_vel(self, value):
        self.vel = float(value)


class CarriedPayloadRetentionTests(unittest.TestCase):
    def _body(self, name, pos=(0., 0., 0.)):
        state = {'pos': torch.tensor(pos, dtype=torch.float32),
                 'ori': torch.tensor([0., 0., 0., 1.], dtype=torch.float32),
                 'still': 0}

        def set_po(position=None, orientation=None, *extra):
            if position is not None:
                state['pos'] = position.clone()
            if orientation is not None:
                state['ori'] = orientation.clone()

        return NS(
            name=name,
            root_link_name='base_link',
            prim_type=0,
            links={'base_link': NS(prim_path=f'/World/{name}/base_link')},
            get_position_orientation=lambda: (state['pos'].clone(), state['ori'].clone()),
            set_position_orientation=set_po,
            keep_still=lambda: state.__setitem__('still', state['still'] + 1),
            _state=state,
        )

    def test_grasp_carried_dependencies_drops_world_supported_riders_only(self):
        tray = self._body('tray')
        cup = self._body('cup')
        fork = self._body('fork')
        world_supported = self._body('world_plate')
        stacked_on_dropped = self._body('berry')
        inside_box = self._body('inside_item')
        robot = self._body('robot')

        contacts = {
            'cup': ['/World/tray/base_link', '/World/fork/base_link'],
            'fork': ['/World/tray/base_link', '/World/cup/base_link'],
            'world_plate': ['/World/tray/base_link', '/World/counter/base_link'],
            'berry': ['/World/world_plate/base_link'],
            'inside_item': ['/World/counter/base_link'],
        }

        backend = ControlledCarry()
        backend.robot = robot
        backend._placement_contacts = lambda child, payload=(): contacts[child.name]

        deps = [
            (cup, tray, 'OnTop'),
            (fork, tray, 'OnTop'),
            (world_supported, tray, 'OnTop'),
            (stacked_on_dropped, world_supported, 'OnTop'),
            (inside_box, tray, 'Inside'),
        ]
        kept = backend._grasp_carried_dependencies(tray, deps)
        self.assertEqual([c.name for c, _, _ in kept], ['cup', 'fork', 'inside_item'])

    def test_grasped_payload_survives_touching_dropout_across_navigation_until_detach(self):
        tray = self._body('tray', (1., 2., 0.8))
        cup = self._body('cup', (1.05, 2.0, 0.83))
        tea = self._body('tea', (0.95, 2.02, 0.83))
        robot = self._body('robot', (0.5, 2.0, 0.0))
        robot.arm_names = ['right']
        held_ref = {'obj': None}
        touching_alive = {'ok': True}

        def establish_grasp(obj, *_args):
            held_ref['obj'] = obj

        def release_grasp(arm=None):
            held_ref['obj'] = None

        robot._establish_grasp = establish_grasp
        robot.release_grasp_immediately = release_grasp

        class DummyBackend(RGBBackend):
            pass

        b = DummyBackend.__new__(DummyBackend)
        b.torch = torch
        b.robot = robot
        b.env = NS(scene=NS(objects=[tray, cup, tea, robot], active_systems={}))
        b.primitives = NS(arm='right', _empty_action=lambda: torch.zeros(2),
                          _get_obj_in_hand=lambda: held_ref['obj'])
        b._placement_contacts = lambda child, payload=(): [f'/World/tray/base_link']
        b._grasp_pose = lambda obj, pt, deps: (
            torch.tensor([0.8, 2.0, 1.0]), torch.tensor([0., 0., 0., 1.]), torch.tensor([0.8, 2.0, 1.0])
        )
        # Simulate 1-step wrist deflection breaking Touching right after grasp.
        b._grasp_dependencies = lambda obj: (
            [(cup, tray, 'OnTop'), (tea, tray, 'OnTop')] if touching_alive['ok'] else []
        )

        def step_breaks_touching(_action):
            touching_alive['ok'] = False
            # Also simulate 1mm transient slip on cup during the step.
            cup._state['pos'] = cup._state['pos'] + torch.tensor([0., 0., 0.001])

        b._step = step_breaks_touching

        constants_mod = types.ModuleType('omnigibson.utils.constants')
        constants_mod.PrimType = NS(CLOTH=99)
        t_mod = _fake_transform_module()
        with patch.dict('sys.modules', {
            'omnigibson': types.ModuleType('omnigibson'),
            'omnigibson.utils': types.ModuleType('omnigibson.utils'),
            'omnigibson.utils.constants': constants_mod,
            'omnigibson.utils.transform_utils': t_mod,
        }):
            res = b._grasp_state(tray, 10, torch.tensor([1., 2., 0.8]))
            self.assertEqual(res['postcondition'], 'selected_object_in_native_grasp')
            # Even though _grasp_dependencies(tray) is now empty, _carry_contents retains both riders!
            self.assertEqual(b._grasp_dependencies(tray), [])
            self.assertEqual([c.name for c, _, _ in b._carry_dependencies], ['cup', 'tea'])
            self.assertEqual([c.name for c, _ in b._carry_contents], ['cup', 'tea'])

            # Simulate symbolic base teleport 8 meters away (moving robot + native AG tray).
            orig_set_po = robot.set_position_orientation

            def move_robot(pos, ori):
                orig_set_po(pos, ori)
                tray.set_position_orientation(pos + torch.tensor([0.3, 0.0, 1.0]), ori)

            robot.set_position_orientation = move_robot
            b._transfer_base_pose(torch.tensor([10.0, 5.0, 0.0]), torch.tensor([0., 0., 0., 1.]))

            # Both cup and tea must arrive at the exact pre-grasp offsets (+0.05, 0, +0.03) and (-0.05, +0.02, +0.03)!
            torch.testing.assert_close(cup.get_position_orientation()[0], torch.tensor([10.35, 5.00, 1.03]))
            torch.testing.assert_close(tea.get_position_orientation()[0], torch.tensor([10.25, 5.02, 1.03]))

            # If an item is poured out, _drop_grasped_payload_objects drops only that item.
            b._drop_grasped_payload_objects([tea])
            self.assertEqual([c.name for c, _ in b._carry_contents], ['cup'])

            # If cup disappears from scene.objects (e.g. sliced), _carry_contents filters it cleanly.
            b.env.scene.objects = [tray, robot]
            self.assertEqual(b._carry_contents, [])

            # Detach clears _grasped_payload completely.
            b._carry_detach()
            self.assertIsNone(b._grasped_payload)
            self.assertEqual(b._carry_dependencies, [])


class ClosedDoorLatchTests(unittest.TestCase):
    def _appliance(self, name, OpenCls, joints_dirs, *, both_sides=False):
        cache_clears = {'count': 0}

        def get_val():
            for j, d in joints_dirs:
                closed_end = j.lower_limit if d == 1 else j.upper_limit
                open_end = j.upper_limit if d == 1 else j.lower_limit
                thresh = 0.95 * closed_end + 0.05 * open_end
                if (j.pos > thresh if open_end > thresh else j.pos < thresh):
                    return True
            return False

        def set_val(wanted, fully=False):
            for j, d in joints_dirs:
                target = (j.upper_limit if d == 1 else j.lower_limit) if wanted else (
                    j.lower_limit if d == 1 else j.upper_limit)
                j.set_pos(target)
                j.set_vel(0.0)
            return True

        open_state = NS(
            _initialized=True,
            relevant_joints_info=(both_sides, [j for j, _ in joints_dirs], [d for _, d in joints_dirs]),
            get_value=get_val,
            set_value=set_val,
            clear_cache=lambda: cache_clears.__setitem__('count', cache_clears['count'] + 1),
        )
        obj = NS(
            name=name,
            n_joints=len(joints_dirs),
            joints={j.name: j for j, _ in joints_dirs},
            links={},
            states={OpenCls: open_state},
            keep_still=lambda: [j.set_vel(0.0) for j, _ in joints_dirs],
            state_updated=Mock(),
            _cache_clears=cache_clears,
        )
        return obj

    def test_closed_doors_stay_latched_across_repeated_nudges_while_open_doors_stay_free(self):
        mods, OpenCls, _ = _fake_open_modules()
        fridge_j = FakeJoint('door', pos=0.005, lower=0.0, upper=1.5)
        oven_j = FakeJoint('drop_door', pos=0.0, lower=0.0, upper=1.57)
        open_cab_j = FakeJoint('open_door', pos=0.8, lower=0.0, upper=1.5)

        fridge = self._appliance('fridge', OpenCls, [(fridge_j, 1)])
        oven = self._appliance('oven', OpenCls, [(oven_j, 1)])
        open_cab = self._appliance('open_cab', OpenCls, [(open_cab_j, 1)])

        backend = RGBBackend.__new__(RGBBackend)
        backend._check_renderer_health = Mock()
        backend.deadline = NS(check=Mock())
        backend._base_target = None
        backend._trunk_hold_target = None
        backend.robot = NS(name='robot')
        backend.env = NS(scene=NS(objects=[fridge, oven, open_cab, backend.robot]))
        sync_mock = Mock()
        backend.og = NS(sim=NS(_render_on_step=True, render_on_step=lambda _: nullcontext(),
                               sync_physx_to_fabric=sync_mock))
        backend._check_placement_objects = Mock()
        backend._video_frame = Mock()

        def physics_nudge(_action):
            # Bump fridge wide open, pull oven slightly every step (ratchet attack), and swing open_cab.
            fridge_j.pos += 0.25
            fridge_j.vel = 0.4
            oven_j.pos += 3e-5
            open_cab_j.pos += 0.05

        with patch.dict('sys.modules', mods), patch.object(OmniGibsonBackend, '_step', side_effect=physics_nudge):
            for _ in range(100):
                backend._step('noop')

        # Closed fridge and oven never drifted from their initial closed positions (0.005 and 0.0)!
        self.assertAlmostEqual(fridge_j.pos, 0.005, places=6)
        self.assertAlmostEqual(fridge_j.vel, 0.0, places=6)
        self.assertAlmostEqual(oven_j.pos, 0.0, places=6)
        # Already-open cabinet remained free to move under physics!
        self.assertGreater(open_cab_j.pos, 1.0)
        self.assertGreater(sync_mock.call_count, 0)

    def test_state_action_allows_operated_panel_while_latching_neighbor_and_unselected_panel(self):
        mods, OpenCls, _ = _fake_open_modules()
        upper_j = FakeJoint('upper_freezer', pos=0.0, lower=0.0, upper=1.4)
        lower_j = FakeJoint('lower_fridge', pos=0.0, lower=0.0, upper=1.4)
        oven_j = FakeJoint('oven_door', pos=0.0, lower=0.0, upper=1.5)

        fridge = self._appliance('fridge', OpenCls, [(upper_j, 1), (lower_j, 1)])
        fridge.get_joint_positions = lambda: torch.tensor([upper_j.pos, lower_j.pos])
        fridge.set_joint_positions = lambda q: (upper_j.set_pos(q[0]), lower_j.set_pos(q[1]))
        oven = self._appliance('oven', OpenCls, [(oven_j, 1)])

        backend = RGBBackend.__new__(RGBBackend)
        backend._check_renderer_health = Mock()
        backend.deadline = NS(check=Mock())
        backend._base_target = None
        backend._trunk_hold_target = None
        backend.robot = NS(name='robot')
        backend.env = NS(scene=NS(objects=[fridge, oven, backend.robot], active_systems={}))
        backend.primitives = NS(_empty_action=lambda: 'noop')
        backend.og = NS(sim=NS(_render_on_step=True, render_on_step=lambda _: nullcontext(),
                               sync_physx_to_fabric=Mock()))
        backend._check_placement_objects = Mock()
        backend._video_frame = Mock()
        backend._placement_record = Mock()

        art_diag = types.ModuleType('manipulation_agent.executors.articulation_diagnostics')
        art_diag.articulation_sample = lambda *a, **kw: {}
        art_diag.record_articulation = lambda *a, **kw: None
        art_goal = types.ModuleType('manipulation_agent.executors.articulation_goal')
        # Select ONLY lower_j on fridge!
        art_goal.native_open_goal = lambda b, obj, feedback=None: (torch.tensor([0.0, 1.4]), [(lower_j, 1)])
        art_goal.opened_joints = lambda obj, controlled: lower_j.pos > 0.5

        def physics_step(_action):
            # Physics tries to swing unselected upper_j AND neighbor oven_j open!
            upper_j.pos += 0.3
            oven_j.pos += 0.4

        extra_mods = {
            **mods,
            'manipulation_agent.executors.articulation_diagnostics': art_diag,
            'manipulation_agent.executors.articulation_goal': art_goal,
        }
        with patch.dict('sys.modules', extra_mods), patch.object(OmniGibsonBackend, '_step', side_effect=physics_step):
            res = backend._state_action('open', fridge, 10)
            self.assertEqual(res['primitive'], 'open')
            self.assertAlmostEqual(lower_j.pos, 1.4, places=5)
            self.assertAlmostEqual(upper_j.pos, 0.0, places=5)
            self.assertAlmostEqual(oven_j.pos, 0.0, places=5)
            self.assertEqual(backend._active_open_joints, ())

            # Now test close: if an obstacle forces lower_j back open during _step, close MUST still fail honestly!
            def blocked_close_step(_action):
                lower_j.pos = 0.5

            with patch.object(OmniGibsonBackend, '_step', side_effect=blocked_close_step):
                with self.assertRaises(SkillError) as ctx:
                    backend._state_action('close', fridge, 10)
                self.assertEqual(ctx.exception.code, 'postcondition_error')
            self.assertEqual(backend._active_open_joints, ())

            # When unobstructed, close succeeds and subsequent steps latch lower_j closed!
            with patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: None):
                backend._state_action('close', fridge, 10)
            self.assertAlmostEqual(lower_j.pos, 0.0, places=5)

            with patch.object(OmniGibsonBackend, '_step', side_effect=lambda _: setattr(lower_j, 'pos', 0.35)):
                backend._step('noop')
            self.assertAlmostEqual(lower_j.pos, 0.0, places=5)

    def test_settle_surface_contacts_restores_closed_doors_each_step(self):
        mods, OpenCls, _ = _fake_open_modules()
        oven_j = FakeJoint('oven_door', pos=0.0, lower=0.0, upper=1.5)
        oven = self._appliance('oven', OpenCls, [(oven_j, 1)])
        held = NS(name='plate', wake=Mock(), states={})
        target = NS(name='counter', states={})

        backend = RGBBackend.__new__(RGBBackend)
        backend.robot = NS(name='robot')
        backend.env = NS(scene=NS(objects=[oven, held, target, backend.robot]))
        backend.sampling_physics_steps = 0
        backend.deadline = NS(check=Mock())
        backend.og = NS(sim=NS(
            get_sim_step_dt=lambda: 1 / 30,
            get_physics_dt=lambda: 1 / 60,
            step_physics=lambda: setattr(oven_j, 'pos', oven_j.pos + 0.2),
            sync_physx_to_fabric=Mock(),
        ))

        with patch.dict('sys.modules', mods):
            CheckedPlacement._settle_surface_contacts(backend, held, target, (), 4)
        self.assertAlmostEqual(oven_j.pos, 0.0, places=6)


if __name__ == '__main__':
    unittest.main()
