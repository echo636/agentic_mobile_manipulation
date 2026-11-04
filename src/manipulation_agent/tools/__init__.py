"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from dataclasses import replace
from .base import REGISTRY, ToolMetadata
from . import observation, action, planning, memory, skills, session, surround, notes
from copy import deepcopy

MINIMAL_TOOLS = frozenset({'initialize', 'look', 'act', 'finish'})
BASIC_SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill'}
SKILL_TOOLS = BASIC_SKILL_TOOLS | {'task_notes'}

# Execution hints, not additional permission gates or changed input contracts.
_METADATA = {
    'initialize': ToolMetadata(read_only=True, idempotent=True),
    'observe': ToolMetadata(read_only=True),
    'look': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
    'act': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
    'finish': ToolMetadata(closes_episode=True),
    'list_skills': ToolMetadata(read_only=True, idempotent=True),
    'read_skill': ToolMetadata(read_only=True, idempotent=True),
    'task_notes': ToolMetadata(),
    'start_observation': ToolMetadata(read_only=True),
    'get_observation': ToolMetadata(read_only=True),
    'cancel_observation': ToolMetadata(read_only=True),
    'update_plan': ToolMetadata(),
    'remember': ToolMetadata(requires_fresh_observation=True),
    'recall': ToolMetadata(read_only=True, idempotent=True),
}
for _name, _metadata in _METADATA.items():
    if _name in REGISTRY:
        REGISTRY[_name] = replace(REGISTRY[_name], metadata=_metadata)

def tool_specs(profile='skills'):
    if profile not in {'minimal', 'basic-skills', 'goal-tree', 'skills', 'workflow'}:
        raise ValueError('Unknown agent profile')
    allowed = {'minimal': MINIMAL_TOOLS, 'basic-skills': BASIC_SKILL_TOOLS}.get(profile, SKILL_TOOLS)
    result = []
    for t in REGISTRY.values():
        if profile != 'workflow' and t.name not in allowed: continue
        schema = deepcopy(t.schema)
        description = t.description
        if profile == 'goal-tree' and t.name == 'task_notes':
            schema['properties']['operation']['enum'] = ['read', 'write', 'update']
            description = ('Store the model-authored goal tree and progress. read returns notes and recent public action receipts; '
                'write replaces the JSON text; update merges goals by id and replaces other supplied fields. '
                'Omitted goals persist; supply active when updating the current local plan. '
                'Use the current expected_version for writes/updates, initially 0. For read, content and expected_version are null. '
                'Notes do not execute actions, choose goals, search plans or verify task success.')
        result.append({'name':t.name,'description':description,'inputSchema':schema,
                       'annotations':t.metadata.annotations(),
                       '_meta':{'mas':{'profile':profile,**t.metadata.as_dict()}}})
    return result
