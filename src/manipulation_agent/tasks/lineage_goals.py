"""Private terminal relations for custom tasks with distinguishable materials.

The native slicer names replacements from their actual parent, not from cut
order. Validation pins that mapping before execution; evaluation only reads
actual objects and native Inside predicates. No simulator imports are needed.
"""
import math


def validate_relational_goals(spec):
    groups = spec.get('lineage_containment_goals', [])
    regions = spec.get('return_region_goals', [])
    if not isinstance(groups, list) or not isinstance(regions, list):
        raise ValueError('Declared relational goals must be lists')
    if (groups or regions) and spec.get('kind') != 'custom_bddl_sampled_instance':
        raise ValueError('Declared relational goals require a custom task')
    parents, children, targets = set(), set(), set()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'parent', 'children', 'destination'}:
            raise ValueError('Lineage goal requires parent, children and destination')
        if any(not isinstance(group[k], str) or not group[k] for k in ('parent','destination')):
            raise ValueError('Lineage entities must have nonempty names')
        if group['parent'] in parents or group['parent'] == group['destination']:
            raise ValueError('Duplicate or invalid lineage parent')
        parents.add(group['parent'])
        if not isinstance(group['children'], list) or not group['children']:
            raise ValueError('Lineage goal requires expected output descriptors')
        for i, child in enumerate(group['children']):
            if not isinstance(child, dict) or set(child) != {'name', 'category', 'model'}:
                raise ValueError('Output descriptor requires name, category and model')
            if any(not isinstance(v, str) or not v for v in child.values()):
                raise ValueError('Output descriptors must be nonempty strings')
            if child['name'] != f"half_{group['parent']}_{i}" or child['name'] in children:
                raise ValueError('Output names must match native parent-derived slicing identities')
            children.add(child['name'])
    for region in regions:
        if not isinstance(region, dict) or set(region) != {'object', 'center_xy', 'radius_m'}:
            raise ValueError('Return region requires object, center_xy and radius_m')
        if not isinstance(region['object'], str) or not region['object'] or region['object'] in targets:
            raise ValueError('Return region needs a distinct object')
        targets.add(region['object'])
        xy = region['center_xy']
        if not isinstance(xy, list) or len(xy) != 2:
            raise ValueError('Return center must have two coordinates')
        values = [*xy, region['radius_m']]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values) or region['radius_m'] <= 0:
            raise ValueError('Return region must be finite with positive radius')


def check_initial_lineages(spec, lookup):
    """Validate spec against the real parents' part metadata before any action."""
    validate_relational_goals(spec)
    for group in spec.get('lineage_containment_goals', []):
        parent = lookup(group['parent'])
        if parent is None or lookup(group['destination']) is None:
            raise ValueError('Initial lineage parent or destination is missing')
        parts = parent.metadata.get('object_parts', {})
        if not isinstance(parts, dict) or len(parts) != len(group['children']):
            raise ValueError('Declared lineage differs from native part metadata')
        for actual, expected in zip(parts.values(), group['children']):
            if actual.get('category') != expected['category'] or actual.get('model') != expected['model']:
                raise ValueError('Declared lineage output asset differs from native slicing metadata')
            if lookup(expected['name']) is not None:
                raise ValueError('Future lineage output already exists initially')


def evaluate_relational_goals(spec, lookup, inside):
    """Return every required condition, treating absent entities as unsatisfied."""
    checks = {}
    for group in spec.get('lineage_containment_goals', []):
        parent, destination = group['parent'], lookup(group['destination'])
        checks[f'{parent}:processed'] = lookup(parent) is None
        for expected in group['children']:
            obj = lookup(expected['name'])
            matches = (obj is not None and obj.category == expected['category'] and obj.model == expected['model'])
            checks[f"{expected['name']}:correct_order_inside"] = bool(
                matches and destination is not None and inside(obj, destination))
    for region in spec.get('return_region_goals', []):
        obj = lookup(region['object'])
        if obj is None:
            correct = False
        else:
            xy = obj.get_position_orientation()[0][:2]
            correct = sum((float(a)-float(b))**2 for a,b in zip(xy,region['center_xy'])) <= region['radius_m']**2
        checks[f"{region['object']}:return_region"] = correct
    return checks
