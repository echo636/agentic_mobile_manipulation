"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from dataclasses import replace
from .base import REGISTRY, ToolMetadata
from . import observation, action, planning, memory, skills, session, surround
from copy import deepcopy

MINIMAL_TOOLS = frozenset({'observe', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill', 'start_observation', 'get_observation', 'cancel_observation'}

# Execution hints, not additional permission gates or changed input contracts.
_METADATA = {
    'observe': ToolMetadata(read_only=True),
    'look': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
    'act': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
    'finish': ToolMetadata(closes_episode=True),
    'list_skills': ToolMetadata(read_only=True, idempotent=True),
    'read_skill': ToolMetadata(read_only=True, idempotent=True),
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
    if profile not in {'minimal', 'skills', 'workflow'}:
        raise ValueError('Unknown agent profile')
    allowed = MINIMAL_TOOLS if profile == 'minimal' else SKILL_TOOLS
    result = []
    for t in REGISTRY.values():
        if profile != 'workflow' and t.name not in allowed: continue
        schema = deepcopy(t.schema)
        result.append({'name':t.name,'description':t.description,'inputSchema':schema,
                       'annotations':t.metadata.annotations(),
                       '_meta':{'mas':{'profile':profile,**t.metadata.as_dict()}}})
    return result
