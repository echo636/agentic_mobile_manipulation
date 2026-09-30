"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from .base import REGISTRY
from . import observation, action, planning, memory, skills, session

MINIMAL_TOOLS = frozenset({'observe', 'look', 'act', 'finish'})

def tool_specs(profile='minimal'):
    if profile not in {'minimal', 'workflow'}:
        raise ValueError('Unknown agent profile')
    return [{"name":t.name,"description":t.description,"inputSchema":t.schema} for t in REGISTRY.values()
            if profile == 'workflow' or t.name in MINIMAL_TOOLS]
