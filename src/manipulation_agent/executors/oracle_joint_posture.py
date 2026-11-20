"""Explicit torso + BOTH arms -> cuRobo joint-space plan -> native open loop.

Base and finger joints are locked at their measured state. No stored simulator
snapshot, historical trajectory or hand/object transform is replayed.
"""
from copy import deepcopy
import time
import traceback

from ..contracts import SkillError
from ..grasp_geometry import resample_positions
from ..posture_goals import POSTURE_JOINT_NAMES, POSTURE_TOLERANCE_RAD
from .oracle_open_loop import OpenLoopPickTrial
from .oracle_pose import ModelPoseManip


class ModelJointPosture(ModelPoseManip):
    planner_cache_attribute = '_model_posture_planners'
    planner_config_prefix = 'curobo_model_posture'

    def preflight(self, contact_target, gripper):
        super().preflight(contact_target, gripper)
        if len({id(obj) for obj, _ in self.retained.values()}) != len(self.retained):
            raise SkillError('held_geometry_unsupported',
                             'Bimanual retention of one object needs a constrained dual-arm planner')

    def execute_phases(self, phases, program=None):
        from ..manip_programs import checked_phases
        phases = checked_phases({'phases': phases})
        if any(p['arm'] != 'both' for p in phases):
            raise SkillError('invalid_arguments', 'Use whole-body joint goals throughout a posture Manip')
        self.arm, self.trial = 'both', None
        start_step = self.b.steps
        try:
            self.preflight(None, 'hold')
            self.b._trace_arm = None
            grippers = deepcopy(self.b._gripper_commands)
            self.event('model_goals', engine='model_program_v1' if program else 'model_pose_v1',
                goal_source='selected_joint_posture' if program else 'explicit_joint_posture',
                submitted_phases=phases, normalized_phases=phases, program=program,
                goal_count=len(phases), automatic_goal_generation=False)
            with OpenLoopPickTrial(self.b, self.event) as trial:
                self.trial = trial
                trial.joint_limit_checks = self.execution_timing != 'model_authored_v1'
                trial.manifest.update(goal_source='explicit_joint_posture', arm='both',
                    timing_policy=self.execution_timing, joint_limit_checks=trial.joint_limit_checks,
                    native_mechanical_limits='unchanged',
                    submitted_phases=phases, program=program, frame='robot_base',
                    base_motion=False, gripper_changes=False, automatic_goal_generation=False,
                    planning_scope='torso and both arms, one joint-space plan per measured phase')
                self.set_phase('planner_prepare')
                self.get_planner('both')
                outcomes = []
                for phase in phases:
                    self.b.deadline.check(changed=bool(trial.steps))
                    self.preflight(None, 'hold')
                    self.b._trace_arm = None
                    self.set_phase(phase['name'] + '/plan')
                    self.event('pose_phase_goal', phase_name=phase['name'],
                        operation_scope='internal_pose_template', start_state_source='measured',
                        pose_operation=dict(mode='pose', **{k: deepcopy(v) for k, v in phase.items() if k != 'name'}))
                    self.requested_control_steps = phase.get('control_steps')
                    with trial.measure('curobo_plan_and_validation'):
                        q, planning = self.plan_goal(phase['goal'])
                    if q is None:
                        outcome = dict(name=phase['name'], reached=False, verified=False,
                            reason='joint_posture_planning_failed', planning=planning)
                    else:
                        self.set_phase(phase['name'] + '/execute')
                        trial.stream(q, 'both')
                        self.set_phase(phase['name'] + '/verify')
                        outcome = dict(self.verify_joint_goal(phase['goal'], grippers),
                                       name=phase['name'], planning=planning)
                    outcomes.append(outcome)
                    self.event('pose_phase_result', phase_name=phase['name'], outcome=outcome)
                    if outcome['verified'] is not True:
                        break
                trial.accepted = len(outcomes) == len(phases) and all(p['verified'] for p in outcomes)
                result = dict(arm='both', reached=all(p['reached'] for p in outcomes),
                    verified=trial.accepted, reason=None if trial.accepted else outcomes[-1]['reason'],
                    phases=outcomes, program=program, frame='robot_base',
                    goal_type='whole_upper_body_joint_pose')
                if not trial.accepted:
                    trial.manifest['postcondition_failure'] = result['reason']
            result['execution'] = dict(trial.manifest, trial=str(trial.root))
            return result
        except SkillError:
            raise
        except Exception as exc:
            self.event('exception', exception=type(exc).__name__, message=str(exc)[:1200],
                       traceback=traceback.format_exc())
            raise SkillError('joint_posture_failed', f'{self.phase}: {type(exc).__name__}: {str(exc)[:500]}',
                             changed=self.b.steps != start_step) from exc
        finally:
            self.b._trace_arm = self.b._trace_target = self.b._trace_waypoint = None

    def goal_vector(self, goal):
        robot, th = self.b.robot, self.b.torch
        names = list(robot.joints)
        requested = goal['joint_positions_rad']
        if set(requested) != set(POSTURE_JOINT_NAMES) or not set(requested) <= set(names):
            raise SkillError('posture_robot_mismatch', 'Recovery requires the R1Pro torso and both seven-joint arms')
        target = robot.get_joint_positions().clone()
        indices = [names.index(name) for name in POSTURE_JOINT_NAMES]
        values = th.tensor([requested[name] for name in POSTURE_JOINT_NAMES], device=target.device, dtype=target.dtype)
        if self.execution_timing != 'model_authored_v1' and bool(((values < robot.joint_lower_limits[indices]) | (values > robot.joint_upper_limits[indices])).any()):
            raise SkillError('joint_limit', 'The requested recovery pose exceeds native mechanical limits')
        target[indices] = values
        return target

    def plan_goal(self, goal, initial=None):
        from curobo.types.state import JointState
        from curobo.wrap.reacher.motion_gen import MotionGenPlanConfig
        b, planner, mg = self.b, self.planner, self.mg
        current = b.robot.get_joint_positions().clone()
        target = self.goal_vector(goal)
        active = list(mg.kinematics.joint_names)
        if set(active) != set(POSTURE_JOINT_NAMES):
            raise SkillError('posture_planner_mismatch', 'Joint recovery must plan exactly the torso and both arms')
        planner.update_obstacles()
        full_start = JointState.from_position(planner.tensor_args.to_device(current[None]), joint_names=planner.robot_joint_names)
        planner.update_locked_joints(full_start, self.emb)
        start = full_start.get_ordered_joint_state(active)
        end = JointState.from_position(planner.tensor_args.to_device(target[None]),
                                      joint_names=planner.robot_joint_names).get_ordered_joint_state(active)
        attached = dict(attached_obj=self.attachments or None,
                        attached_obj_scale={name: 1. for name in self.attachments} or None)
        info, started = [], time.perf_counter()
        try:
            # Attach separately so a failure on the second payload still cleans up the first.
            for eef, link in self.attachments.items():
                info.extend(planner._attach_objects_to_robot({eef: link}, {eef: 1.}, start, self.emb))
            result = mg.plan_single_js(start, end, MotionGenPlanConfig(
                enable_graph=False, enable_graph_attempt=2, num_graph_seeds=1,
                max_attempts=6, timeout=12., enable_finetune_trajopt=True))
        finally:
            planner._detach_objects_from_robot(info, self.emb)
        planning = dict(success=bool(result.success.flatten()[0]), status=str(result.status),
            seconds=time.perf_counter()-started, planner_implementation='MotionGen.plan_single_js',
            goal_type='whole_upper_body_joint_pose', goal_joint_positions_rad=deepcopy(goal['joint_positions_rad']),
            attached_eef_links=list(self.attachments), attached_object_scale=1.,
            base_motion=False, gripper_changes=False)
        self.event('plan_result', **planning)
        if not planning['success']:
            return None, planning
        # plan_single_js returns a single (T,D) result. get_paths() is the
        # separate batched-query API and may have no path_buffer_last_tstep.
        full = mg.get_full_js(result.get_interpolated_plan()).get_ordered_joint_state(planner.robot_joint_names)
        dt = float(b.og.sim.get_sim_step_dt())
        source_dt = float(result.interpolation_dt)
        raw = resample_positions(full.position.detach().cpu().numpy(), source_dt, dt)
        return self.finish_planned_path(raw, goal, planning, current, attached, None, source_dt)

    def verify_joint_goal(self, goal, grippers):
        target = self.goal_vector(goal)
        names = list(self.b.robot.joints)
        actual = self.b.robot.get_joint_positions()
        measured = {name: float(actual[names.index(name)]) for name in POSTURE_JOINT_NAMES}
        errors = {name: abs(measured[name]-float(target[names.index(name)])) for name in POSTURE_JOINT_NAMES}
        reached = max(errors.values()) <= POSTURE_TOLERANCE_RAD
        retention = {arm: self.b._retention(arm, obj, link) for arm, (obj, link) in self.retained.items()}
        unchanged = self.b._gripper_commands == grippers
        verified = reached and unchanged and all(row['retained'] for row in retention.values())
        return dict(reached=reached, verified=verified, actual={'joint_positions_rad': measured},
            max_joint_error_rad=max(errors.values()), joint_errors_rad=errors,
            joint_tolerance_rad=POSTURE_TOLERANCE_RAD, retention=retention, grippers_unchanged=unchanged,
            body_posture=self.b._body_posture_state(),
            verification_scope='torso and both arm joint targets, unchanged grip commands and native retention',
            reason=None if verified else 'joint_posture_not_reached' if not reached else
                'gripper_command_changed' if not unchanged else 'native_retention_failed')
