"""CPU validation of explicit construction extensions; no simulator setters."""
import math


def robot_spawn_supports(position, floor_bounds):
    """Reject parked/fallen robots; this coarse screen does not prove a route."""
    if len(position)!=3 or not all(math.isfinite(float(v)) for v in position):
        return []
    return [name for name,(low,high) in floor_bounds.items()
            if all(low[i]-.05<=position[i]<=high[i]+.05 for i in (0,1))
            and high[2]-.1<=position[2]<=high[2]+1.5]


def upright_orientation(quaternion, max_tilt_degrees=15.):
    x,y,z,w=map(float,quaternion)
    norm=x*x+y*y+z*z+w*w
    return math.isfinite(norm) and norm>0 and 1.-2.*(x*x+y*y)/norm>=math.cos(math.radians(max_tilt_degrees))


def validate_construction(spec):
    roles = spec['roles']
    native=spec.get('native_assets',[])
    if len({r['name'] for r in native})!=len(native):raise ValueError('Duplicate native assets')
    for row in native:
        if not row.get('category') or not row.get('model') or len(row.get('usd_sha256',''))!=64:
            raise ValueError('Native assets need pinned category, model and SHA256')
        if row.get('prim_type',0) not in (0,1):raise ValueError('Unknown native primitive type')
    if spec.get('reference_plan',{}).get('kind')=='full_workflow':
        from .full_workflow_reference import validate_workflow_plan
        validate_workflow_plan(spec)
    if spec.get('robot_start'):
        pose=spec['robot_start']
        for key,length in [('position',3),('orientation',4)]:
            if len(pose[key])!=length or not all(math.isfinite(float(v)) for v in pose[key]):
                raise ValueError('Invalid construction robot pose')
        if sum(float(v)**2 for v in pose['orientation'])<=0:
            raise ValueError('Invalid construction robot orientation')
    excluded=spec.get('scene_exclusions',[])
    if len(excluded)!=len(set(excluded)) or set(excluded)&set(roles.values()):
        raise ValueError('Scene exclusions must be distinct background entities outside task roles')
    future = set(spec.get('future_roles', []))
    if not future <= roles.keys():
        raise ValueError('Unknown future entity')
    produced = []
    for rule in spec.get('replacements', []):
        parent, children = rule['parent'], rule['children']
        if parent not in roles or parent in future or not children:
            raise ValueError('Invalid replacement parent')
        for i, child in enumerate(children):
            role = child['role']
            if role not in future or roles[role] != f'half_{roles[parent]}_{i}':
                raise ValueError('Replacement binding must match native slicing names')
            if not child.get('category') or not child.get('model'):
                raise ValueError('Replacement needs pinned category/model')
            produced.append(role)
    if set(produced) != future or len(produced) != len(set(produced)):
        raise ValueError('Future entities must have exactly one declared producer')
    names = []
    for fixture in spec.get('fixtures', []):
        names.append(fixture['name'])
        if fixture['fixed_base'] is not True:
            raise ValueError('Construction supports must be fixed')
        for key in ('position','size'):
            values = fixture[key]
            if (len(values) != 3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values)
                    or (key == 'size' and min(values) <= 0)):
                raise ValueError('Invalid fixture geometry')
    if len(names) != len(set(names)):
        raise ValueError('Duplicate fixture names')
    for row in spec['evaluation']['predicates'].values():
        if row['state']=='NearInitial' and not 0<float(row.get('distance',.03))<=.10:
            raise ValueError('Invalid protected object displacement tolerance')
        if row['state']=='Upright' and not 0<float(row.get('max_tilt_degrees',15.))<=45:
            raise ValueError('Invalid upright tilt tolerance')
        if row['state'] in ('PairedOnTop','CountOnTop'):
            if not row.get('members') or not set(row['members']) <= roles.keys():
                raise ValueError('Pair predicate requires known member roles')
            if len(set(row['members'])) != len(row['members']):
                raise ValueError('Counted members must be distinct')
            if row['state']=='CountOnTop' and (type(row.get('count')) is not int
                                              or not 1<=row['count']<=len(row['members'])):
                raise ValueError('Invalid target object count')
    for rule in spec['evaluation'].get('carry_guards',[]):
        if rule.get('mode')!='single_object':
            raise ValueError('Unknown handling protocol')
    for move in spec.get('initial_layout', []):
        attempts=move.get('max_sampling_attempts',1)
        if type(attempts) is not int or not 1<=attempts<=3:
            raise ValueError('Construction state sampling needs a bounded positive attempt count')
        if move['object'] not in roles or move['object'] in future:
            raise ValueError('Initial layout cannot manipulate a future/unknown entity')
        if move.get('target') and move['target'] not in roles:
            raise ValueError('Unknown layout target')
    if len(spec['acceptance_cases']['physical_negative_cases']) < 3:
        raise ValueError('100+ candidates require at least three physical negatives')


def replacement_complete(rule, roles, live, initially_real):
    """Conserve native slicing identity; same-looking unrelated parts never count."""
    parent = roles[rule['parent']]
    if parent not in initially_real or parent in live:
        return False
    for child in rule['children']:
        name = roles[child['role']]
        obj = live.get(name)
        if name in initially_real or obj is None:
            return False
        if (obj.category, obj.model) != (child['category'], child['model']):
            return False
    return True
