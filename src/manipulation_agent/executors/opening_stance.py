"""rc2 (2026-11-21, benchmark side, coordinator 21:14/21:16): stand in front of the opening of an openable object.

Before (rc1): navigate_to on an object picks, among reachable cells, the one whose distance to the clicked point is
closest to 1.25 m, ties broken by nearness to the robot -- coming from the side, the robot stops at the side of a
fridge / washer and cannot see into it (T1 fridge, T2 washer: 0 px from that stance, 500+ px from the front).

After (switch MAS_OPENING_STANCE, default on; off = rc1): when the clicked object -- or the openable object whose
box contains the clicked point -- has an Open state with a door or drawer whose opening faces sideways, the stance is
chosen in front of that opening (0.7-1.1 m out along the opening normal, at most 0.25 m off the axis, still within the
usual reach of the clicked point); no such reachable cell, or its route is more than 2 m longer than the rc1
stance's route -> the rc1 rule. Only the stance changes; receipts do not.

Opening normal, generic (no task role, category or coordinate is read):
1. Joints: OmniGibson's Open-state relevant joints of the object. The one used: the door/drawer link whose box
   contains the clicked point, else the largest door/drawer link.
2. Door (revolute, vertical axis): the door link's centre with the door closed (current centre rotated back about the
   hinge by the joint position; of the two senses the one nearer the body is taken) relative to the body centre,
   expressed in the body's own frame; the dominant horizontal body axis (relative to the body half-extent) is the
   opening side. The hinge must lie on the same side (check); otherwise -> rc1 rule, recorded.
3. Drawer (prismatic, horizontal axis): the slide axis, signed outward (away from the body centre).
0. A body thinner than 0.25 m (room door leaf / frame: no interior, passable from both sides) -> rc1 rule.
4. Revolute with a horizontal hinge: a lid (closed panel on top) -> rc1 rule; a drop-down front door (oven: closed
   panel on a side face) -> that face. Vertical slide, or a panel sliding along the body's long side (sliding door,
   not a drawer) -> rc1 rule.
Every decision is written to the private navigation record (normal, face, basis, fallback reason)."""
import math
import os

FRONT_MIN_M, FRONT_MAX_M, FRONT_BEST_M = 0.7, 1.1, 0.9
LATERAL_MAX_M = 0.25
VERTICAL_AXIS_MIN = 0.7          # |axis_z| above this = vertical hinge (door); below 1-this... = horizontal (lid / drawer)
HINGE_FACE_MIN = 0.6             # hinge must sit at >= 60 % of the half-extent on the chosen side
THIN_BODY_M = 0.25               # body thinner than this (a room door leaf / frame) has no inside to look into
DETOUR_MAX_M = 2.0               # new stance's route may be at most this much longer than the rc1 stance's route


def enabled():
    return os.environ.get('MAS_OPENING_STANCE', 'on') == 'on'


# ---------------- pure geometry (simulator-free, unit-tested) ----------------
def _rot_z(v, ang):
    c, s = math.cos(ang), math.sin(ang)
    return [c * v[0] - s * v[1], s * v[0] + c * v[1]]


def _rotate_about(v, axis, ang):
    """Rodrigues rotation of 3-vector v about unit axis by ang."""
    n = math.sqrt(sum(a * a for a in axis)) or 1.0
    k = [a / n for a in axis]
    c, s = math.cos(ang), math.sin(ang)
    dot = sum(k[i] * v[i] for i in range(3))
    cross = [k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0]]
    return [v[i] * c + cross[i] * s + k[i] * dot * (1 - c) for i in range(3)]


def closed_center(kind, center, pivot, axis, q, body_center):
    """Door/drawer link centre with the joint at 0. Sign of q is not trusted: of the two candidates the one whose
    horizontal distance to the body centre is smaller is returned (a closed door lies on the body)."""
    if kind == 'revolute':
        rel = [center[i] - pivot[i] for i in range(3)]
        cands = [[pivot[i] + r for i, r in enumerate(_rotate_about(rel, axis, s * q))] for s in (-1, 1)]
    else:
        n = math.sqrt(sum(a * a for a in axis)) or 1.0
        cands = [[center[i] - s * q * axis[i] / n for i in range(3)] for s in (-1, 1)]
    return min(cands, key=lambda c: math.hypot(c[0] - body_center[0], c[1] - body_center[1]))


def face_from_point(point, body_center, body_yaw, half_extents):
    """Dominant horizontal body axis of point (in the body frame, relative to the half-extent): returns
    (axis index 0/1, sign, normalised coordinate)."""
    rel = _rot_z([point[0] - body_center[0], point[1] - body_center[1]], -body_yaw)
    scaled = [rel[i] / max(half_extents[i], 1e-6) for i in (0, 1)]
    i = 0 if abs(scaled[0]) >= abs(scaled[1]) else 1
    return i, (1 if scaled[i] >= 0 else -1), abs(scaled[i])


def opening_normal(kind, axis_world, closed_ctr, hinge, body_center, body_yaw, half_extents, half_z=None):
    """World xy unit normal of the opening side, plus a reason string; (None, reason) when there is none."""
    vertical = abs(axis_world[2]) / (math.sqrt(sum(a * a for a in axis_world)) or 1.0)
    if 2 * min(half_extents[0], half_extents[1]) < THIN_BODY_M:
        # coordinator 23:59: a room door is a thin leaf passable from both sides, with no interior; generic by
        # geometry (no category name)
        return None, f'thin_body_no_interior(depth {2 * min(half_extents):.2f} m)'
    if kind == 'revolute':
        i, sgn, coord = face_from_point(closed_ctr, body_center, body_yaw, half_extents)
        if vertical < VERTICAL_AXIS_MIN:
            # horizontal hinge: a lid (closed panel on the top) has no sideways opening; a drop-down front door
            # (oven, dishwasher: closed panel on a side face, hinge at its bottom edge) opens sideways
            zc = abs(closed_ctr[2] - body_center[2]) / max(half_z or 0.0, 1e-6) if half_z else float('inf')
            if not (coord >= zc):
                return None, 'lid_horizontal_hinge'
        hi, hsgn, hcoord = face_from_point(hinge, body_center, body_yaw, half_extents)
        rel_h = _rot_z([hinge[0] - body_center[0], hinge[1] - body_center[1]], -body_yaw)
        on_face = (rel_h[i] * sgn) / max(half_extents[i], 1e-6)
        if on_face < HINGE_FACE_MIN:
            return None, f'hinge_not_on_door_face(axis {i}, hinge at {on_face:.2f} of half-extent)'
        local = [0.0, 0.0]; local[i] = float(sgn)
        n = _rot_z(local, body_yaw)
        kind_txt = 'door_panel_closed_centre_face' if vertical >= VERTICAL_AXIS_MIN else 'drop_down_door_closed_centre_face'
        return n, f'{kind_txt}(axis {i} sign {sgn}); hinge check {on_face:.2f}'
    # prismatic
    if vertical > 1 - VERTICAL_AXIS_MIN:
        return None, 'vertical_slide'
    a = [axis_world[0], axis_world[1]]
    na = math.hypot(*a) or 1.0
    a = [a[0] / na, a[1] / na]
    # a drawer slides along the body's shorter horizontal side (its depth); a panel sliding along the long side is a
    # sliding door / screen, whose "outward" is not a place to stand
    al = _rot_z(a, -body_yaw)
    k = 0 if abs(al[0]) >= abs(al[1]) else 1
    if half_extents[k] > half_extents[1 - k]:
        return None, 'sliding_panel_along_long_side'
    out = [closed_ctr[0] - body_center[0], closed_ctr[1] - body_center[1]]
    if a[0] * out[0] + a[1] * out[1] < 0:
        a = [-a[0], -a[1]]
    return a, 'drawer_slide_axis_outward'


def face_center(body_center, body_yaw, half_extents, normal):
    """Point on the opening side of the body box along the normal."""
    local = _rot_z(normal, -body_yaw)
    d = abs(local[0]) * half_extents[0] + abs(local[1]) * half_extents[1]
    return [body_center[0] + normal[0] * d, body_center[1] + normal[1] * d]


def pick_front_cell(points_xy, dist_to_click, reach_m, front, normal):
    """Index of the reachable cell best placed in front of the opening, or None.
    points_xy: list of (x, y); dist_to_click: list of distances to the clicked point."""
    best, best_score = None, None
    for k, (x, y) in enumerate(points_xy):
        if dist_to_click[k] > reach_m:
            continue
        rx, ry = x - front[0], y - front[1]
        t = rx * normal[0] + ry * normal[1]
        lat = abs(-rx * normal[1] + ry * normal[0])
        if not (FRONT_MIN_M <= t <= FRONT_MAX_M) or lat > LATERAL_MAX_M:
            continue
        score = abs(t - FRONT_BEST_M) + lat
        if best_score is None or score < best_score:
            best, best_score = k, score
    return best


# ---------------- simulator glue ----------------
def _quat_to_yaw(q):
    x, y, z, w = (float(v) for v in q)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _quat_rotate(q, v):
    """Rotate 3-vector v by quaternion q = (x, y, z, w): v + 2w(u x v) + 2u x (u x v)."""
    x, y, z, w = (float(a) for a in q)
    u = [x, y, z]
    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    t = cross(u, v)
    t = [2 * c for c in t]
    ut = cross(u, t)
    return [v[i] + w * t[i] + ut[i] for i in range(3)]


def _quat_mul(a, b):
    ax, ay, az, aw = (float(v) for v in a); bx, by, bz, bw = (float(v) for v in b)
    return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]


def _box(obj_or_link):
    lo, hi = obj_or_link.aabb
    return [float(v) for v in lo], [float(v) for v in hi]


def _openable(obj):
    try:
        from omnigibson.object_states import Open
        return obj is not None and hasattr(obj, 'states') and Open in obj.states
    except Exception:
        return False


def _container_of_point(backend, obj, point):
    if _openable(obj):
        return obj
    p = [float(v) for v in point]
    best = None
    for o in backend.env.scene.objects:
        if o is obj or not _openable(o):
            continue
        try:
            lo, hi = _box(o)
        except Exception:
            continue
        if all(lo[i] - .02 <= p[i] <= hi[i] + .02 for i in range(3)):
            vol = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
            if best is None or vol < best[0]:
                best = (vol, o)
    return None if best is None else best[1]


def opening_info(backend, obj, point):
    """Opening normal of the openable object hit (or containing the hit point); dict for the private record."""
    body = _container_of_point(backend, obj, point)
    if body is None:
        return {'status': 'not_openable'}
    from omnigibson.object_states.open_state import _get_relevant_joints
    _, joints, _ = _get_relevant_joints(body)
    base = body.links.get(getattr(body, 'root_link_name', 'base_link')) or next(iter(body.links.values()))
    blo, bhi = _box(base)
    bc = [(blo[i] + bhi[i]) / 2 for i in range(3)]
    yaw = _quat_to_yaw(body.get_position_orientation()[1])
    # half-extents in the body frame from the base link's world box (axis-aligned) rotated into the body frame
    wx, wy = (bhi[0] - blo[0]) / 2, (bhi[1] - blo[1]) / 2
    c, s = abs(math.cos(yaw)), abs(math.sin(yaw))
    det = c * c - s * s
    if abs(det) > 0.2:
        hx, hy = (c * wx - s * wy) / det, (c * wy - s * wx) / det
    else:
        hx = hy = max(wx, wy)
    half = [max(hx, 0.05), max(hy, 0.05)]
    p = [float(v) for v in point]
    cands = []
    for j in joints:
        try:
            link = next(l for l in body.links.values() if l.prim_path == j.body1)
        except StopIteration:
            continue
        lo, hi = _box(link)
        area = max((hi[0] - lo[0]), (hi[1] - lo[1])) * (hi[2] - lo[2])
        inside = all(lo[i] - .02 <= p[i] <= hi[i] + .02 for i in range(3))
        cands.append((inside, area, j, link, lo, hi))
    if not cands:
        return {'status': 'no_joint', 'body': body.name}
    cands.sort(key=lambda t: (t[0], t[1]), reverse=True)
    inside, area, j, link, lo, hi = cands[0]
    lp, lq = link.get_position_orientation()
    lp = [float(v) for v in lp]
    pivot = [lp[i] + d for i, d in enumerate(_quat_rotate(lq, [float(v) for v in j.local_position_1]))]
    axis_local = {'X': [1, 0, 0], 'Y': [0, 1, 0], 'Z': [0, 0, 1]}[j.axis]
    axis_w = _quat_rotate(_quat_mul(lq, j.local_orientation_1), axis_local)
    q = float(j.get_state()[0][0])
    kind = 'revolute' if j.is_revolute else 'prismatic'
    center = [(lo[i] + hi[i]) / 2 for i in range(3)]
    closed = closed_center(kind, center, pivot, axis_w, q, bc)
    normal, why = opening_normal(kind, axis_w, closed, pivot, bc, yaw, half, half_z=(bhi[2] - blo[2]) / 2)
    info = {'status': 'ok' if normal else 'no_side_opening', 'body': body.name, 'joint': j.joint_name, 'kind': kind,
            'clicked_link': bool(inside), 'joint_q': round(q, 3), 'axis_world': [round(v, 3) for v in axis_w],
            'body_center': [round(v, 3) for v in bc], 'body_yaw_deg': round(math.degrees(yaw), 1),
            'half_extents': [round(v, 3) for v in half], 'closed_door_center': [round(v, 3) for v in closed],
            'hinge': [round(v, 3) for v in pivot], 'basis': why}
    if normal:
        info['normal'] = [round(normal[0], 4), round(normal[1], 4)]
        info['face_center'] = [round(v, 3) for v in face_center(bc, yaw, half, normal)]
    return info
