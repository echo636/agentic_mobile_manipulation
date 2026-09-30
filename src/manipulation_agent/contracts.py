from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol
import math


SKILLS = (
    "navigate_to", "grasp", "place_inside", "place_on_top", "open", "close",
    "toggle_on", "toggle_off", "release", "wait",
)


@dataclass(frozen=True)
class Budget:
    max_actions: int = 80
    max_calls: int = 240
    max_sim_steps: int = 20000
    max_steps_per_action: int = 700
    wall_seconds: float = 3600

    def __post_init__(self):
        for value in asdict(self).values():
            if isinstance(value, bool) or value <= 0:
                raise ValueError("Budgets must be positive")


class SkillError(Exception):
    def __init__(self, code: str, message: str, *, changed: bool = False):
        super().__init__(message)
        self.code = code
        self.changed = changed


class Backend(Protocol):
    mode: str
    steps: int

    def observe(self) -> dict: ...
    def execute(self, skill: str, target: str | None, max_steps: int) -> dict: ...
    def evaluate(self) -> dict: ...
    def provenance(self) -> dict: ...
    def close(self) -> None: ...


def object_schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


STRING = {"type": "string"}
INT = {"type": "integer", "minimum": 0}
NULL_STRING = {"type": ["string", "null"]}


def tool_specs() -> list[dict]:
    """The model, MCP proxy and local client share one catalog."""
    rows = [
        ("observe", "Read current task objects and robot state. Does not advance physics.", {}),
        ("act", "Execute one bounded skill using the latest observation revision; failures may change the world.", {
            "skill": {"type": "string", "enum": list(SKILLS)}, "target": NULL_STRING,
            "revision": INT}),
        ("update_plan", "Replace the revisable subgoal plan. Done claims need a successful action evidence ID; they remain agent claims.", {
            "reason": STRING, "subgoals": {"type": "array", "maxItems": 40, "items": object_schema({
                "id": STRING, "description": STRING,
                "dependencies": {"type": "array", "items": STRING},
                "status": {"type": "string", "enum": ["pending", "active", "done", "blocked"]},
                "evidence": NULL_STRING})}}),
        ("remember", "Store an episode-local observation or hypothesis. Memory is not evaluator truth.", {
            "key": STRING, "text": STRING, "revision": INT}),
        ("recall", "Read the current plan and bounded episode memory.", {}),
        ("finish", "Close the episode with a completion claim. Independent BDDL evaluation determines actual task success.", {
            "outcome": {"type": "string", "enum": ["achieved", "blocked", "aborted"]},
            "reason": STRING}),
    ]
    return [{"name": name, "description": desc, "inputSchema": object_schema(props)}
            for name, desc, props in rows]


def validate(value, schema: dict, path: str = "arguments") -> None:
    """Validate our small JSON Schema subset without adding a simulator dependency."""
    kind = schema.get("type")
    if isinstance(kind, list):
        if value is None and "null" in kind:
            return
        kind = next(k for k in kind if k != "null")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value)}.get(kind, False)
    if not valid:
        raise SkillError("invalid_arguments", f"{path} must be {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise SkillError("invalid_arguments", f"{path} is not an allowed value")
    if kind == "object":
        props = schema["properties"]
        if set(value) != set(props):
            raise SkillError("invalid_arguments", f"{path} needs exactly {sorted(props)}")
        for k, v in value.items():
            validate(v, props[k], f"{path}.{k}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise SkillError("invalid_arguments", f"{path} is too short")
        if len(value) > schema.get("maxItems", 200):
            raise SkillError("invalid_arguments", f"{path} is too long")
        for i, v in enumerate(value):
            validate(v, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if len(value) > schema.get('maxLength',8000) or len(value) < schema.get('minLength',0):
            raise SkillError("invalid_arguments", f"{path} has invalid length")
    elif kind in {"integer", "number"}:
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise SkillError("invalid_arguments", f"{path} is outside the allowed range")
