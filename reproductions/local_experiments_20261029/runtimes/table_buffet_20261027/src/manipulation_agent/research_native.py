"""Explicit admission and restoration for generated native research instances.

Research instances use the original BDDL predicates, but have no public-split
identity or human reference metrics. This module does not expose private state
to the model and imports no simulator code until state restoration is requested.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

PROTOCOL = 'original_native_predicates_research_instance_v1'


def load_instance(path, *, task, instance):
    path = Path(path).resolve()
    raw = path.read_bytes()
    spec = json.loads(raw)
    if spec.get('schema_version') != 1 or spec.get('scoring_protocol') != PROTOCOL:
        raise ValueError('Unsupported native research instance protocol')
    if spec.get('task') != task or spec.get('instance') != instance:
        raise ValueError('Research instance task or identity does not match the request')
    if not isinstance(spec.get('scene_model'), str) or not spec['scene_model']:
        raise ValueError('Research instance requires a scene model')
    for key, size in (('robot_start_position', 3), ('robot_start_orientation', 4)):
        values = spec.get(key)
        if not isinstance(values, list) or len(values) != size or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
            raise ValueError('Invalid research instance ' + key)
    if abs(sum(v*v for v in spec['robot_start_orientation']) - 1) > .01:
        raise ValueError('Research instance robot orientation must be a unit quaternion')
    if spec.get('tensor_pipeline_device') not in (None, 'cpu', 'cuda:0'):
        raise ValueError('Unsupported research tensor pipeline device')
    if spec.get('physics_solver_type') not in (None, 'PGS', 'TGS'):
        raise ValueError('Unsupported research physics solver')
    camera_barrier = spec.get('camera_initialization_barrier')
    if camera_barrier not in (None, 'native_state_checked_post_rgb_startup_v1'):
        raise ValueError('Unsupported research camera initialization barrier')
    if camera_barrier and spec.get('physics_edit_barrier') != 'native_particle_edit_articulation_barrier_v2':
        raise ValueError('Camera initialization requires the checked native state barrier')
    rooms = spec.get('load_room_instances')
    if rooms is not None and (not isinstance(rooms, list) or
                             not all(isinstance(room, str) and room for room in rooms)):
        raise ValueError('Invalid research room selection')
    for key in ('scene_template', 'task_state', 'bddl_definition'):
        value = Path(spec[key])
        value = (path.parent / value).resolve() if not value.is_absolute() else value.resolve()
        digest = hashlib.sha256(value.read_bytes()).hexdigest()
        if digest != spec.get(key + '_sha256'):
            raise ValueError('Research input hash mismatch: ' + key)
        spec[key] = str(value)
    topology = spec.get('cloth_topology')
    if topology:
        from .cloth_topology import PROTOCOL as TOPOLOGY_PROTOCOL
        if topology.get('protocol') != TOPOLOGY_PROTOCOL:
            raise ValueError('Unsupported native cloth topology protocol')
        directory = Path(topology['directory'])
        directory = (path.parent / directory).resolve() if not directory.is_absolute() else directory.resolve()
        manifest = directory / 'manifest.json'
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != topology['manifest_sha256']:
            raise ValueError('Cloth topology manifest hash mismatch')
        for filename, sha in json.loads(manifest.read_text())['files'].items():
            if Path(filename).name != filename or hashlib.sha256((directory / filename).read_bytes()).hexdigest() != sha:
                raise ValueError('Cloth topology input hash mismatch')
        topology['directory'] = str(directory)
    definition = Path(spec['bddl_definition']).read_text()
    if f'(problem {task}-0)' not in definition or '(:goal' not in definition:
        raise ValueError('Research definition does not identify the requested task')
    spec.update(spec_path=str(path), spec_sha256=hashlib.sha256(raw).hexdigest(),
                split='research_development', human_reference_metrics_available=False)
    return spec


def restore_state(env, state_path):
    """Restore the recorded native entities using the official evaluator sequence."""
    import omnigibson as og
    from omnigibson.utils.bddl_utils import is_system_bddl_inst
    from omnigibson.utils.python_utils import recursively_convert_to_torch
    from contextlib import nullcontext

    state = recursively_convert_to_torch(json.loads(Path(state_path).read_text()))
    transaction = getattr(og.sim, 'state_restore_transaction', nullcontext)
    with transaction():
        for key, value in state.items():
            if key == 'robot_poses':
                poses = {name.lower(): rows for name, rows in value.items()}
                rows = poses.get('robot') or poses.get(env.robots[0].model)
                if not rows:
                    raise ValueError('Research state has no applicable robot pose')
                env.robots[0].set_position_orientation(rows[0]['position'], rows[0]['orientation'])
                env.scene.write_task_metadata(key=key, data=value)
            else:
                entity = env.task.object_scope.get(key)
                if entity is None:
                    raise ValueError('Research state references an unbound entity: ' + key)
                entity.load_state(value, serialized=False)
    og.sim.update_handles()
    for _ in range(25):
        og.sim.step_physics()
        for key, entity in env.task.object_scope.items():
            if entity is not None and not is_system_bddl_inst(key):
                entity.keep_still()
    env.scene.update_initial_file()
    env.scene.reset()


def rebase_holonomic_robot(data, spec):
    """Recenter a saved R1 virtual base without changing its world start pose."""
    import math
    from copy import deepcopy
    import numpy as np

    def multiply(a, b):
        av, bv = np.asarray(a[:3]), np.asarray(b[:3])
        return np.r_[a[3]*bv + b[3]*av + np.cross(av, bv), a[3]*b[3] - np.dot(av, bv)]
    records = []
    for name, entry in data['objects_info']['init_info'].items():
        if (entry.get('class_name'), entry.get('args', {}).get('model')) != ('Robot', 'r1pro'):
            continue
        state = data['state']['registry']['object_registry'][name]
        q = np.asarray(state['joint_pos'], dtype=float)
        root = state['root_link']
        root_q = np.asarray(root['ori'], dtype=float)
        rotation = np.empty((3, 3))
        for axis in range(3):
            vector = np.eye(3)[axis]
            rotation[:, axis] = multiply(multiply(root_q, np.r_[vector, 0]), np.r_[-root_q[:3], root_q[3]])[:3]
        world_pos = np.asarray(root['pos']) + rotation @ q[:3]
        world_q = root_q.copy()
        for axis, angle in enumerate(q[3:6]):
            vector = np.zeros(3); vector[axis] = math.sin(angle/2)
            world_q = multiply(world_q, np.r_[vector, math.cos(angle/2)])
        target_pos = np.asarray(spec['robot_start_position'])
        target_q = np.asarray(spec['robot_start_orientation'])
        position_error = float(np.max(np.abs(world_pos-target_pos)))
        rotation_error = float(1-abs(np.dot(world_q, target_q)))
        if position_error > .001 or rotation_error > 1e-5:
            raise ValueError('Saved virtual base does not match the requested world start pose')
        before = {'root': deepcopy(root), 'base_joints': q[:6].tolist()}
        root.update(pos=target_pos.tolist(), ori=target_q.tolist(), lin_vel=[0.,0.,0.], ang_vel=[0.,0.,0.])
        state['joint_pos'][:6] = [0.] * 6
        state['joint_vel'][:6] = [0.] * 6
        state['controller_groups'] = {}
        records.append({'object': name, 'migration': 'holonomic_base_frame_at_start_pose_v1',
                        'before': before, 'after_root': deepcopy(root),
                        'world_position_error_m': position_error, 'quaternion_dot_error': rotation_error,
                        'non_base_joint_positions_preserved': True,
                        'initial_base_velocity_policy': 'zero_as_in_backend_keep_still'})
    if len(records) != 1:
        raise ValueError('Base frame normalization requires exactly one saved R1Pro')
    return records
