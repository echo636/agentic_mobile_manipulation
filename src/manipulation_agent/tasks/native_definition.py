"""Process-local registration of a pinned custom BDDL, without editing upstream files."""
import copy
import hashlib
from pathlib import Path


def resolve_definition(spec, spec_path):
    """Resolve and verify the explicit source for a custom sampled instance."""
    if spec.get('kind') != 'custom_bddl_sampled_instance':
        return None
    row = spec.get('definition', {})
    if not isinstance(row.get('path'), str) or not row['path']:
        raise ValueError('Custom native task requires a pinned BDDL definition')
    path = (Path(spec_path).resolve().parent / row['path']).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest() != row.get('sha256'):
        raise ValueError('Custom native BDDL hash mismatch')
    if row['sha256'] != spec.get('bddl_sha256'):
        raise ValueError('Custom native definition differs from sampling identity')
    return path


def register_definition(knowledgebase, task_name, text):
    """Register one definition in this worker's in-memory knowledge base.

    Existing names are accepted only when their definition is byte-identical.
    This does not edit installed BDDL, the knowledge-base cache, or task assets.
    """
    from bddl.activity import Conditions
    conditions = Conditions(task_name, 0, 'behavior-1k', predefined_problem=text)
    # The parser accepts an explicit problem string without enforcing its name.
    from bddl.parsing import parse_problem
    parsed_name, *_ = parse_problem(task_name, 0, 'behavior-1k', predefined_problem=text)
    if parsed_name != task_name + '-0':
        raise ValueError('Custom native BDDL problem name does not match task')
    name = task_name + '-0'
    existing = knowledgebase.get_task(name)
    if existing is not None:
        if existing.definition != text:
            raise ValueError('Refusing to replace an existing native task definition')
        return existing
    synsets = {}
    for category in conditions.parsed_objects:
        if category == 'agent.n.01':
            continue
        synset = knowledgebase.get_synset(category)
        if synset is None:
            raise ValueError('Unknown custom task synset: ' + category)
        synsets[category] = synset
    future = {row[1] for row in conditions.parsed_initial_conditions if row[0] == 'future'}
    task = knowledgebase.add_task(name=name, definition=text)
    task.synsets = set(synsets.values())
    task.future_synsets = {synsets[category] for category, members in conditions.parsed_objects.items()
                           if category in synsets and any(member in future for member in members)}
    return task


def sampling_scene_config(base, scene_model):
    """Keep native furniture before online sampling has created object bindings."""
    result = copy.deepcopy(base)
    result.pop("scene_file", None)
    result.pop("scene_instance", None)
    result.update(scene_model=scene_model, include_robots=False,
                  load_room_types=None, load_room_instances=None,
                  load_object_categories=None, not_load_object_categories=None,
                  load_task_relevant_only=False)
    return result


def with_saved_bindings(scene, bindings):
    """Add recorded native scope metadata without changing objects or physics."""
    result = copy.deepcopy(scene)
    objects = set(result['objects_info']['init_info'])
    systems = set(result.get('state', {}).get('registry', {}).get('system_registry', {}))
    if not bindings or any(not isinstance(k, str) or not isinstance(v, str) or
                           v not in objects | systems for k, v in bindings.items()):
        raise ValueError('Recorded task bindings must name existing scene entities')
    metadata = result.setdefault('metadata', {}).setdefault('task', {})
    previous = metadata.get('inst_to_name', {})
    if any(key in previous and previous[key] != value for key, value in bindings.items()):
        raise ValueError('Recorded task bindings conflict with the frozen scene')
    metadata['inst_to_name'] = {**previous, **bindings}
    return result
