"""Selectable adapter for the existing sampler, authored by the harness developer."""
import numpy as np
if __package__:
    from ..grasp_geometry import bottle_grasps
else:
    from manip_reference import bottle_grasps


def quaternion_xyzw(rotation):
    # Eigenvector form remains well-conditioned for half turns.
    r = np.asarray(rotation)
    k = np.array([
        [r[0,0]-r[1,1]-r[2,2], r[1,0]+r[0,1], r[2,0]+r[0,2], r[2,1]-r[1,2]],
        [r[1,0]+r[0,1], r[1,1]-r[0,0]-r[2,2], r[2,1]+r[1,2], r[0,2]-r[2,0]],
        [r[2,0]+r[0,2], r[2,1]+r[1,2], r[2,2]-r[0,0]-r[1,1], r[1,0]-r[0,1]],
        [r[2,1]-r[1,2], r[0,2]-r[2,0], r[1,0]-r[0,1], np.trace(r)]]) / 3
    _, vectors = np.linalg.eigh(k)
    q = vectors[:, -1]
    return (q if q[3] >= 0 else -q).tolist()


def rotation_xyzw(q):
    x, y, z, w = np.asarray(q) / np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def generate(task, oracle, parameters):
    if oracle['frame'] != 'robot_base':
        raise ValueError('Expected current robot_base geometry')
    obj = next(o for o in oracle['objects'] if o['id'] == parameters['object_id'])
    arm = parameters['arm']
    hand = oracle['robot']['arms'][arm]
    if hand['held_object'] is not None:
        raise ValueError('Select an empty arm for a new grasp')
    geometry = hand['grasp_geometry']
    if hand['gripper_command'] <= 0 or geometry['aperture_m'] <= .006:
        raise ValueError('Open this hand and query geometry again before sampling grasps')
    mesh = obj['collision_mesh']
    if mesh['frame'] != 'robot_base':
        raise ValueError('Mesh frame differs from input robot_base')
    # Preserve the historical sampler's world-axis mesh sections and ranking.
    # The public input/output still uses the observed mobile robot_base frame.
    base = oracle['robot']['base_pose_world']
    origin = np.asarray(base['position_m'])
    rotation = rotation_xyzw(base['quaternion_xyzw'])
    vertices_world = np.asarray(mesh['vertices_m']) @ rotation.T + origin
    hand_world = np.asarray(hand['eef_pose']['position_m']) @ rotation.T + origin
    candidates = bottle_grasps(vertices_world, hand_world,
        geometry['contact_offset_eef_m'], geometry['aperture_m'],
        clearance=parameters.get('clearance_m', .10), faces=mesh['faces'])
    if not candidates:
        raise ValueError('No aperture-compatible upright-object candidates; inspect shape or choose another program')
    target = {'object_id': obj['id'], 'link_name': parameters.get('link_name', 'base_link')}
    if target['link_name'] not in {link['name'] for link in obj['links']}:
        raise ValueError('Select the actual physical contact link')
    def phase(name, p, q, gripper, contact):
        row = dict(name=name, arm=arm, frame='robot_base',
            goal=dict(position_m=p.tolist(), quaternion_xyzw=q),
            gripper=gripper, contact_target=contact)
        estimate = parameters.get('control_steps', {}).get(name)
        if estimate is not None:
            if type(estimate) is not int or estimate < 1:
                raise ValueError('Each phase control_steps estimate must be a positive integer')
            row['control_steps'] = estimate
        return row
    result = []
    for index, c in enumerate(candidates):
        q = quaternion_xyzw(rotation.T @ c.rotation)
        result.append(dict(name='grasp_'+str(index), score=c.score,
            phases=[phase('pregrasp', (c.pregrasp-origin) @ rotation, q, 'hold', None),
                    phase('contact_close', (c.eef-origin) @ rotation, q, 'close', target)]))
    return {'candidates': result, 'prepare_gripper': None,
            'planning_profile': 'geometry_pick_v1'}
