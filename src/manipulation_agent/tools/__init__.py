"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from dataclasses import replace
from .base import REGISTRY, ToolMetadata
from . import observation, action, planning, memory, skills, session, surround
from copy import deepcopy
import os

MINIMAL_TOOLS = frozenset({'initialize', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill'}
PLANNING_TOOLS = SKILL_TOOLS | {'update_plan', 'remember', 'recall'}
PLANNING_NO_SKILLS_TOOLS = PLANNING_TOOLS - {'list_skills', 'read_skill'}
PLANNING_PROFILES = frozenset({'planning', 'planning_no_skills'})
SEMANTIC_TOOLS = frozenset({'initialize', 'act_semantic', 'finish'})

DECISION = {'type':'object','properties':{
    key:{'type':'string','minLength':1,'maxLength':300}
    for key in ('state','active_subgoal','reason','expected')},
    'required':['state','active_subgoal','reason','expected'],
    'additionalProperties':False}

# Execution hints, not additional permission gates or changed input contracts.
_METADATA = {
    'initialize': ToolMetadata(read_only=True, idempotent=True),
    'observe': ToolMetadata(read_only=True),
    'look': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
    'act': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
    'act_semantic': ToolMetadata(mutates_world=True, requires_fresh_observation=True),
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
    if profile not in {'minimal', 'skills', 'planning', 'planning_no_skills', 'workflow', 'semantic'}:
        raise ValueError('Unknown agent profile')
    allowed = (SEMANTIC_TOOLS if profile == 'semantic' else
               MINIMAL_TOOLS if profile == 'minimal' else
               PLANNING_TOOLS if profile == 'planning' else
               PLANNING_NO_SKILLS_TOOLS if profile == 'planning_no_skills' else SKILL_TOOLS)
    result = []
    for t in REGISTRY.values():
        if (profile != 'workflow' and t.name not in allowed) or (profile == 'workflow' and t.name == 'act_semantic'):
            continue
        schema = deepcopy(t.schema)
        if profile in PLANNING_PROFILES or (profile == 'skills' and os.environ.get('MAS_REQUIRE_DECISION') == '1'):
            schema['properties']['decision'] = deepcopy(DECISION)
            if profile not in PLANNING_PROFILES:
                schema['properties']['decision']['properties'].pop('active_subgoal')
                schema['properties']['decision']['required'].remove('active_subgoal')
            schema['required'].append('decision')
        result.append({'name':t.name,'description':t.description,'inputSchema':schema,
                       'annotations':t.metadata.annotations(),
                       '_meta':{'mas':{'profile':profile,**t.metadata.as_dict()}}})
    return result
