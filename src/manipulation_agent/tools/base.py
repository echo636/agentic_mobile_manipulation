"""One registry for model schemas and executable tool handlers."""
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable

REGISTRY: dict[str, Tool] = {}

def register(name, description, schema):
    def decorate(handler):
        if name in REGISTRY:
            raise ValueError(f"Duplicate tool {name}")
        REGISTRY[name] = Tool(name, description, schema, handler)
        return handler
    return decorate
