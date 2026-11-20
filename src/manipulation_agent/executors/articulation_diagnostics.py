"""Private joint/contact evidence captured before a failed action rolls back."""
import json


def articulation_sample(backend, obj, phase):
    from omnigibson.object_states import Open
    from omnigibson.object_states.open_state import _compute_joint_threshold
    from omnigibson.utils.usd_utils import RigidContactAPI
    both_sides, joints, directions = obj.states[Open].relevant_joints_info
    readings = []
    for joint, direction in zip(joints, directions):
        position, velocity, _ = joint.get_state()
        readings.append({
            'joint': str(joint.prim_path), 'position': float(position.reshape(-1)[0]),
            'velocity': float(velocity.reshape(-1)[0]), 'direction': direction,
            'threshold_open_closed': [float(x) for x in _compute_joint_threshold(joint, direction)],
        })
    contacts = [{'body0': str(a), 'body1': str(b)} for a, b in sorted(
        RigidContactAPI.get_contact_pairs(obj.scene.idx, query_set=[obj],
                                         with_set=None, current_only=True))]
    return {'phase': phase, 'env_step': backend.steps, 'both_sides': both_sides,
            'official_open': bool(obj.states[Open].get_value()),
            'uncached_open': bool(obj.states[Open]._get_value()),
            'joints': readings, 'contacts': contacts}


def record_articulation(backend, obj, primitive, samples, **result):
    with (backend.output / 'articulation_diagnostics.jsonl').open('a') as stream:
        stream.write(json.dumps({'request_id': getattr(backend, '_active_request_id', None),
            'audience': 'executor_private', 'primitive': primitive, 'target': obj.name,
            'samples': samples, **result}) + '\n')
