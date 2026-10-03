"""One registry for model schemas and executable tool handlers."""
from dataclasses import asdict, dataclass, field
from typing import Callable

@dataclass(frozen=True)
class ToolMetadata:
    """Hints never replace validation. read_only means no world mutation.

    Observation updates bookkeeping/image refs and is therefore not idempotent.
    """
    read_only: bool = False
    mutates_world: bool = False
    requires_fresh_observation: bool = False
    idempotent: bool = False
    closes_episode: bool = False

    def annotations(self):
        return {'readOnlyHint': self.read_only,
                'destructiveHint': self.mutates_world or self.closes_episode,
                'idempotentHint': self.idempotent, 'openWorldHint': False}

    def as_dict(self): return asdict(self)

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable
    metadata: ToolMetadata = field(default_factory=ToolMetadata)

REGISTRY: dict[str, Tool] = {}

def register(name, description, schema, *, metadata=None):
    def decorate(handler):
        if name in REGISTRY:
            raise ValueError(f"Duplicate tool {name}")
        REGISTRY[name] = Tool(name, description, schema, handler,
                              metadata if metadata is not None else ToolMetadata())
        return handler
    return decorate
