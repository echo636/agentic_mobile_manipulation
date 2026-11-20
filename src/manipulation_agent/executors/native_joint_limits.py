"""Runtime-only R1Pro position-limit policy for pinned OmniGibson 3.9.2.

Author USD limits before physics creation; verify and unclamp after initialization.
Assets, upstream sources, joint state, gains and rate/effort limits are untouched.
OmniGibson's finite bounds for normalizing unlimited joints are compatibility
values, not actuator limits; native controllers receive explicit infinities.
"""
import os

from ..posture_goals import POSTURE_JOINT_NAMES


def enabled():
    from ..model_pose_protocol import enabled as pose_enabled
    return pose_enabled() and os.environ.get('MAS_MANIP_TIMING', 'model_authored_v1') == 'model_authored_v1'


def _native_api():
    # Keep simulator dependencies inside this adapter and out of CPU imports.
    from omnigibson.controllers import ControlType, ControllerView, JointController
    from omnigibson.utils.backend_utils import _compute_backend
    return ControlType, ControllerView, JointController, _compute_backend


def author_unlimited_positions(robot, sim, prims):
    """Author the in-memory stage only, while stopped and before first play.

    Setting tensor limits after creation cannot change PhysX's LIMITED axis to
    FREE. USD's default infinite range creates a FREE revolute axis instead.
    """
    if not sim.is_stopped() or robot.initialized:
        raise RuntimeError('Unlimited native joints must be authored before physics creation')
    selected = {}
    for prim in prims:
        name = prim.GetName()
        if name not in POSTURE_JOINT_NAMES:
            continue
        if name in selected or prim.GetTypeName() != 'PhysicsRevoluteJoint':
            raise RuntimeError('Expected exactly one revolute joint per torso/arm DOF: ' + name)
        selected[name] = prim
    if set(selected) != set(POSTURE_JOINT_NAMES):
        raise RuntimeError('Missing native torso/arm joint prims before physics creation')
    attrs = [(name, selected[name].GetAttribute('physics:' + bound + 'Limit'), value)
             for name in POSTURE_JOINT_NAMES
             for bound, value in [('lower', -float('inf')), ('upper', float('inf'))]]
    original = [(name, attr, attr.Get()) for name, attr, _ in attrs]
    try:
        with sim.editing_usd():
            for name, attr, value in attrs:
                if not attr.Set(value) or attr.Get() != value:
                    raise RuntimeError('USD rejected unlimited position range: ' + name)
    except Exception:
        with sim.editing_usd():
            for _, attr, value in original:
                attr.Set(value)
        raise
    import math
    robot._mas_original_position_limits_rad = {
        name: [math.radians(value) for n, _, value in original if n == name]
        for name in POSTURE_JOINT_NAMES}


def create_unbounded_environment(og, config):
    """Use the supported import callback, including embedded scene robots."""
    from omnigibson.robots import Robot

    class UnboundedEnvironment(og.Environment):
        def load(self):
            sim = og.sim
            # Start the readback extension before the first timeline play so
            # its native handles participate in the articulation lifecycle.
            from isaacsim.core.utils.extensions import enable_extension
            enable_extension('omni.isaac.dynamic_control')
            callback_name = 'mas_author_unlimited_torso_arms'

            def on_import(obj):
                if isinstance(obj, Robot):
                    # SimulationApp starts inside Environment.__init__; pxr is
                    # available only now, after that launcher has run.
                    from pxr import Usd
                    if obj.model != 'r1pro':
                        raise RuntimeError('Native unlimited-position policy requires R1Pro')
                    author_unlimited_positions(obj, sim, Usd.PrimRange(obj._prim))

            sim.add_callback_on_add_obj(callback_name, on_import)
            try:
                super().load()
            finally:
                sim.remove_callback_on_add_obj(callback_name)

    return UnboundedEnvironment(configs=config)


def _native_limit_flags(robot):
    """Read the actual PhysX motion flags, not stale tensor limit parameters.

    Dynamic Control's has_limits/lower/upper are explicitly read-only. Its
    getter can verify FREE axes, but its property setter cannot disable them.
    """
    from isaacsim.core.utils.extensions import enable_extension
    enable_extension('omni.isaac.dynamic_control')
    from omni.isaac.dynamic_control import _dynamic_control
    dc = _dynamic_control.acquire_dynamic_control_interface()
    art = dc.get_articulation(robot.articulation_root_path)
    if art == _dynamic_control.INVALID_HANDLE:
        raise RuntimeError('Cannot read native articulation motion flags')
    flags = {}
    for name in robot.joints:
        dof = dc.find_articulation_dof(art, name)
        if dof == _dynamic_control.INVALID_HANDLE:
            raise RuntimeError('Cannot read native DOF motion flag: ' + name)
        props = dc.get_dof_properties(dof)
        if props is None:
            raise RuntimeError('Missing native DOF properties: ' + name)
        flags[name] = bool(props.has_limits)
    return flags


def disable_native_joint_position_limits(robot):
    """Verify the 18 FREE native axes and remove controller position clamps.

    Controller bounds and masks are group-wide in this OG version. Refuse a
    shared group rather than changing another robot. No env.step, state setter,
    controller reload or goal update is performed.
    """
    import torch as th
    ControlType, ControllerView, JointController, cb = _native_api()
    names = list(robot.joints)
    if not set(POSTURE_JOINT_NAMES) <= set(names):
        raise RuntimeError('Native position policy requires the R1Pro torso and both arms')
    indices = [names.index(name) for name in POSTURE_JOINT_NAMES]
    original = getattr(robot, '_mas_original_position_limits_rad', None)
    if original is None or set(original) != set(POSTURE_JOINT_NAMES):
        raise RuntimeError('Native unlimited positions were not authored before physics creation')
    flags = _native_limit_flags(robot)
    if any(flags[name] for name in POSTURE_JOINT_NAMES):
        raise RuntimeError('Native physics still reports limited torso/arm axes')

    cache_names = ('joint_lower_limits', 'joint_upper_limits', 'control_limits')
    caches = {name: getattr(robot, name) for name in cache_names}
    controls = caches['control_limits']
    updated_controls = dict(controls)
    low, high = (x.clone() for x in controls['position'])
    low[indices], high[indices] = -float('inf'), float('inf')
    has_limit = controls['has_limit'].clone()
    has_limit[indices] = False
    updated_controls.update(position=(low, high), has_limit=has_limit)

    changes, covered = [], []
    for name in ('trunk', 'arm_left', 'arm_right'):
        group, member = robot.controllers[name]
        controller = ControllerView._controller_groups[group]
        if (not isinstance(controller, JointController)
                or controller.control_type != ControlType.POSITION
                or controller._motor_type != 'position'
                or controller._use_delta_commands
                or controller.command_input_limits is not None
                or controller.command_output_limits is not None):
            raise RuntimeError(f'{name}: expected an unscaled absolute native position controller')
        active = (cb.to_torch(controller._unregistered_controllers) == 0).nonzero().flatten().tolist()
        if active != [member] or controller._articulation_root_paths[member] != robot.articulation_root_path:
            raise RuntimeError(f'{name}: position policy cannot modify a shared controller group')
        dofs = cb.to_torch(controller.dof_idx).long().tolist()
        expected = (robot.trunk_control_idx if name == 'trunk'
                    else robot.arm_control_idx[name.removeprefix('arm_')]).tolist()
        if dofs != expected:
            raise RuntimeError(f'{name}: native controller DOFs do not match the robot')
        covered.extend(dofs)
        bounds = dict(controller._control_limits)
        clow, chigh = (cb.to_torch(x).clone() for x in bounds[ControlType.POSITION])
        clow[dofs], chigh[dofs] = -float('inf'), float('inf')
        bounds[ControlType.POSITION] = [cb.from_torch(clow), cb.from_torch(chigh)]
        mask = cb.to_torch(controller._dof_has_limits).clone()
        mask[dofs] = False
        new = dict(_control_limits=bounds, _dof_has_limits=cb.from_torch(mask),
                   _clip_lo=cb.from_torch(clow[dofs]), _clip_hi=cb.from_torch(chigh[dofs]))
        old = {attr: getattr(controller, attr) for attr in new}
        config = robot._controller_config[name]
        changes.append((name, controller, old, new, config, config['control_limits']))
    if sorted(covered) != sorted(indices):
        raise RuntimeError('Native position policy must cover exactly the 18 torso/arm joints')

    try:
        # Joint getters deliberately map unlimited ranges to finite normalization
        # placeholders. Recompute those caches, then give controllers real bounds.
        for name in cache_names:
            robot.__dict__.pop(name, None)
        robot.joint_lower_limits
        robot.joint_upper_limits
        robot.__dict__['control_limits'] = updated_controls
        for name, controller, old, new, config, old_config in changes:
            for attr, value in new.items():
                setattr(controller, attr, value)
            config['control_limits'] = updated_controls
    except Exception as exc:
        robot.__dict__.update(caches)
        for name, controller, old, new, config, old_config in changes:
            for attr, value in old.items():
                setattr(controller, attr, value)
            config['control_limits'] = old_config
        raise RuntimeError(f'Cannot remove native controller clamps: {exc}; '
                           'controller rollback restored; startup aborted with FREE physics axes') from exc

    import math
    return dict(policy='unbounded_torso_arms_v1', status='disabled',
        joints=list(POSTURE_JOINT_NAMES), dof_indices=indices,
        original_position_limits_rad=[[x if math.isfinite(x) else None for x in original[name]]
                                     for name in POSTURE_JOINT_NAMES],
        physical_limit_readback='native_dof_has_limits_false',
        native_dof_has_limits=flags, physical_configuration='USD unlimited range before articulation creation',
        native_controller_position_clamp=False, controllers=[row[0] for row in changes],
        normalization_bounds='OmniGibson finite placeholders; not actuator limits',
        base_and_gripper_limits='unchanged', native_velocity_and_effort_limits='unchanged',
        collision_geometry='unchanged', joint_state='unchanged')
