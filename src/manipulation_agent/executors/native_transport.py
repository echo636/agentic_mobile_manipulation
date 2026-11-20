"""Opt-in feedback base transport through the pinned native velocity controller.

No robot/object pose setters. Planning and swept-footprint checks remain in the
existing navigation implementation; measured base feedback drives its follower.
"""
import json
import math
import os

from ..contracts import SkillError
from ..records import now

POLICY = 'native_joint_v1'
LINEAR_SPEED = .25
ANGULAR_SPEED = .4


def enabled():
    return os.environ.get('MAS_NATIVE_TRANSPORT') == POLICY


def local_velocity(actual, target, dt):
    """World next-pose increment -> bounded robot-local x/y/yaw velocity."""
    values = (*actual, *target, dt)
    if not all(math.isfinite(float(v)) for v in values) or dt <= 0:
        raise ValueError('Finite poses and positive timestep required')
    dx, dy = (target[0]-actual[0])/dt, (target[1]-actual[1])/dt
    scale = min(1., LINEAR_SPEED/max(math.hypot(dx, dy), 1e-12))
    c, s = math.cos(actual[2]), math.sin(actual[2])
    yaw = math.atan2(math.sin(target[2]-actual[2]), math.cos(target[2]-actual[2]))/dt
    return ((c*dx+s*dy)*scale, (-s*dx+c*dy)*scale,
            max(-ANGULAR_SPEED, min(ANGULAR_SPEED, yaw)))


def run(b, target):
    """Keep grip and joint commands, execute checked base route transactionally."""
    from .oracle_open_loop import OpenLoopPickTrial
    if b._base_target is not None:
        raise SkillError('native_transport_precondition', 'Kinematic base target must be absent')
    if b._carry_contents or getattr(b, '_stabilized_containers', {}):
        raise SkillError('native_transport_precondition', 'Ideal payload/container anchors are unsupported')
    retained = {}
    for arm in b.robot.arm_names:
        held = b._held(arm)
        if held is not None:
            obj = b._resolve_object(held)
            state = b._grasp_state(arm)
            link = b._resolve_link(obj, state['target_link'])
            retained[arm] = (obj, link)
    if not retained:
        raise SkillError('native_transport_precondition', 'Native carrying branch requires a held object')
    def event(kind, **fields):
        # Terminal trial fields already carry request_id; merge before adding
        # event metadata so logging cannot mask a navigation rejection.
        payload = {'request_id': getattr(b, '_active_request_id', None), **fields,
                   'at': now(), 'kind': kind}
        with (b.output/'native_transport.jsonl').open('a') as stream:
            stream.write(json.dumps(payload, allow_nan=False)+'\n')
    b._native_transport_active = True
    try:
        with OpenLoopPickTrial(b, event, record_surround=True) as trial:
            trial.manifest.update(mode='feedback_native_transport_rollback',
                execution='native_holonomic_joint_velocity', base_feedback=True,
                arm_replanning_during_transport=False, object_pose_projection=False,
                robot_pose_projection=False, posture_projection=False,
                speed_m_s=LINEAR_SPEED, yaw_rate_rad_s=ANGULAR_SPEED)
            trial.video.mark('navigate_to', {'target': target}, getattr(b, '_active_request_id', None))
            trial.video.execution_caption = 'Native base feedback control; assisted grip; no pose projection.'
            trial.video.manifest['execution_caption'] = trial.video.execution_caption
            trial.enter_phase('native_transport')
            details = b.execute_visual('navigate_to', target, max_steps=700)
            checks = {arm:b._retention(arm, obj, link) for arm,(obj,link) in retained.items()}
            if not all(row['retained'] for row in checks.values()):
                raise SkillError('native_transport_retention_failed', 'Native grip lost during transport', changed=True)
            trial.accepted = True
        return dict(details, reached=True, verified=True, retention=checks,
            execution_phase='native_transport', transport_policy=POLICY,
            transport_execution='native_joint_controller_and_assisted_constraint',
            object_pose_projection=False, robot_pose_projection=False, posture_projection=False,
            manipulation_performed=False, execution=dict(trial.manifest, trial=str(trial.root)))
    finally:
        b._native_transport_active = False
        b._base_target = None


def placement_state(b, object_id, container_id):
    """Native relation + disengagement evidence, no physics, render or setters."""
    from omnigibson.object_states import Inside
    obj, container = b._resolve_object(object_id), b._resolve_object(container_id)
    if obj is container:
        raise SkillError('invalid_arguments', 'Select distinct object and container')
    held = {arm:b._grasp_state(arm) for arm in b.robot.arm_names}
    released = all(row.get('object_id') != object_id for row in held.values())
    available = Inside in obj.states
    try:
        relation = bool(obj.states[Inside].get_value(container)) if available else None
    except (ValueError, RuntimeError, AssertionError) as exc:
        raise SkillError('placement_query_failed', str(exc)[:500]) from exc
    return dict(object_id=object_id, container_id=container_id,
        relation='Inside', relation_available=available, relation_satisfied=relation,
        released=released, native_grasps=held,
        object_pose_world=b._pose(obj.get_position_orientation()),
        container_pose_world=b._pose(container.get_position_orientation()),
        env_step=b.steps, source='native_Inside_get_value_and_native_grasp_state',
        physics_steps=0, whole_task_success_not_evaluated=True)
