"""rc2 MAS_PLACE_FOOTPRINT (2026-11-22, benchmark side, coordinator 10:55 / 10:59; plan
sec20/footprint_plan_20261122/FOOTPRINT_PLAN_20261122.md): before release, the item's bottom must lie entirely on a
flat, free part of the selected support.

Why: on the T1 l4 tray a tea bottle (bottom radius 3.5 cm) released 3 cm inside the edge had part of its bottom on the
rim slope and fell 6/6 (TEA_TRACE_20261122); in a crowded corner tea and cups were released into occupied space and
fell or were thrown 0.5 m. The selector only ranked candidates by envelope overlap and checked one support ray.

Check, at a proposed release pose (no physics step):
  footprint = 2-D convex hull of the item's collision vertices within FOOTPRINT_BAND_M of its lowest point, in the
  proposed orientation (lying items use the face they lie on; same rule). Samples = up to MAX_HULL hull vertices,
  each also pulled halfway to the centroid, plus the centroid. A ray down from 3 cm above the support height at every
  sample (ignoring the item, the robot and carried items) must
    - hit the target itself (for a floor target: any floor-like or fixed object, e.g. a rug or a second floor piece),
    - with an upward normal within normal_max_deg() of vertical,
    - at a height within HEIGHT_TOL_M of the pose's centre support height;
  and the pose's envelope overlap score with other objects must be 0.
Not met: the pose is moved by the smallest displacement that meets it (rings of RING_STEP_M up to MAX_SHIFT_M =
the rc1 A5 limit, DIRECTIONS directions, the centre support re-measured at every shifted pose); if none does, the
placement is refused BEFORE the release with the existing placement_surface_unavailable text and the item stays in
the hand. Applies to every place_on_top except cloth; place_inside is unchanged (curved container bottoms; an item
that tips inside a container is still Inside). Open-cavity rim candidates (surface_kind open_fillable_rim: the tray's
inside, plates) are checked and RECORDED only, never moved or refused (coordinator 12:00 b: plates are concave; decide
from the record-mode table). When the clicked target is a movable item lying on a support (coordinator 12:00 c, as
support_judge case 3: a cup clicked on a fork lying in the tray), hits on that support count as the target.
Modes: off (default) | record (compute and write the private record only; behaviour and model-visible output are
byte-identical to off) | on.
Private record: placement_diagnostics `status: footprint_check`.
"""
import math
import os

FOOTPRINT_BAND_M = .01
HEIGHT_TOL_M = .01
MAX_SHIFT_M = .12
RING_STEP_M = .01
DIRECTIONS = 16
MAX_HULL = 16
SMALL_TARGET_FACTOR = 4.
DEFAULT_NORMAL_MAX_DEG = 5.   # 12:00 (a): scanned 15 / 10 / 8 / 5 deg; only 5 kept the near-edge tea standing (8 deg:
#   9/9 fell after a 1 cm shift the check accepted); no margin on the wide side. Evidence: FOOTPRINT_L4 (sec20/
#   footprint_plan_20261122/FOOTPRINT_RESULTS_20261122.md)
FLOOR_LIKE = frozenset({'floors', 'rug', 'carpet', 'mat', 'doormat', 'bath_rug'})
REFUSAL_TEXT = 'No upward receiving surface near the selected point'   # the existing placement_surface_unavailable text


def mode():
    m = os.environ.get('MAS_PLACE_FOOTPRINT', 'off')
    return m if m in ('record', 'on') else 'off'


def normal_max_deg():
    """Calibration knob for probes; default 5 deg (FOOTPRINT_L4 scan, coordinator 12:00 a)."""
    try:
        return float(os.environ.get('MAS_PLACE_FOOTPRINT_NORMAL_DEG', DEFAULT_NORMAL_MAX_DEG))
    except ValueError:
        return DEFAULT_NORMAL_MAX_DEG


# ---------------------------------------------------------------- pure geometry (testable without a simulator)

def convex_hull(points):
    """Monotone chain; points = iterable of (x, y); returns the hull counter-clockwise without repeats."""
    pts = sorted(set((round(float(x), 6), round(float(y), 6)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def thin(hull, maximum=MAX_HULL):
    """At most `maximum` hull vertices, spread by farthest-point sampling (keeps the extremes)."""
    if len(hull) <= maximum:
        return list(hull)
    keep = [hull[0]]
    d = [math.dist(p, keep[0]) for p in hull]
    while len(keep) < maximum:
        i = max(range(len(hull)), key=lambda k: d[k])
        keep.append(hull[i])
        d = [min(d[k], math.dist(hull[k], hull[i])) for k in range(len(hull))]
    order = {p: k for k, p in enumerate(hull)}
    return sorted(keep, key=lambda p: order[p])


def footprint_samples(relative_vertices, band=FOOTPRINT_BAND_M):
    """Root-relative (x, y) sample offsets of the bottom footprint and the bottom height (root-relative z)."""
    zs = [float(v[2]) for v in relative_vertices]
    zmin = min(zs)
    bottom = [(float(v[0]), float(v[1])) for v in relative_vertices if float(v[2]) <= zmin + band]
    hull = thin(convex_hull(bottom))
    if not hull:
        return [], zmin
    cx, cy = sum(p[0] for p in hull) / len(hull), sum(p[1] for p in hull) / len(hull)
    samples = [*hull, *[((p[0] + cx) / 2, (p[1] + cy) / 2) for p in hull], (cx, cy)]
    return samples, zmin


def judge_hits(hits, centre_z, normal_max, *, accept):
    """hits = [{'hit': bool, 'distance': float, 'owner': str|None, 'normal_z': float, 'z': float}] -> (ok, failures).
    accept(owner) says whether the hit body is the selected support."""
    cos_max = math.cos(math.radians(normal_max))
    failures = {'off_support': 0, 'other_object': 0, 'not_flat': 0, 'height_step': 0}
    for h in hits:
        if not h.get('hit') or h.get('distance', 1.) == 0.:
            failures['off_support'] += 1                       # nothing below, or the ray starts inside a body
        elif not accept(h.get('owner')):
            failures['other_object'] += 1                      # occupied by something else, or a different support
        elif h.get('normal_z', 0.) < cos_max:
            failures['not_flat'] += 1                          # rim slope, curved edge
        elif abs(h.get('z', centre_z) - centre_z) > HEIGHT_TOL_M:
            failures['height_step'] += 1
    return not any(failures.values()), {k: v for k, v in failures.items() if v}


def polygon_area(pts):
    if len(pts) < 3:
        return 0.
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                   for i in range(len(pts)))) / 2


def clicked_item_too_small(target_xy_area, footprint_area):
    """12:00 c: the clicked target counts as 'a small item lying on a support' (its support then counts as the target)
    when its top-view box is smaller than SMALL_TARGET_FACTOR footprints of the held item (fork vs cup: yes; tray or
    plate vs tea / hotdog: no -- so a tray's own support never excuses an overhang past the tray edge)."""
    return footprint_area > 0 and target_xy_area < SMALL_TARGET_FACTOR * footprint_area


def shift_rings(max_shift=MAX_SHIFT_M, step=RING_STEP_M, directions=DIRECTIONS):
    """(dx, dy) shifts in order of increasing displacement: rings of `step` up to max_shift, `directions` each."""
    out = []
    n = int(round(max_shift / step))
    for r in range(1, n + 1):
        d = r * step
        for k in range(directions):
            a = 2 * math.pi * k / directions
            out.append((d * math.cos(a), d * math.sin(a)))
    return out


# ---------------------------------------------------------------- simulator side

def _owner(backend, path):
    from .support_judge import owner_name
    name = owner_name(path)
    reg = getattr(backend.env.scene, 'object_registry', None)
    return name, (reg('name', name) if (reg is not None and name) else None)


def support_under(backend, obj):
    """Name of the body directly under a movable obj's bottom centre (downward ray; obj and robot ignored), else None."""
    if getattr(obj, 'fixed_base', False):
        return None
    try:
        from omnigibson.utils.sampling_utils import raytest
        from .support_judge import owner_name
        lo, hi = obj.aabb
        start = backend.torch.tensor([float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2, float(lo[2]) + 0.01])
        ignored = [link.prim_path for o in (obj, backend.robot) for link in o.links.values()]
        hit = raytest(start, start - backend.torch.tensor([0., 0., 0.10]), ignore_bodies=ignored)
        return owner_name(hit.get('rigidBody')) if hit.get('hit') else None
    except Exception:
        return None


def _acceptor(backend, target, under=None):
    floor = getattr(target, 'category', None) == 'floors'
    tname = getattr(target, 'name', None)
    cache = {}

    def accept(path):
        if path in cache:
            return cache[path]
        name, obj = _owner(backend, path)
        ok = name == tname or (under is not None and name == under) or \
            (floor and obj is not None and (getattr(obj, 'category', None) in FLOOR_LIKE
                                            or getattr(obj, 'fixed_base', False)))
        cache[path] = ok
        return ok
    return accept


def _rays(backend, root_xy, samples, bottom_z, centre_z, ignored):
    from omnigibson.utils.sampling_utils import raytest
    torch = backend.torch
    out = []
    for sx, sy in samples:
        x, y = root_xy[0] + sx, root_xy[1] + sy
        hit = raytest(torch.tensor([x, y, centre_z + .03]), torch.tensor([x, y, centre_z - .04]), ignore_bodies=ignored)
        out.append({'hit': bool(hit['hit']), 'distance': float(hit.get('distance', 1.)) if hit['hit'] else 1.,
                    'owner': hit.get('rigidBody'), 'normal_z': float(hit['normal'][2]) if hit['hit'] else 0.,
                    'z': float(hit['position'][2]) if hit['hit'] else centre_z})
    return out


def _centre_support(backend, xy, z_hint, ignored, accept):
    """Re-measure the support under a shifted bottom anchor, as _surface_candidates does (one ray)."""
    from omnigibson.utils.sampling_utils import raytest
    torch = backend.torch
    for h in (.03, .10):
        hit = raytest(torch.tensor([xy[0], xy[1], z_hint + h]), torch.tensor([xy[0], xy[1], z_hint - .08]),
                      ignore_bodies=ignored)
        if hit['hit'] and float(hit['distance']) > 0 and accept(hit.get('rigidBody')) \
                and float(hit['normal'][2]) >= .9 and abs(float(hit['position'][2]) - z_hint) <= .05:
            return float(hit['position'][2])
    return None


def apply(backend, held, target, point, candidate, payload, score):
    """Returns the candidate to release (possibly shifted) or raises the pre-release refusal (mode on).
    In record mode it only writes the record and returns `candidate` unchanged."""
    m = mode()
    if m == 'off' or candidate.get('relative_vertices') is None:
        return candidate
    from ..contracts import SkillError
    torch = backend.torch
    ignored = [link.prim_path for obj in (held, backend.robot, *payload) for link in obj.links.values()]
    rim = candidate.get('surface_kind') == 'open_fillable_rim'
    samples, bottom_rel_z = footprint_samples(candidate['relative_vertices'])
    try:
        tlo, thi = target.aabb
        t_area = float(thi[0] - tlo[0]) * float(thi[1] - tlo[1])
    except Exception:
        t_area = float('inf')
    under = support_under(backend, target) if clicked_item_too_small(t_area, polygon_area(samples[:(len(samples) - 1) // 2])) else None
    accept = _acceptor(backend, target, under)
    nmax = normal_max_deg()
    root, quat = candidate['pose']
    anchor = candidate['bottom_anchor_offset']
    centre_z = float(candidate['support_position'][2])
    root_xy = (float(root[0]), float(root[1]))

    def check(rxy, cz, overlap):
        ok, why = judge_hits(_rays(backend, rxy, samples, bottom_rel_z, cz, ignored), cz, nmax, accept=accept)
        if overlap:
            ok, why = False, {**why, 'overlap': overlap}
        return ok, why
    first_ok, first_why = check(root_xy, centre_z, candidate.get('proposal_overlap_score') or 0.)
    row = {'status': 'footprint_check', 'mode': m, 'held': held.name, 'target': target.name,
           'normal_max_deg': nmax, 'samples': len(samples), 'candidate': candidate.get('index'),
           'first_ok': first_ok, 'first_failures': first_why, 'candidate_kind': 'rim' if rim else 'surface',
           'support_under_target': under}
    chosen = candidate
    if not first_ok:
        found = None
        for dx, dy in shift_rings():
            ax, ay = float(root[0]) + dx + float(anchor[0]), float(root[1]) + dy + float(anchor[1])
            if point is not None and math.dist((ax, ay), (float(point[0]), float(point[1]))) > .20:
                continue                                       # would fail selected_surface_neighborhood anyway
            cz = _centre_support(backend, (ax, ay), centre_z, ignored, accept)
            if cz is None:
                continue
            new_root = root.clone()
            new_root[0] += dx; new_root[1] += dy; new_root[2] += cz - centre_z
            shifted = {**candidate, 'pose': (new_root, quat.clone()),
                       'support_position': [ax, ay, cz], 'footprint_shift_m': [round(dx, 4), round(dy, 4)]}
            overlap = score(shifted)
            ok, _ = check((float(new_root[0]), float(new_root[1])), cz, overlap)
            if ok:
                found = shifted
                break
        row.update(shift_m=found and round(math.hypot(*found['footprint_shift_m']), 3),
                   shift_xy_m=found and found['footprint_shift_m'], refused=found is None)
        chosen = found
    backend._placement_record(row)
    if m == 'record' or rim:
        return candidate
    if chosen is None:
        raise SkillError('placement_surface_unavailable', REFUSAL_TEXT)
    return chosen
