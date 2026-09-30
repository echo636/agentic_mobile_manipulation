"""Public RGB agent tools. Historical oracle contracts are not this catalog."""
from .base import REGISTRY
from . import observation, action, planning, memory, skills, session

def tool_specs():
    return [{"name":t.name,"description":t.description,"inputSchema":t.schema} for t in REGISTRY.values()]
