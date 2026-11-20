"""Curated direct placement, adapted from Astra's 309_18 mouth_targets.

Uses current meshes and the measured rigid hand/payload transform. It produces
EEF goals, never simulator actions. No historical coordinates or recovery arcs.
"""
import numpy as np


def rotation(q):
    x, y, z, w = np.asarray(q, dtype=float)/np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                     [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                     [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])


def quaternion(r):
    k = np.array([
        [r[0,0]-r[1,1]-r[2,2], r[1,0]+r[0,1], r[2,0]+r[0,2], r[2,1]-r[1,2]],
        [r[1,0]+r[0,1], r[1,1]-r[0,0]-r[2,2], r[2,1]+r[1,2], r[0,2]-r[2,0]],
        [r[2,0]+r[0,2], r[2,1]+r[1,2], r[2,2]-r[0,0]-r[1,1], r[1,0]-r[0,1]],
        [r[2,1]-r[1,2], r[0,2]-r[2,0], r[1,0]-r[0,1], np.trace(r)]])/3
    _, v = np.linalg.eigh(k)
    q = v[:, -1]
    return (q if q[3] >= 0 else -q).tolist()


def axis_rotation(axis, angle):
    x, y, z = np.asarray(axis, dtype=float)
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3)+np.sin(angle)*k+(1-np.cos(angle))*(k@k)


def section_radius(vertices, faces, height, center):
    distances = []
    for tri in vertices[faces]:
        hits = []
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if (a[2]-height)*(b[2]-height) < 0:
                hits.append((a+(b-a)*(height-a[2])/(b[2]-a[2]))[:2])
        if len(hits) == 2:
            a, b = hits
            d = b-a
            t = np.clip(np.dot(center-a, d)/max(np.dot(d, d), 1e-15), 0, 1)
            distances.append(np.linalg.norm(a+t*d-center))
    if not distances:
        raise ValueError('No opening mesh section; inspect container geometry')
    return min(distances)


def center_is_covered(vertices, faces, center, low, high):
    # A vertical ray through the mouth must not intersect a lid/top surface.
    for tri in vertices[faces]:
        matrix = np.stack((tri[1, :2]-tri[0, :2], tri[2, :2]-tri[0, :2]), axis=1)
        if abs(np.linalg.det(matrix)) < 1e-12:
            continue
        u, v = np.linalg.solve(matrix, center-tri[0, :2])
        if min(u, v) >= -1e-8 and u+v <= 1+1e-8:
            z = tri[0, 2]+u*(tri[1, 2]-tri[0, 2])+v*(tri[2, 2]-tri[0, 2])
            if low <= z <= high:
                return True
    return False


def generate(task, oracle, parameters):
    if oracle['frame'] != 'robot_base':
        raise ValueError('Require fresh robot_base geometry')
    arm = parameters['arm']
    hand = oracle['robot']['arms'][arm]
    obj = next(o for o in oracle['objects'] if o['id'] == parameters['object_id'])
    container = next(o for o in oracle['objects'] if o['id'] == parameters['container_id'])
    if obj['id'] == container['id'] or hand['held_object'] != obj['id']:
        raise ValueError('Require the selected free object retained in the selected hand')
    for item in (obj, container):
        if item['collision_mesh']['frame'] != 'robot_base':
            raise ValueError('Refresh both meshes after motion')
    if rotation(container['pose']['quaternion_xyzw'])[2, 2] < .98:
        raise ValueError('This program supports upright open bins; adapt for tilted containers')
    points = np.asarray(obj['collision_mesh']['vertices_m'], dtype=float)
    bv = np.asarray(container['collision_mesh']['vertices_m'], dtype=float)
    bf = np.asarray(container['collision_mesh']['faces'], dtype=int)
    center = np.asarray(container['pose']['position_m'], dtype=float)[:2]
    rim = bv[:, 2].max()
    if center_is_covered(bv, bf, center, rim-.06, rim+.005):
        raise ValueError('Opening center is covered near the rim; open lid or select another bin')
    radius = min(section_radius(bv, bf, rim-h, center) for h in (.012, .03, .06))
    clearance = float(parameters.get('clearance_m', .035))
    bottom_clearance = float(parameters.get('bottom_clearance_m', .045))
    if min(clearance, bottom_clearance) <= 0 or not np.isfinite([clearance, bottom_clearance]).all():
        raise ValueError('Positive finite rim and bottom clearances required')
    release = parameters.get('release', False)
    if type(release) is not bool:
        raise ValueError('release must be explicitly true or false')
    estimate = parameters.get('control_steps')
    if estimate is not None and (type(estimate) is not int or estimate < 1):
        raise ValueError('control_steps is a positive integer model estimate')
    hp = np.asarray(hand['eef_pose']['position_m'], dtype=float)
    hr = rotation(hand['eef_pose']['quaternion_xyzw'])
    op = np.asarray(obj['pose']['position_m'], dtype=float)
    offset, local = op-hp, points-op
    candidates = []
    # Preserve orientation first, then free wrist alternatives. No torso target.
    for yaw in parameters.get('yaw_degrees', [0, -30, 30, -60, 60, 90, -90, 180]):
        for tilt in parameters.get('tilt_degrees', [0, -20, 20]):
            d = axis_rotation((0, 0, 1), np.deg2rad(yaw)) @ axis_rotation((1, 0, 0), np.deg2rad(tilt))
            transformed = local @ d.T
            if np.linalg.norm(transformed[:, :2], axis=1).max()+clearance >= radius:
                continue
            target = np.r_[center, rim+bottom_clearance-transformed[:, 2].min()]
            goal = target-d@offset
            angle = np.arccos(np.clip((np.trace(d)-1)/2, -1, 1))
            name = 'mouth_yaw_'+str(yaw)+'_tilt_'+str(tilt)
            phase = dict(name='align_and_release' if release else 'align_above_mouth', arm=arm,
                frame='robot_base', goal=dict(position_m=goal.tolist(), quaternion_xyzw=quaternion(d@hr)),
                gripper='open' if release else 'hold', contact_target=None)
            if estimate is not None:
                phase['control_steps'] = estimate
            candidates.append(dict(name=name, score=float(np.linalg.norm(goal-hp)+.055*angle), phases=[phase]))
    if not candidates:
        raise ValueError('Full payload does not fit the measured opening with clearance')
    return dict(candidates=sorted(candidates, key=lambda c: c['score']), prepare_gripper=None)
