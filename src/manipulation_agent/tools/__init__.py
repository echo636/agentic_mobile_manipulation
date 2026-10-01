"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from .base import REGISTRY
from . import observation, action, planning, memory, skills, session, surround, motor
from copy import deepcopy

MINIMAL_TOOLS = frozenset({'observe', 'look', 'act', 'finish'})
SKILL_TOOLS = MINIMAL_TOOLS | {'list_skills', 'read_skill', 'start_observation', 'get_observation', 'cancel_observation'}
MOTOR_TOOLS = frozenset({'observe','start_observation','get_observation','cancel_observation','describe_controls','execute_code','finish'})

def tool_specs(profile='skills'):
    if profile not in {'minimal', 'skills', 'workflow', 'motor'}:
        raise ValueError('Unknown agent profile')
    allowed = MINIMAL_TOOLS if profile == 'minimal' else MOTOR_TOOLS if profile == 'motor' else SKILL_TOOLS
    result = []
    for t in REGISTRY.values():
        if profile != 'workflow' and t.name not in allowed: continue
        if profile == 'workflow' and t.name in {'describe_controls','execute_code'}: continue
        schema = deepcopy(t.schema)
        result.append({'name':t.name,'description':t.description,'inputSchema':schema})
    return result
