"""One model-authored EEF goal -> official OG cuRobo -> native open-loop motion.

Only planner construction, collision geometry and frame/device fixes are shared
with GeometryPick. Its pick execution, candidate sampler, body goals and lift
recovery are never called here. Simulator imports remain inside this adapter.
"""
from copy import deepcopy
import math
import os
import time
import traceback
from types import SimpleNamespace

import numpy as np

from ..contracts import SkillError, validate
from ..grasp_geometry import resample_positions
from ..model_pose_protocol import (ENGINE, POSITION_TOLERANCE_M,
                                   ORIENTATION_TOLERANCE_RAD, specs)
from ..oracle_contracts import checked_pose
from ..trajectory_timing import compact_path, dense_joint_samples, MAX_CONTROL_STEPS, estimated_path, authored_path
from .oracle_open_loop import OpenLoopPickTrial
from .oracle_pick import GeometryPick


class ModelPoseManip(GeometryPick):
    planner_cache_attribute = '_model_pose_planners'
    planner_link_names = ()  # No hidden torso target or restoration cost.
    planner_config_prefix = 'curobo_model_pose'
    planner_limit_scales = (.5, .5, .5)

    def __init__(self, backend):
        super().__init__(backend)
        self.log = backend.output / 'model_pose.jsonl'
        self.execution_timing = os.environ.get('MAS_MANIP_TIMING', 'model_authored_v1')
        if self.execution_timing not in ('historical_full_trajectory', 'explicit_fast_v1', 'model_estimated_v1', 'model_authored_v1'):
            raise SkillError('invalid_execution_timing', 'Unknown manipulation timing policy')
        # Do not reuse a planner configured for a different limit policy.
        if self.execution_timing == 'model_authored_v1':
            self.planner_cache_attribute += '_joint_unbounded'

    def event(self, kind, **values):
        if kind == 'plan_result':
            self.last_planning_result = deepcopy(values)
        if kind == 'trajectory_validated':
            self.last_trajectory_validation = deepcopy(values)
        try:
            super().event(kind, **values)
        except (OSError, ValueError) as exc:
            raise SkillError('model_pose_evidence_failed', str(exc)[:500],
                             changed=bool(getattr(getattr(self, 'trial', None), 'steps', 0))) from exc

    def execute(self, *, arm, frame, goal, gripper, contact_target, control_steps=None):
        arguments = dict(arm=arm, frame=frame, goal=goal, gripper=gripper,
                         contact_target=contact_target, revision=0)
        if control_steps is not None:
            arguments['control_steps'] = control_steps
        validate(arguments, specs([])[0]['inputSchema'])
        return self.execute_phases(phases=[dict(name='model_goal', arm=arm, frame=frame,
            goal=goal, gripper=gripper, contact_target=contact_target,
            **({'control_steps': control_steps} if control_steps is not None else {}))])

    def execute_phases(self, phases, program=None):
        from ..manip_programs import checked_phases, ENGINE as PROGRAM_ENGINE
        submitted = deepcopy(phases)
        phases = checked_phases({'phases': phases})
        if phases[0]['arm'] == 'both':
            from .oracle_joint_posture import ModelJointPosture
            return ModelJointPosture(self.b).execute_phases(phases, program=program)
        self.arm = phases[0]['arm']
        engine = PROGRAM_ENGINE if program is not None else ENGINE
        goal_source = ('model_authored_program' if program.get('author', 'model') == 'model'
                       else 'model_selected_program') if program is not None else 'model_authored'
        start_step = self.b.steps
        self.trial = None
        try:
            self.preflight(phases[0]['contact_target'], phases[0]['gripper'])
            self.reference = tuple(v.clone() for v in self.b.robot.get_position_orientation())
            previous = getattr(self.b, '_program_grasp_planning', {})
            if (program is None and previous.get('profile') == 'geometry_pick_v1'
                    and previous.get('arm') == self.arm and self.arm in self.retained
                    and self.retained[self.arm][0].name == previous['target']['object_id']):
                self.enable_legacy_planning()
                self.set_legacy_target(previous['target'])
            self.event('model_goals', engine=engine, goal_source=goal_source,
                submitted_phases=submitted, normalized_phases=phases, program=program,
                goal_count=len(phases), automatic_goal_generation=False)
            with OpenLoopPickTrial(self.b, self.event) as trial:
                self.trial = trial
                trial.joint_limit_checks = self.execution_timing != 'model_authored_v1'
                trial.manifest.update(engine=engine, goal_source=goal_source,
                    timing_policy=self.execution_timing, joint_limit_checks=trial.joint_limit_checks,
                    native_mechanical_limits='unchanged',
                    submitted_phases=submitted, frame='robot_base', arm=self.arm,
                    program=program, automatic_goal_generation=False,
                    planning_scope='one trajectory per authored phase; native gripper changes precede planning subsequent phases')
                self.set_phase('planner_prepare')
                self.get_planner(self.arm)
                outcomes = []
                for phase in phases:
                    q, pose, planning = self.plan_phase(phase)
                    if q is None:
                        outcomes.append({'name': phase['name'], 'reached': False, 'verified': False,
                                         'reason': 'model_goal_planning_failed', 'planning': planning})
                        break
                    outcome = self.execute_planned_phase(phase, q, pose, planning)
                    outcomes.append(outcome)
                    if outcome['verified'] is not True:
                        break
                trial.accepted = len(outcomes) == len(phases) and all(row['verified'] for row in outcomes)
                result = {'engine': engine, 'goal_source': goal_source, 'arm': self.arm,
                          'reached': all(row['reached'] for row in outcomes), 'verified': trial.accepted,
                          'reason': None if trial.accepted else outcomes[-1]['reason'],
                          'phases': outcomes, 'frame': 'robot_base', 'program': program}
                if not trial.accepted:
                    trial.manifest['postcondition_failure'] = result['reason']
            result['execution'] = dict(trial.manifest, trial=str(trial.root))
            if not trial.accepted:
                result['verified'] = False
            return result
        except SkillError:
            raise
        except Exception as exc:
            # Includes setup/import/planner failures: do not kill the bridge.
            self.event('exception', exception=type(exc).__name__, message=str(exc)[:1200],
                       traceback=traceback.format_exc())
            raise SkillError('model_pose_failed', f'{self.phase}: {type(exc).__name__}: {str(exc)[:500]}',
                             changed=self.b.steps != start_step) from exc
        finally:
            self.b._trace_arm = self.b._trace_target = self.b._trace_waypoint = None

    def plan_phase(self, phase, *, prefix=None):
        """Apply the pose contract and plan from the current measured state.

        Keep goals in the call-start input frame. Only the planner's starting
        joints, obstacles and retained-object transforms are refreshed between
        phases; a predicted endpoint is never used in place of actual execution.
        """
        self.b.deadline.check(changed=bool(self.trial.steps))
        self.preflight(phase['contact_target'], phase['gripper'])
        pose = checked_pose(dict(phase['goal'], gripper=phase['gripper']))
        name = phase['name'] if prefix is None else prefix + '/' + phase['name']
        self.set_phase(name + '/plan')
        self.event('pose_phase_goal', phase_name=phase['name'],
            operation_scope='internal_pose_template', start_state_source='measured',
            pose_operation=dict(mode='pose', **{key: deepcopy(value) for key, value in phase.items()
                                               if key != 'name'}))
        with self.trial.measure('curobo_plan_and_validation'):
            self.requested_control_steps = phase.get('control_steps')
            q, planning = self.plan_goal(pose)
        if q is None:
            self.event('pose_phase_result', phase_name=phase['name'],
                outcome=dict(reached=False, verified=False, reason='model_goal_planning_failed', planning=planning))
        return q, pose, planning

    def execute_planned_phase(self, phase, q, pose, planning, *, prefix=None):
        """Stream one validated pose trajectory, then its gripper and checks."""
        name = phase['name'] if prefix is None else prefix + '/' + phase['name']
        self.set_phase(name + '/execute')
        self.trial.stream(q, self.arm)
        endpoint = self.endpoint(pose)
        if endpoint['reached']:
            gripper = phase['gripper']
            if gripper != 'hold':
                self.set_phase(name + '/gripper_' + gripper)
                self.b._set_gripper(self.arm, gripper)
            self.set_phase(name + '/verify')
            outcome = self.verify(pose, gripper)
        else:
            outcome = dict(endpoint, verified=False, reason='eef_goal_not_reached')
        result = dict(outcome, name=phase['name'], planning=planning)
        self.event('pose_phase_result', phase_name=phase['name'], outcome=result)
        return result

    def preflight(self, contact_target, gripper):
        b = self.b
        self.contact_obj = self.contact_link = None
        if contact_target is not None:
            self.contact_obj = b._resolve_object(contact_target['object_id'])
            self.contact_link = b._resolve_link(self.contact_obj, contact_target['link_name'])
        if gripper == 'close' and self.contact_link is None:
            raise SkillError('invalid_arguments', 'close requires an explicit contact_target link')
        self.attachments, self.retained = {}, {}
        for arm in b.robot.arm_names:
            held = b._held(arm)
            if held is None:
                continue
            obj = b._resolve_object(held)
            state = b._grasp_state(arm)
            link = b._resolve_link(obj, state['target_link'])
            # A free rigid link can be carried. An articulated object cannot be
            # approximated by attaching one link while ignoring its joint tree.
            physical_links = [part for part in obj.links.values() if not getattr(part, 'is_meta_link', False)]
            if obj.fixed_base or len(physical_links) != 1:
                raise SkillError('held_geometry_unsupported',
                                 'This pose planner carries free single-link objects; articulated retention needs constrained planning')
            self.attachments[b.robot.eef_link_names[arm]] = link
            self.retained[arm] = (obj, link)
        if gripper == 'close' and self.arm in self.retained:
            obj, link = self.retained[self.arm]
            if obj is not self.contact_obj or link is not self.contact_link:
                raise SkillError('hand_occupied', 'Release the actual held object before closing on another target')
        if (self.contact_link is not None and self.attachments and
                all(self.contact_link is not link for link in self.attachments.values())):
            raise SkillError('contact_with_payload_unsupported',
                             'Contact exceptions with another carried object need an additional carried-pair checker')
        self.b._trace_arm = self.arm

    def plan_goal(self, pose, initial=None):
        if getattr(self, 'legacy_planning', False):
            return self.plan_legacy_goal(pose, initial)
        import omnigibson.utils.transform_utils as T
        b, th = self.b, self.b.torch
        planner = self.planner
        planner.update_obstacles()  # Keep every object/link in the world.
        contact = self.contact_link
        if contact is not None and all(contact is not link for link in self.attachments.values()):
            # Disable ONLY the explicitly named contact link. The complementary
            # sphere/convex-hull check below excludes ONLY selected fingers.
            for mesh in contact.collision_meshes.values():
                self.mg.world_coll_checker.enable_obstacle(enable=False, name=mesh.prim_path)
        else:
            contact = None
        attached = {'attached_obj': self.attachments or None,
                    'attached_obj_scale': {name: 1. for name in self.attachments} or None}
        eef = b.robot.eef_link_names[self.arm]
        # Oracle's robot_base is the mobile base footprint, not the holonomic
        # articulation root. Freeze the input frame and submit world goals, as
        # in the historical executor; the pinned OG API uses its own base_link.
        world = T.pose_transform(*self.reference,
            th.tensor(pose['position_m'], dtype=th.float32),
            th.tensor(pose['quaternion_xyzw'], dtype=th.float32))
        started = time.perf_counter()
        results = planner.compute_trajectories(
            target_pos={eef: world[0]}, target_quat={eef: world[1]},
            is_local=False, max_attempts=6, timeout=12., return_full_result=True,
            skip_obstacle_update=True, initial_joint_pos=initial, **attached)
        result = results[0]
        planning = {'success': bool(result.success.flatten()[0]), 'status': str(result.status),
                    'seconds': time.perf_counter()-started, 'goal_count': 1,
                    'goal_source': 'model_authored', 'attempts': int(result.attempts),
                    'trajopt_attempts': int(result.trajopt_attempts),
                    'attached_eef_links': list(self.attachments), 'attached_object_scale': 1.0,
                    'contact_target': None if contact is None else contact.prim_path}
        self.event('plan_result', **planning)
        if not planning['success']:
            return None, planning
        full = self.mg.get_full_js(result.get_paths()[0]).get_ordered_joint_state(planner.robot_joint_names)
        dt = float(b.og.sim.get_sim_step_dt())
        source_dt = float(self.mg.interpolation_dt)
        raw = resample_positions(full.position.detach().cpu().numpy(), source_dt, dt)
        return self.finish_planned_path(raw, pose, planning, initial, attached, contact, source_dt)

    def finish_planned_path(self, raw, pose, planning, initial, attached, contact, source_dt):
        b, th, planner = self.b, self.b.torch, self.planner
        dt = float(b.og.sim.get_sim_step_dt())
        if self.execution_timing == 'model_authored_v1':
            compact, timing = authored_path(raw, dt,
                control_steps=getattr(self, 'requested_control_steps', None))
        elif self.execution_timing == 'model_estimated_v1':
            indices, rates, evidence = self.execution_rates()
            compact, timing = estimated_path(raw, dt, *rates, active_indices=indices,
                control_steps=getattr(self, 'requested_control_steps', None))
            timing['limits'] = evidence
        else:
            limits = self.mg.robot_cfg.kinematics.get_joint_limits()
            indices = [planner.robot_joint_names.index(name) for name in limits.joint_names]
            rates = []
            for name in ('velocity', 'acceleration', 'jerk'):
                values = getattr(limits, name).detach().cpu().numpy()
                if values.shape != (2, len(indices)):
                    raise ValueError('Pinned cuRobo joint-limit layout changed')
                rates.append(np.min(np.abs(values), axis=0))
            compact, timing = compact_path(raw, dt, *rates, active_indices=indices,
                                           max_steps=MAX_CONTROL_STEPS)
        planning['timing'] = timing
        self.event('trajectory_timing', **timing)
        if compact is None:
            planning.update(success=False, rejection='joint_rate_timing_infeasible')
            return None, planning
        compact = compact.astype(np.float32)
        if self.execution_timing != 'model_authored_v1':
            _, certified = compact_path(compact, dt, *rates, active_indices=indices, max_steps=len(compact)-1)
            if not certified['within_joint_limits']:
                planning.update(success=False, rejection='float32_execution_rate_limit')
                return None, planning
        q = th.as_tensor(compact, dtype=th.float32)
        dense = th.as_tensor(dense_joint_samples(compact, max_joint_increment=.005), dtype=th.float32)
        if bool(planner.check_collisions(dense, skip_obstacle_update=True, initial_joint_pos=initial, **attached).any()):
            planning.update(success=False, rejection='resampled_collision')
            return None, planning
        if contact is not None:
            vertices = self.vertices(SimpleNamespace(links={'contact': contact}))
            if not self.target_clearance(dense, vertices=vertices):
                planning.update(success=False, rejection='nonfinger_contact_target_collision')
                return None, planning
        artifact = 'model_pose_trajectory_' + str(time.time_ns()) + '.npz'
        goal_artifact = ({'goal_joint_names': np.array(list(pose['joint_positions_rad'])),
                          'goal_joint_positions_rad': np.array(list(pose['joint_positions_rad'].values()))}
                         if 'joint_positions_rad' in pose else
                         {'goal_position_m': pose['position_m'], 'goal_quaternion_xyzw': pose['quaternion_xyzw']})
        np.savez(b.output / artifact, position=q.numpy(), source_position=raw, control_dt=dt, source_dt=source_dt,
                 joint_names=np.array(planner.robot_joint_names), **goal_artifact)
        planning.update(trajectory=artifact, control_samples=len(q), control_dt=dt)
        self.event('trajectory_validated', **planning)
        return q, planning

    def enable_legacy_planning(self):
        """Reuse historical contact planning and timing, with free torso posture.

        Model-program execution never inherits the historical torso target or
        tilt/height envelope. Collision checks and the selected limit policy remain.
        The program supplies every EEF goal and candidate in its chosen order.
        """
        self.legacy_planning = True
        self.planner_cache_attribute = ('_program_geometry_pick_eef_planners_joint_unbounded'
            if self.execution_timing == 'model_authored_v1' else '_program_geometry_pick_eef_planners')
        self.planner_config_prefix = 'curobo_program_geometry_pick_eef'
        self.planner_link_names = ()
        self.planner_limit_scales = GeometryPick.planner_limit_scales
        self.event('body_posture_policy', torso_pose_target=False,
                   extra_tilt_or_height_envelope=False,
                   joint_position_limits=self.joint_position_policy())

    def joint_position_policy(self):
        if self.execution_timing != 'model_authored_v1':
            return 'native_robot_model'
        native = getattr(self.b, '_native_joint_limit_policy', {})
        return ('planning_disabled; native_torso_arm_positions_disabled'
                if native.get('status') == 'disabled'
                else 'planning_disabled; native_mechanical_limits_unchanged')

    def set_legacy_target(self, target):
        if target is None:
            raise SkillError('invalid_program_output', 'Historical grasp needs an explicit contact target')
        self.obj = self.b._resolve_object(target['object_id'])
        self.link = self.b._resolve_link(self.obj, target['link_name'])
        physical = [link for link in self.obj.links.values() if not getattr(link, 'is_meta_link', False)]
        if self.obj.fixed_base or len(physical) != 1 or physical[0] is not self.link:
            raise SkillError('held_geometry_unsupported', 'Historical grasp adapter needs a free single-link object')

    def plan_legacy_goal(self, pose, initial=None):
        import omnigibson.utils.transform_utils as T
        th = self.b.torch
        world = T.pose_transform(*self.reference,
            th.tensor(pose['position_m'], dtype=th.float32),
            th.tensor(pose['quaternion_xyzw'], dtype=th.float32))
        contact = self.contact_link
        attached = bool(self.attachments)
        if attached and (len(self.attachments) != 1 or self.arm not in self.retained):
            raise SkillError('held_geometry_unsupported', 'Historical planner supports one selected carried object')
        started = time.perf_counter()
        self.last_planning_result = {}
        q = GeometryPick.plan(self, world[0].detach().cpu().numpy(),
            T.quat2mat(world[1]).detach().cpu().numpy(), initial=initial,
            ignore_target=contact is not None and not attached,
            approach=contact is not None and not attached, attached=attached)
        planning = dict(self.last_planning_result, success=q is not None,
            seconds=time.perf_counter()-started, goal_count=1,
            planning_profile='geometry_pick_v1', planner_implementation='GeometryPick.plan',
            approach_constraint=[1, 1, 1, 1, 1, 0] if contact is not None and not attached else None,
            torso_link=None, extra_body_posture_constraints=False,
            joint_position_limits=self.joint_position_policy(),
            endpoint_goal_source='submitted_program_or_pose')
        if q is None and attached and initial is None:
            # The historical successful lift needed a certified departure from
            # initial table contact. Reuse it only for the exact authored
            # vertical goal, never replace a lateral/orientation goal.
            current_p, current_q = self.b.robot.eef_links[self.arm].get_position_orientation()
            delta = world[0] - current_p
            same_rotation = abs(float(world[1] @ current_q)) > .99999
            if float(delta[:2].norm()) <= 1e-4 and 0 < float(delta[2]) <= .25 and same_rotation:
                q = GeometryPick.plan_support_departure(self,
                    world[0].detach().cpu().numpy(), T.quat2mat(world[1]).detach().cpu().numpy())
                if q is not None:
                    planning.update(success=True, fallback='GeometryPick.plan_support_departure',
                        support_departure_certified=True, control_samples=len(q),
                        trajectory=self.last_trajectory_validation['artifact'],
                        control_dt=float(self.b.og.sim.get_sim_step_dt()),
                        seconds=time.perf_counter()-started)
                    if self.execution_timing == 'explicit_fast_v1':
                        planning.update(success=False, rejection='fast_support_departure_not_certified')
                        return None, planning
                    return q, planning
        if q is None:
            return None, planning
        if self.execution_timing in ('explicit_fast_v1', 'model_estimated_v1', 'model_authored_v1'):
            return self.retime_legacy_path(q, pose, planning, initial, attached, contact)
        # The baseline uses the exact fully validated path returned by the old
        # planner. Do not run it through the new 100-step compactor.
        planning.update(control_samples=len(q),
            trajectory=self.last_trajectory_validation['artifact'],
            control_dt=float(self.b.og.sim.get_sim_step_dt()),
            timing_policy='historical_full_trajectory', limit_scales=list(self.planner_limit_scales))
        return q, planning

    def execution_rates(self):
        """Use native velocity and unscaled model acceleration/jerk for execution.

        The pinned USD parser omits native velocity limits and its planner applies
        velocity_scale twice. Read velocity from the simulated robot instead.
        CSpaceConfig retains unscaled acceleration/jerk maxima even after its
        scaled limits have been constructed. Preserve explicit_fast_v1 caps for
        historical comparisons; model_estimated_v1 adds no execution rate cap.
        """
        limits = self.mg.robot_cfg.kinematics.get_joint_limits()
        names = list(limits.joint_names)
        indices = [self.planner.robot_joint_names.index(name) for name in names]
        robot_names = list(self.b.robot.joints)
        native = self.b.robot.max_joint_velocities.detach().cpu().numpy()
        native = np.asarray([native[robot_names.index(name)] for name in names], dtype=float)
        if not np.isfinite(native).all() or np.any(native <= 0):
            raise ValueError('Native joint velocity limits must be finite and positive')
        planner_rates = []
        for key in ('velocity', 'acceleration', 'jerk'):
            values = getattr(limits, key).detach().cpu().numpy()
            if values.shape != (2, len(names)):
                raise ValueError('Pinned cuRobo joint-limit layout changed')
            planner_rates.append(np.min(np.abs(values), axis=0))
        if self.execution_timing == 'model_estimated_v1':
            cspace = self.mg.robot_cfg.kinematics.cspace
            model_names = list(cspace.joint_names)
            order = [model_names.index(name) for name in names]
            model_rates = []
            for key in ('max_acceleration', 'max_jerk'):
                values = getattr(cspace, key).detach().cpu().numpy()
                if (values.shape != (len(model_names),) or not np.isfinite(values).all()
                        or np.any(values <= 0)):
                    raise ValueError('Unscaled model limits must be finite and positive for every joint')
                model_rates.append(values[order].copy())
            rates = [native.copy(), *model_rates]
            source = dict(velocity='robot.max_joint_velocities',
                          acceleration='cspace.max_acceleration', jerk='cspace.max_jerk')
        else:
            rates = [np.minimum(native, .75), np.minimum(planner_rates[1], 4.5),
                     np.minimum(planner_rates[2], 150.)]
            source = dict(velocity='historical_explicit_fast_v1_cap',
                          acceleration='historical_explicit_fast_v1_cap',
                          jerk='historical_explicit_fast_v1_cap')
        evidence = dict(policy=self.execution_timing, joint_names=names,
            native_max_velocity=native.tolist(),
            execution_limit_source=source,
            planner_limits={key: value.tolist() for key, value in
                zip(('velocity', 'acceleration', 'jerk'), planner_rates)},
            execution_limits={key: value.tolist() for key, value in
                zip(('velocity', 'acceleration', 'jerk'), rates)},
            planner_configuration_changed=False)
        return indices, rates, evidence

    def retime_legacy_path(self, raw, pose, planning, initial, attached, contact):
        """Retime only after old planning, and certify the new interpolants."""
        dt = float(self.b.og.sim.get_sim_step_dt())
        if self.execution_timing == 'model_authored_v1':
            evidence = dict(planning_joint_limits=False, execution_joint_limits=False,
                            native_mechanical_limits=getattr(self.b, '_native_joint_limit_policy', {}).get('status', 'unchanged'))
            compact, timing = authored_path(raw.detach().cpu().numpy(), dt,
                control_steps=getattr(self, 'requested_control_steps', None))
        else:
            indices, rates, evidence = self.execution_rates()
        if self.execution_timing == 'model_estimated_v1':
            compact, timing = estimated_path(raw.detach().cpu().numpy(), dt, *rates,
                active_indices=indices, control_steps=getattr(self, 'requested_control_steps', None))
        elif self.execution_timing != 'model_authored_v1':
            compact, timing = compact_path(raw.detach().cpu().numpy(), dt, *rates,
                active_indices=indices, max_steps=MAX_CONTROL_STEPS)
        timing.update(limits=evidence, policy=self.execution_timing)
        planning['timing'] = timing
        self.event('trajectory_timing', **timing)
        if compact is None:
            planning.update(success=False, rejection='execution_rate_timing_infeasible')
            return None, planning
        # Validate the actual float32 commands, not just an intermediate float64 curve.
        compact = compact.astype(np.float32)
        if self.execution_timing != 'model_authored_v1':
            _, final_timing = compact_path(compact, dt, *rates, active_indices=indices,
                                          max_steps=len(compact)-1)
            if not final_timing['within_joint_limits']:
                planning.update(success=False, rejection='float32_execution_rate_limit')
                return None, planning
            timing['limit_ratios'] = final_timing['limit_ratios']
        dense = self.b.torch.as_tensor(dense_joint_samples(compact, max_joint_increment=.005),
                                      dtype=self.b.torch.float32)
        collision = self.planner.check_collisions(dense, initial_joint_pos=initial,
            skip_obstacle_update=True,
            attached_obj={self.b.robot.eef_link_names[self.arm]: self.link} if attached else None,
            attached_obj_scale={self.b.robot.eef_link_names[self.arm]: 1.0} if attached else None)
        reason = ('fast_resampled_collision' if bool(collision.any()) else
                  'fast_body_envelope' if not self.body_clearance(dense) else
                  'fast_nonfinger_contact' if contact is not None and not attached
                      and not self.target_clearance(dense) else None)
        if reason is not None:
            planning.update(success=False, rejection=reason)
            return None, planning
        source_artifact = self.last_trajectory_validation['artifact']
        artifact = 'fast_trajectory_' + str(time.time_ns()) + '.npz'
        np.savez(self.b.output / artifact, position=compact, source_position=raw.detach().cpu().numpy(), control_dt=dt,
            joint_names=np.array(self.planner.robot_joint_names),
            source_trajectory=source_artifact,
            goal_position_m=pose['position_m'], goal_quaternion_xyzw=pose['quaternion_xyzw'])
        planning.update(trajectory=artifact, source_trajectory=source_artifact,
            control_samples=len(compact), control_dt=dt, timing_policy=self.execution_timing,
            dense_validation_samples=len(dense), max_validation_joint_increment_rad=.005)
        self.event('fast_trajectory_validated', **planning)
        return self.b.torch.as_tensor(compact), planning

    def time_support_departure(self, waypoints, rates, dt):
        """Use the same model limits before certifying shallow support departure."""
        if self.execution_timing == 'model_authored_v1':
            q, timing = authored_path(waypoints, dt,
                control_steps=getattr(self, 'requested_control_steps', None))
            self.event('trajectory_timing', **timing)
            return q, timing, dense_joint_samples(q, max_joint_increment=.005)
        if self.execution_timing != 'model_estimated_v1':
            return super().time_support_departure(waypoints, rates, dt)
        _, full_rates, evidence = self.execution_rates()
        order = [evidence['joint_names'].index(name) for name in self.mg.kinematics.joint_names]
        rates = [values[order] for values in full_rates]
        # Preserve the existing geometric spline, then certify its actual commands
        # and dense interpolants using the same timing policy as other Pose moves.
        raw, _, _ = super().time_support_departure(waypoints, rates, dt)
        q, timing = estimated_path(raw, dt, *rates,
            control_steps=getattr(self, 'requested_control_steps', None))
        timing['limits'] = evidence
        self.event('trajectory_timing', **timing)
        if q is None:
            raise ValueError('Support departure could not satisfy robot model rate limits')
        return q, timing, dense_joint_samples(q, max_joint_increment=.005)

    def validate_execution_join(self, paths):
        if self.execution_timing == 'model_authored_v1':
            arrays = [q.detach().cpu().numpy() for _, q in paths]
            error = max((float(np.abs(a[-1]-b[0]).max())
                         for a, b in zip(arrays, arrays[1:])), default=0.)
            valid = all(np.isfinite(q).all() for q in arrays) and error <= 1e-4
            self.event('execution_join_validated', valid=valid, join_error_rad=error,
                       joint_limit_checks=False)
            return valid
        if self.execution_timing not in ('explicit_fast_v1', 'model_estimated_v1'):
            return True
        arrays = [q.detach().cpu().numpy() for _, q in paths]
        indices, rates, evidence = self.execution_rates()
        error = max((float(np.abs(a[-1, indices]-b[0, indices]).max())
                     for a, b in zip(arrays, arrays[1:])), default=0.)
        joined = np.concatenate([arrays[0][:1], *[q[1:] for q in arrays]])
        _, timing = compact_path(joined, float(self.b.og.sim.get_sim_step_dt()), *rates,
            active_indices=indices, max_steps=len(joined)-1)
        valid = error <= 1e-4 and timing['within_joint_limits']
        self.event('execution_join_validated', valid=valid, join_error_rad=error,
                   timing=timing, limits=evidence)
        return valid

    def endpoint(self, pose):
        # Compare in the measured CALL-START base frame, including physical base
        # drift. No new observation, RGB inference or per-tick error measurement.
        world = self.b.robot.eef_links[self.arm].get_position_orientation()
        actual = self.b._pose(self.b._relative(world, self.reference))
        position = np.asarray(actual['position_m']) - pose['position_m']
        quat = np.asarray(actual['quaternion_xyzw'])
        quat = quat / np.linalg.norm(quat)
        angle = 2 * math.acos(float(np.clip(abs(quat @ np.asarray(pose['quaternion_xyzw'])), 0., 1.)))
        error = float(np.linalg.norm(position))
        return {'reached': error <= POSITION_TOLERANCE_M and angle <= ORIENTATION_TOLERANCE_RAD,
                'actual': actual, 'position_error_m': error, 'orientation_error_rad': angle,
                'position_tolerance_m': POSITION_TOLERANCE_M,
                'orientation_tolerance_rad': ORIENTATION_TOLERANCE_RAD}

    def verify(self, pose, gripper):
        result = self.endpoint(pose)
        checks = {}
        for arm, (obj, link) in self.retained.items():
            if arm == self.arm and gripper == 'open':
                continue
            checks[arm] = self.b._retention(arm, obj, link)
        if gripper == 'close':
            checks[self.arm] = self.b._retention(self.arm, self.contact_obj, self.contact_link)
        released = gripper != 'open' or self.b._held(self.arm) is None
        verified = result['reached'] and released and all(row['retained'] for row in checks.values())
        return dict(result, verified=verified, retention=checks, release_verified=released if gripper == 'open' else None,
                    verification_scope='EEF goal and native gripper postcondition; not placement or task success',
                    reason=None if verified else 'eef_goal_not_reached' if not result['reached'] else
                        'release_failed' if not released else 'native_retention_failed')
