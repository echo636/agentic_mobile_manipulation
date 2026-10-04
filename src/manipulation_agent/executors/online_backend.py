"""Owner-thread, sensor-built navigation for the RGB manipulation backend.

Only depth and camera calibration enter mapping. Simulator pose is a private
localization input. No precomputed floor occupancy or NavMesh is consulted.
The bounded pose actuator is retained; this is not a physical wheel controller.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math

from ..contracts import SkillError
from ..records import now
from .profiling import component


class OnlineNavigation:
    def _config(self, task, gpu, max_steps):
        from .online_scene import register_online_scene
        config = super()._config(task, gpu, max_steps)
        config['scene']['type'] = register_online_scene().__name__
        return config

    def _initialize_online_navigation(self):
        from .cartographer_mapping import CartographerMapper
        self._online_snapshot = None
        self._online_capture = 0
        # Floor reference follows the robot's ground-contact geometry, not a
        # scene floor-height table. This planar navigator assumes one floor.
        self._online_floor_height = float(self.robot.aabb[0][2])
        position = self.robot.get_position_orientation()[0]
        radii = []
        for link in (self.robot.base_footprint_link, *self.robot.floor_touching_base_links):
            points = link.collision_boundary_points_world
            if points is not None and len(points):
                radii.append(float(self.torch.linalg.norm(points[:, :2]-position[:2], dim=1).max()))
        # Radius from actual base/wheel geometry is invariant to yaw, unlike a
        # WORLD-axis bounding box. It never clears cells under/around the robot.
        self._online_robot_radius = max(radii, default=0.)
        if not .05 < self._online_robot_radius < 1.5:
            raise ValueError('Robot base geometry does not supply a usable navigation footprint')
        self._online_mapper = CartographerMapper(self.output / 'online_mapping', deadline=self.deadline)
        self._online_enabled = True

    def _update_online_map(self, *, rendered=False):
        """Capture calibrated four-depth packets without advancing physics.

        Public observe() already rendered these packets. During motion we make
        additional private captures and never mutate public image references.
        """
        import numpy as np
        import omnigibson.utils.transform_utils as T
        from .depth_scan import DepthFrame, ProjectionConfig, project_four_depth_frames, to_mapper_scans
        self.deadline.check(changed=True)
        with component(self, 'online_mapping'):
            if not rendered:
                self._render_rgb_views(require_calibration=True)
            self._online_capture += 1
            capture_id = f'nav-depth-{self._online_capture:06d}'
            frames = []
            for view, sensor in self.rig.items():
                data, _ = self._sensor_packets[view]
                position, orientation = sensor.get_position_orientation()
                transform = np.eye(4)
                transform[:3, :3] = T.quat2mat(orientation).detach().cpu().numpy()
                transform[:3, 3] = position.detach().cpu().numpy()
                frames.append(DepthFrame(view, capture_id, self.steps,
                    data['depth_linear'].detach().cpu().numpy(),
                    self._sensor_intrinsics[view].detach().cpu().numpy(), transform))
            scans = project_four_depth_frames(frames, ProjectionConfig(self._online_floor_height))
            position, orientation = self.robot.get_position_orientation()
            rotation = T.quat2mat(orientation)
            pose = (float(position[0]), float(position[1]), math.atan2(float(rotation[1, 0]), float(rotation[0, 0])))
            self._online_snapshot = self._online_mapper.update(to_mapper_scans(scans, pose), pose=pose,
                timestamp=self.steps*self.og.sim.get_sim_step_dt())
            return self._online_snapshot

    def _online_grid(self, snapshot):
        from .online_navigation import observed_grid
        return observed_grid(snapshot, robot_radius=self._online_robot_radius)

    def _online_plan(self, snapshot, position, point, fixed_goal=None):
        from .online_navigation import plan_online_navigation, STRATEGY
        from ..observations.rig import visible_rig_rays
        grid = self._online_grid(snapshot)
        xy = position[:2].detach().cpu().tolist()
        hint = point[:2].detach().cpu().tolist()
        def in_camera(candidate):
            yaw = math.atan2(hint[1]-candidate[1], hint[0]-candidate[0])
            return bool(visible_rig_rays(candidate, yaw, float(position[2]), self.rig_height,
                                         point.detach().cpu().tolist(), radius=self.rig_radius))
        with component(self, 'navigation_planning'):
            plan = plan_online_navigation(grid, xy, hint, fixed_goal=fixed_goal, candidate_filter=in_camera,
                                          check_cancel=self.deadline.check)
        with (self.output / 'navigation_plans.jsonl').open('a') as stream:
            stream.write(json.dumps({'at':now(), 'audience':'executor_private', 'strategy':STRATEGY,
                'plan':asdict(plan), 'map_sequence':snapshot.sequence, 'map_resolution_m':grid.resolution,
                'map_sha256':hashlib.sha256(grid.free).hexdigest(),
                'collision_substrate':'current_depth_probability_grid', 'precomputed_walkability':False,
                'robot_radius_m':self._online_robot_radius, 'dynamic_collision_check':'observed_map_at_capture',
                'unobserved_space':'blocked'}) + '\n')
        return grid, plan

    def _navigate(self, target, max_steps):
        """Reobserve and replan while executing the selected RGB-point action."""
        import omnigibson.utils.transform_utils as T
        from .gt_navigation import GreedyGridFollower, NavigationError
        from .online_navigation import STRATEGY
        settling = 10
        before = self.steps
        if max_steps <= settling:
            raise SkillError('action_timeout', 'Insufficient navigation control-step budget')
        position, orientation = self.robot.get_position_orientation()
        point = target.get_position_orientation()[0]
        travelled = 0.
        previous = position[:2].detach().cpu().tolist()
        held = self._get_held()
        relative = T.relative_pose_transform(*held.get_position_orientation(), position, orientation) if held else None
        joints = self.robot.get_joint_positions()
        indices = self.torch.tensor([i for i in range(len(joints)) if i not in self.robot.base_idx.tolist()], device=joints.device)
        posture = joints[indices].clone()
        replans = 0
        try:
            snapshot = self._update_online_map()
            grid, plan = self._online_plan(snapshot, position, point)
            initial_plan = plan
            follower = GreedyGridFollower(grid, plan, self.og.sim.get_sim_step_dt())
            rotation = T.quat2mat(orientation)
            map_pose = (*previous, math.atan2(float(rotation[1,0]), float(rotation[0,0])))
            with (self.output/'base_motion.jsonl').open('a') as stream:
                while True:
                    self.deadline.check(changed=self.steps>before)
                    actual_pos, actual_quat = self.robot.get_position_orientation()
                    rotation = T.quat2mat(actual_quat)
                    yaw = math.atan2(float(rotation[1,0]), float(rotation[0,0]))
                    xy = actual_pos[:2].detach().cpu().tolist()
                    # At most 25 cm / 15 degrees of movement between depth maps.
                    # Replanning uses a fixed selected endpoint to avoid oscillating
                    # between similarly ranked approach candidates.
                    if (math.dist(xy, map_pose[:2]) >= .25 or
                            abs(GreedyGridFollower.angle(yaw-map_pose[2])) >= math.radians(15)):
                        snapshot = self._update_online_map()
                        grid, plan = self._online_plan(snapshot, actual_pos, point, fixed_goal=initial_plan.goal)
                        follower = GreedyGridFollower(grid, plan, self.og.sim.get_sim_step_dt())
                        map_pose = (*xy, yaw)
                        replans += 1
                    command = follower.next_pose((*xy, yaw))
                    if command is None:
                        break
                    if self.steps-before >= max_steps-settling:
                        raise SkillError('action_timeout', 'Navigation exhausted its control-step budget', changed=self.steps>before)
                    x, y, next_yaw = command
                    next_position = position.clone()
                    next_position[0] = x
                    next_position[1] = y
                    next_orientation = T.euler2quat(self.torch.tensor([0.,0.,next_yaw], device=orientation.device))
                    self._base_target = {'position':next_position, 'orientation':next_orientation, 'indices':indices,
                                         'posture':posture, 'held':held, 'relative':relative}
                    self._restore_base_target()
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
                    measured, measured_quat = self.robot.get_position_orientation()
                    actual_xy = measured[:2].detach().cpu().tolist()
                    travelled += math.dist(previous, actual_xy)
                    previous = actual_xy
                    stream.write(json.dumps({'at':now(), 'env_step':self.steps,
                        'commanded_position':next_position.tolist(), 'actual_position':measured.tolist(),
                        'actual_orientation':measured_quat.tolist(), 'commanded_yaw':next_yaw,
                        'follower':'online_depth_grid_pose_feedback', 'map_sequence':snapshot.sequence,
                        'audience':'executor_private'})+'\n')
            motion_steps = self.steps-before
            if self._base_target is not None:
                for _ in range(settling):
                    self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            actual_pos, _ = self.robot.get_position_orientation()
            final_error = math.dist(actual_pos[:2].detach().cpu().tolist(), initial_plan.goal)
            if final_error > follower.position_tolerance:
                raise SkillError('navigation_unreachable', 'Final pose verification failed', changed=self.steps>before)
            self._update_online_map()
            return {'motor':'online_depth_grid_feedback_kinematic', 'nav_status':'reached',
                'strategy':STRATEGY, 'motion_steps':motion_steps, 'steps':self.steps-before,
                'final_position_error_m':final_error, 'actual_path_distance_m':travelled,
                'planned_path_distance_m':initial_plan.geodesic_m, 'replans':replans,
                'candidate_count':initial_plan.candidates_considered,
                'reachable_candidates':initial_plan.candidates_reachable,
                'map_sequence':self._online_snapshot.sequence, 'precomputed_walkability':False,
                'max_speed_m_s':.5, 'max_yaw_speed_deg_s':60, 'physical_controller':False}
        except NavigationError as exc:
            raise SkillError(exc.code, str(exc), changed=self.steps>before) from exc
        finally:
            self.navigation_distance += travelled
            self._base_target = None

    def _close_online_navigation(self):
        mapper = getattr(self, '_online_mapper', None)
        if mapper is not None:
            mapper.close()
