"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from dataclasses import replace
from .base import REGISTRY, ToolMetadata
from . import observation, action, planning, memory, skills, session, surround
from copy import deepcopy

MINIMAL_TOOLS = frozenset({'observe', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill', 'start_observation', 'get_observation', 'cancel_observation'}
OFFICIAL_TOOLS = frozenset({'observe','start_observation','get_observation','cancel_observation','act','finish'})

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
    if profile not in {'minimal', 'skills', 'workflow', 'official'}:
        raise ValueError('Unknown agent profile')
    allowed = MINIMAL_TOOLS if profile == 'minimal' else SKILL_TOOLS
    if profile == 'official': allowed = OFFICIAL_TOOLS
    result = []
    for t in REGISTRY.values():
        if profile != 'workflow' and t.name not in allowed: continue
        schema = deepcopy(t.schema)
        description = t.description
        if profile == 'official' and t.name == 'act':
            from ..executors.official_symbolic import OFFICIAL_PRIMITIVES
            schema['properties'] = {k:v for k,v in schema['properties'].items() if k in {'primitive','target','revision'}}
            schema['properties']['primitive']['enum'] = list(OFFICIAL_PRIMITIVES)
            schema['required'] = ['primitive','target','revision']
            description = ('Call one official OmniGibson symbolic primitive. Select the object in current RGB with '
                'target={image_ref,point:[x,y]} (normalized left-to-right, top-to-bottom); only release takes null. '
                'For placement, the pixel selects the destination OBJECT, not the exact surface or placement point. '
                'Upstream can directly change states/poses and uses its own preconditions; there is no project auto-approach, '
                'GT navigator, placement fallback or rollback. One upstream attempt. Inspect fresh RGB after success or failure. '
                'NAVIGATE_TO uses an initialized native planner to check candidate base poses; sampling can still fail. '
                'For soak_under/soak_inside select the fluid source/container while holding the item; wipe/cut select the target '
                'while holding the appropriate tool; place_near_heating_element selects the heat source while holding the item. '
                'Tool completion is not whole-task success.')
        result.append({'name':t.name,'description':description,'inputSchema':schema,
                       'annotations':t.metadata.annotations(),
                       '_meta':{'mas':{'profile':profile,**t.metadata.as_dict()}}})
    return result
