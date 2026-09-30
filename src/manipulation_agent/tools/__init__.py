"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from .base import REGISTRY
from . import observation, action, planning, memory, skills, session
from copy import deepcopy
from ..contracts import object_schema

MINIMAL_TOOLS = frozenset({'observe', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill'}
DECISION_SCHEMA = object_schema({
    'observation': {'type':'string', 'minLength':1, 'maxLength':280, 'description':'One short description of visible evidence or uncertainty.'},
    'reason': {'type':'string', 'minLength':1, 'maxLength':280, 'description':'One short public explanation of the immediate action, not private reasoning.'},
    'expected': {'type':'string', 'minLength':1, 'maxLength':280, 'description':'The observable change to check after this action.'}})

def tool_specs(profile='skills'):
    if profile not in {'minimal', 'skills', 'workflow'}:
        raise ValueError('Unknown agent profile')
    allowed = MINIMAL_TOOLS if profile == 'minimal' else SKILL_TOOLS
    result = []
    for t in REGISTRY.values():
        if profile != 'workflow' and t.name not in allowed: continue
        schema = deepcopy(t.schema)
        if profile == 'skills' and t.name in {'look','act'}:
            schema['properties']['decision'] = deepcopy(DECISION_SCHEMA)
            schema['required'].append('decision')
        result.append({'name':t.name,'description':t.description,'inputSchema':schema})
    return result
