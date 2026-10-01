"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from .base import REGISTRY
from . import observation, action, planning, memory, skills, session, surround
from copy import deepcopy

MINIMAL_TOOLS = frozenset({'observe', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill', 'start_observation', 'get_observation', 'cancel_observation'}
OFFICIAL_TOOLS = frozenset({'observe','start_observation','get_observation','cancel_observation','act','finish'})

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
                'NAVIGATE_TO may fail because the pinned upstream symbolic implementation lacks its CuRobo planner. '
                'For soak_under/soak_inside select the fluid source/container while holding the item; wipe/cut select the target '
                'while holding the appropriate tool; place_near_heating_element selects the heat source while holding the item. '
                'Tool completion is not whole-task success.')
        result.append({'name':t.name,'description':description,'inputSchema':schema})
    return result
