"""Read-only admission for bindings deferred by the pinned BDDL task compiler.

Scene metadata is a cache, not the complete runtime object scope. Do not write
inferred assignments into it: OmniGibson must perform its own initialization.
"""
from collections import Counter
from functools import lru_cache


@lru_cache(maxsize=1)
def _knowledge_base():
    try:
        from bddl.knowledge_base import KnowledgeBase
    except ImportError as exc:
        raise ValueError('Official BDDL compiler unavailable for deferred bindings') from exc
    return KnowledgeBase(verbose=False)


def audit_wildcard_bindings(data, required, task_name, *, knowledge_base=None):
    """Check official compiled scope and available entities without starting OG.

    Only newly expanded instances may lack cached bindings. Existing broken
    references and missing ordinary instances remain hard failures. Candidate
    assignments demonstrate capacity; runtime initialization is authoritative.
    """
    kb = knowledge_base if knowledge_base is not None else _knowledge_base()
    definition = kb.get_task(task_name + '-0')
    _, base_scope, inroom = definition.parse_base_scope()
    bindings = data.get('metadata', {}).get('task', {}).get('inst_to_name', {})
    objects = data['objects_info']['init_info']
    systems = data.get('state', {}).get('registry', {}).get('system_registry', {})
    missing = set(required) - set(bindings)
    broken = sorted(inst for inst in required if inst in bindings and
                    not inst.startswith('agent.') and bindings[inst] not in objects
                    and bindings[inst] not in systems)
    if broken:
        raise ValueError('Cached bindings reference absent entities: ' + str(broken))
    ordinary_missing = sorted(missing & set(base_scope))
    if ordinary_missing:
        raise ValueError('Missing non-wildcard bindings: ' + str(ordinary_missing))

    # Match BehaviorTask._determine_room_instances and its room layout counts.
    rooms = {}
    for inst, room_type in inroom.items():
        args = objects.get(bindings.get(inst), {}).get('args', {})
        for room in args.get('in_rooms', []):
            if room.rsplit('_', 1)[0] == room_type:
                rooms.setdefault(room_type, room)
                break
    layout = {room_type: dict(Counter(
        entry.get('args', {}).get('category', 'robot') for entry in objects.values()
        if room in entry.get('args', {}).get('in_rooms', [])))
        for room_type, room in rooms.items()}
    compiled = definition.compile(scene_layout=layout)
    expanded = set(compiled.object_scope) - set(base_scope) - {'agent.n.01_1'}
    unsupported = sorted(missing - expanded)
    if unsupported:
        raise ValueError('Instance keys not supplied by official wildcard expansion: ' + str(unsupported))

    # Account for objects already consumed by cached bindings. Match the pinned
    # taxonomy (including descendant categories), never object-name prefixes.
    used = {bindings[inst] for inst in compiled.object_scope if inst in bindings}
    candidates = {}
    for inst in compiled.object_scope:
        if inst not in expanded or inst in bindings:
            continue
        synset = kb.get_synset(inst.rsplit('_', 1)[0])
        categories = {category.name
                      for s in [synset] + sorted(synset.descendants, key=lambda x: x.name)
                      if s.is_leaf for category in s.categories}
        name = next((name for name, entry in objects.items()
                     if name not in used and entry.get('args', {}).get('category') in categories), None)
        if name is None:
            raise ValueError('No unassigned scene object for expanded instance: ' + inst)
        candidates[inst] = name
        used.add(name)
    unresolved = sorted(missing - set(candidates))
    if unresolved:
        raise ValueError('Unresolved deferred bindings: ' + str(unresolved))
    return {'status': 'passed', 'task': task_name,
            'mechanism': 'official_bddl_compilation_with_serialized_room_layout',
            'missing_static_bindings': sorted(missing), 'room_instances': rooms,
            'candidate_assignments_offline_only': candidates,
            'runtime_validation_required': True, 'template_modified': False}


def validate_runtime_bindings(object_scope, required):
    """Validate actual entities before the official evaluator restores states."""
    missing = sorted(inst for inst in required if object_scope.get(inst) is None)
    return {'status': 'failed' if missing else 'passed',
            'required_count': len(required), 'missing_instances': missing,
            'level': 'actual_initialized_simulator_object_scope',
            'bindings_offline_only': {inst: getattr(object_scope[inst], 'name', None)
                                      for inst in sorted(required) if inst not in missing}}
