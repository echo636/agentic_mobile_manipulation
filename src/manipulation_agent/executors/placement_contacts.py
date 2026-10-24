"""Permit shallow physical contact without accepting embedded placements."""
import math


# Contact offsets and solver tolerances produce small signed overlaps even on
# ordinary supports. This is a penetration tolerance, not a clearance reserve.
MAX_PLACEMENT_PENETRATION_M = .005


def contact_report(backend, held, payload=()):
    """Read signed separations for the assembly's current external contacts.

    A Boolean contact flag cannot distinguish touching a support from being
    embedded in it. Unknown or truncated evidence retains the old rejection;
    only measured shallow contacts receive the relaxed admission policy.
    Call after physics, never immediately after a hypothetical pose setter.
    """
    from omnigibson.utils.usd_utils import RigidContactAPI

    assembly = (held, *payload)
    scene = held.scene.idx
    paths = {str(link.prim_path) for obj in assembly for link in obj.links.values()}
    pairs = RigidContactAPI.get_contact_pairs(scene, query_set=assembly,
                                              with_set=None, current_only=True)
    external = {(a, b) for a, b in pairs if b not in paths}
    report = {'admissible': True, 'contact_bodies': sorted({b for _, b in external}),
              'contact_points': 0, 'minimum_separation_m': None,
              'max_penetration_m': 0., 'penetration_limit_m': MAX_PLACEMENT_PENETRATION_M}
    if not external:
        return report
    try:
        view = RigidContactAPI._CONTACT_VIEW[scene]
        _, _, _, separations, counts, starts = view.get_contact_data(
            dt=backend.og.sim.get_physics_dt())
        row_map = RigidContactAPI._PATH_TO_ROW_IDX[scene]
        col_map = RigidContactAPI._PATH_TO_COL_IDX[scene]
        minimum = math.inf
        for a, b in external:
            row, col = row_map[a], col_map[b]
            count, start = int(counts[row, col]), int(starts[row, col])
            if count <= 0 or start < 0 or start+count > len(separations):
                raise ValueError('Missing or truncated contact separations')
            values = separations[start:start+count].flatten()
            value = float(values.min())
            if not math.isfinite(value):
                raise ValueError('Nonfinite contact separation')
            minimum = min(minimum, value)
            report['contact_points'] += count
        report.update(minimum_separation_m=minimum,
                      max_penetration_m=max(0., -minimum),
                      admissible=minimum >= -MAX_PLACEMENT_PENETRATION_M)
        if not report['admissible']:
            report['reason'] = 'deep_penetration'
    except (AttributeError, KeyError, IndexError, RuntimeError, ValueError) as exc:
        report.update(admissible=False, reason='contact_separation_unavailable',
                      error_type=type(exc).__name__)
    return report
