"""Discover explicit Manip recipes; trusted snapshots and sandboxed development.

Navigation is selected separately by the agent. Recipe ok is never independent
acceptance. Mutable exploration sources are always drafts.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import uuid

from .contracts import SkillError, validate
from .exploration_workspace import safe_tree, tree_hash

TOOLS = frozenset({'list_manip_recipes', 'read_manip_recipe', 'manip'})


def source_root():
    source = Path(__file__).resolve().parents[2] / 'program_skills/rax'
    return source if source.is_dir() else Path(sys.prefix) / 'share/manipulation-agentic-system/program_skills/rax'


def tool_specs():
    opened = {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': True}
    rows = [
        ('list_manip_recipes', {}, [],
         'List available Manip recipes, applicability, source hashes and editable project names. After empty navigation, select a matching recipe from fresh RGB/geometry.'),
        ('read_manip_recipe', {'recipe': {'type': 'string'}}, ['recipe'],
         'Read a recipe contract, defaults, preconditions, evidence limits and source path before choosing arguments.'),
        ('manip', {'recipe': {'type': 'string'}, 'targets': deepcopy(opened),
                   'parameters': deepcopy(opened), 'constraints': deepcopy(opened),
                   'revision': {'type': 'integer', 'minimum': 0}, 'allow_draft': {'type': 'boolean'}},
         ['recipe', 'targets', 'revision'],
         'Execute the selected Manip recipe at the current station. Default: no base motion. Draft development needs allow_draft=true. Blocks until completion; stop on failure, inspect current RGB/geometry, and edit the workspace recipe rather than repeating unchanged calls. No task-success claim follows from ok=true.'),
    ]
    return [{'name': name, 'description': desc, 'inputSchema': {
        'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False},
        'annotations': {'readOnlyHint': name != 'manip', 'destructiveHint': name == 'manip',
                        'idempotentHint': name != 'manip', 'openWorldHint': False}}
            for name, props, required, desc in rows]


class ManipRecipes:
    def __init__(self, root):
        self.root = Path(root)

    def entries(self):
        result = {}
        if not self.root.is_dir():
            return result
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.is_symlink() or not (folder / 'spec.json').is_file():
                continue
            spec = json.loads((folder / 'spec.json').read_text())
            name = spec.get('recipe')
            if not name:
                continue
            files = safe_tree(folder)
            if not isinstance(name, str) or not name.replace('_', '').isalnum() or name in result:
                raise SkillError('invalid_recipe', 'Recipe names must be unique identifiers')
            result[name] = {'recipe': name, 'project': folder.name, 'directory': folder,
                            'source_sha256': tree_hash(files), 'controller_sha256': files['controller.py'],
                            'status': 'draft', 'spec': spec,
                            'applicability': spec.get('applicability', 'Read the recipe preconditions before use'),
                            'contract': spec['recipe_contract']}
        return result

    def get(self, recipe):
        entry = self.entries().get(recipe)
        if entry is None:
            raise SkillError('unknown_recipe', 'Choose a recipe from list_manip_recipes')
        return entry

    @classmethod
    def frozen_project(cls, directory):
        """Read one exact controller bundle, never the mutable recipe library."""
        directory = Path(directory)
        spec = json.loads((directory / 'spec.json').read_text())
        name = spec.get('recipe')
        if not isinstance(name, str) or not name or not name.replace('_', '').isalnum():
            raise SkillError('invalid_recipe', 'Frozen project must declare a recipe identifier')
        files = safe_tree(directory)
        return {'recipe': name, 'project': directory.name, 'directory': directory,
                'source_sha256': tree_hash(files), 'controller_sha256': files['controller.py'],
                'status': 'draft', 'spec': spec, 'contract': spec['recipe_contract']}

    def catalog(self):
        return [{k: deepcopy(v) for k, v in entry.items() if k not in {'directory', 'spec', 'contract'}}
                for entry in self.entries().values()]

    def read(self, recipe):
        entry = self.get(recipe)
        return {**{k: deepcopy(v) for k, v in entry.items() if k not in {'directory', 'spec'}},
                'controller_path': str(entry['directory'] / 'controller.py'),
                'text': (entry['directory'] / 'SKILL.md').read_text(),
                'validation': 'draft; independent physical qualification required'}

    def copy_to(self, destination):
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for entry in self.entries().values():
            target = destination / entry['project']
            if target.exists():
                raise ValueError('Refuse to replace an existing recipe project')
            files = safe_tree(entry['directory'])
            for rel in files:
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry['directory'] / rel, out)
        return ManipRecipes(destination)


def checked_invocation(entry, arguments):
    """Reject bad parameters before any observation, notes write or motor call."""
    schema = next(t['inputSchema'] for t in tool_specs() if t['name'] == 'manip')
    validate(arguments, schema)
    if arguments['recipe'] != entry['recipe']:
        raise SkillError('unknown_recipe', 'Recipe binding changed')
    if arguments.get('allow_draft') is not True:
        raise SkillError('draft_recipe', 'This source is a draft; explicit allow_draft=true is required')
    contract = entry['contract']
    targets = arguments['targets']
    target_spec = contract['targets']
    if set(targets) - set(target_spec) or any(v.get('required') and k not in targets for k, v in target_spec.items()):
        raise SkillError('invalid_arguments', 'Missing or unknown target roles')
    targets = {**{k: deepcopy(v['default']) for k, v in target_spec.items() if 'default' in v}, **targets}
    for entity in targets.values():
        if (not isinstance(entity, dict) or set(entity) != {'task_name'} or
                not isinstance(entity['task_name'], str) or not entity['task_name'].strip()):
            raise SkillError('invalid_arguments', 'Targets need EntityRef JSON: {task_name: nonempty string}')
    if len({v['task_name'] for v in targets.values()}) != len(targets):
        raise SkillError('invalid_arguments', 'Target roles must resolve to different task bindings')
    result = {'recipe': entry['recipe'], 'targets': deepcopy(targets), 'revision': arguments['revision']}
    for section in ('parameters', 'constraints'):
        schema = deepcopy(contract[section])
        values = {k: deepcopy(v['default']) for k, v in schema['properties'].items() if 'default' in v}
        supplied = arguments.get(section, {})
        if not isinstance(supplied, dict):
            raise SkillError('invalid_arguments', section+' must be an object')
        values.update(supplied)
        validate(values, schema)
        result[section] = values
    return result


class RecipeContext:
    """Host-enforced movement permissions also cover edited recipe code."""
    def __init__(self, call, snapshot, constraints, *, notes_path=None):
        self._call, self._snapshot = call, snapshot
        self.constraints = constraints
        self.failed = False
        self.notes = None
        if notes_path is not None:
            from .task_notes import TaskNotes
            directory = Path(notes_path)
            directory.mkdir(parents=True, exist_ok=True)
            self.notes = TaskNotes(directory)
            if (directory / 'task_notes.json').exists():
                self.notes.state = json.loads((directory / 'task_notes.json').read_text())

    def call(self, name, **arguments):
        return self.invoke(name, arguments, str(uuid.uuid4()))

    def invoke(self, name, arguments, request_id):
        if self.failed:
            raise RuntimeError('Recipe stopped after its first failed child call')
        try:
            if name == 'task_notes' and self.notes is not None:
                if arguments.get('operation') == 'read':
                    if arguments.get('content') is not None or arguments.get('expected_version') is not None:
                        raise SkillError('invalid_arguments', 'Read recipe notes with null content/version')
                    return {'ok': True, 'notes': self.notes.read()}
                if arguments.get('operation') != 'write':
                    raise SkillError('invalid_arguments', 'Unknown recipe notes operation')
                notes = self.notes.write(arguments.get('content'), arguments.get('expected_version'),
                                         request_id=str(uuid.uuid4()),
                                         revision=(self._snapshot() or {}).get('revision'), agent_step=None)
                return {'ok': True, 'notes': notes}
            if name in TOOLS or name in {'finish', 'initialize'}:
                raise SkillError('invalid_recipe_call', 'No recursive recipes or lifecycle calls')
            if name == 'navigate_to':
                arms = (self._snapshot() or {}).get('oracle_3d', {}).get('robot', {}).get('arms', {})
                if (not self.constraints['allow_base_motion'] or not arms or
                        any(a.get('held_object') is not None for a in arms.values())):
                    raise SkillError('base_motion_forbidden', 'Navigate separately while empty; no held navigation')
            if not self.constraints['allow_trunk_motion'] and (
                    (name == 'move_joints' and arguments.get('group') == 'trunk') or arguments.get('allow_trunk')):
                raise SkillError('trunk_motion_forbidden', 'Recipe constraints prohibit trunk motion')
            reply = self._call(name, arguments, request_id)
            if reply.get('ok') is False:
                error = RuntimeError(json.dumps(reply, ensure_ascii=False))
                error.reply = reply
                raise error
            return reply
        except Exception:
            self.failed = True
            raise


def run_trusted(harness, library, arguments, request_id):
    """Only operator-frozen code runs in-process, on the simulator owner thread."""
    entry = library.get(arguments['recipe'])
    invocation = checked_invocation(entry, arguments)
    if invocation['revision'] != harness.revision:
        raise SkillError('stale_observation', 'Use the latest revision before Manip')
    from .oracle_presentation import OraclePresentation
    presentation = OraclePresentation()

    def call(name, kw, ident):
        presentation.remember({'observation': harness.snapshot})
        if name in {'get_oracle_geometry', 'set_oracle_focus'}:
            return presentation.query(name, {'revision': harness.revision, **kw})
        spec = harness.catalog.get(name)
        if spec is None:
            raise SkillError('unknown_tool', 'Recipe attempted a non-robot tool')
        if 'revision' in spec['inputSchema'].get('required', []):
            kw.setdefault('revision', harness.revision)
        harness.recorder.event('procedure_child', {'parent_request_id': request_id, 'request_id': ident})
        return harness.call(name, kw, ident, origin='manip_recipe')

    ctx = RecipeContext(call, lambda: harness.snapshot, invocation['constraints'],
                        notes_path=harness.recorder.output / 'manip_notes' / entry['recipe'])
    module_spec = importlib.util.spec_from_file_location('manip_'+uuid.uuid4().hex, entry['directory'] / 'controller.py')
    module = importlib.util.module_from_spec(module_spec)
    try:
        module_spec.loader.exec_module(module)
        result = module.Manip(ctx, **invocation)
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise ValueError('Manip must return a dict with boolean ok')
    except Exception as exc:
        result = {'ok': False, 'error': str(exc), 'phase': 'recipe_exception'}
    if ctx.failed:
        result['ok'] = False
    result.update(recipe=entry['recipe'], source_sha256=entry['source_sha256'],
                  validation='runtime checks only; independent evaluator required',
                  observation=deepcopy(harness.snapshot))
    harness.recorder.run['config']['motion_source'] = 'model_native_tools_plus_explicit_manip_recipes'
    from .records import write_json
    write_json(harness.recorder.output / 'run.json', harness.recorder.run)
    harness.recorder.event('manip_invocation', {'parent_request_id': request_id, 'arguments': arguments, 'result': result})
    return result
