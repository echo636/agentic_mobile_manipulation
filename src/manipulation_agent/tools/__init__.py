"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from dataclasses import replace
from .base import REGISTRY, ToolMetadata
from . import observation, action, planning, memory, skills, session, surround, notes
from copy import deepcopy
from .intent import INTENT_SCHEMA, PROTOCOL as INTENT_PROTOCOL

MINIMAL_TOOLS = frozenset({'initialize', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill', 'task_notes'}
INLINE_SKILL_TOOLS = MINIMAL_TOOLS | {'task_notes'}
PROFILE_TOOLS = {'minimal': MINIMAL_TOOLS, 'skills': SKILL_TOOLS, 'skills_inline': INLINE_SKILL_TOOLS}

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

def tool_specs(profile='skills', *, require_intent=False):
    if profile not in {*PROFILE_TOOLS, 'workflow'}:
        raise ValueError('Unknown agent profile')
    allowed = PROFILE_TOOLS.get(profile)
    result = []
    for t in REGISTRY.values():
        if profile != 'workflow' and t.name not in allowed: continue
        schema = deepcopy(t.schema)
        metadata = {'profile': profile, **t.metadata.as_dict()}
        description = t.description
        if require_intent:
            schema['properties']['intent'] = deepcopy(INTENT_SCHEMA)
            schema['required'].append('intent')
            metadata['tool_intent_protocol'] = INTENT_PROTOCOL
            description += ' Include intent stating the immediate purpose of this call.'
        result.append({'name':t.name,'description':description,'inputSchema':schema,
                       'annotations':t.metadata.annotations(),
                       '_meta':{'mas':metadata}})
    return result
