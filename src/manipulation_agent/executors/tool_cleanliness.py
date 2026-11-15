"""Benchmark-side tool cleanliness tracker for monotonic wiping order and decontamination reset.

When a custom task declares ``tool_cleanliness``, the cleanliness level of surfaces
and the dirtiness of the cleaning tool are tracked across 3 cleanliness levels:
  - Level 1 (High cleanliness): dining table, prep counter
  - Level 2 (Standard cleanliness): bar counter, cabinet shelf
  - Level 3 (Heavy stain): stove top, waste surround

Tool dirtiness D in {0, 1, 2, 3}, initial D = 0.
- ``record_wipe(surface_level: int)``:
    requires D <= surface_level. If D > surface_level, marks violation = True (cross-contamination).
    updates D <- max(D, surface_level).
- ``record_soak(target: str)``:
    if target is a water source (e.g. 'sink'), resets D <- 0.
- ``has_violation() -> bool``:
    returns whether cross-contamination occurred.
- ``to_dict() -> dict``:
    exports current state for inspection and logging.

CPU-only schema validation ``validate_tool_cleanliness(spec)`` runs without OmniGibson.
"""
from __future__ import annotations

from typing import Any

VERSION = 'tool_cleanliness_v2_factual_receipt'
VALID_SURFACE_LEVELS = frozenset({1, 2, 3})
LEVEL_STATES = {
    0: 'clean',
    1: 'tier_1_light_dust',
    2: 'tier_2_moderate_dust',
    3: 'tier_3_heavy_stain',
}


def validate_tool_cleanliness(spec: dict[str, Any]) -> None:
    """CPU-only contract validation for optional ``tool_cleanliness`` in custom specs."""
    cfg = spec.get('tool_cleanliness')
    if cfg is None:
        return
    if not isinstance(cfg, dict):
        raise ValueError('tool_cleanliness must be a dict when present')

    roles = spec.get('roles', {})

    # Validate tool: can be 'tool' (single role) or 'tools' (list of roles)
    tool = cfg.get('tool')
    tools = cfg.get('tools')
    if tool is None and tools is None:
        raise ValueError('tool_cleanliness must declare "tool" or "tools"')
    if tool is not None:
        if not isinstance(tool, str) or tool not in roles:
            raise ValueError(f'tool_cleanliness "tool" must be a valid declared role: {tool!r}')
    if tools is not None:
        if not isinstance(tools, list) or not tools or any(not isinstance(t, str) or t not in roles for t in tools):
            raise ValueError(f'tool_cleanliness "tools" must be a nonempty list of valid declared roles: {tools!r}')

    # Validate water_sources
    water_sources = cfg.get('water_sources')
    if (not isinstance(water_sources, list) or not water_sources
            or any(not isinstance(ws, str) or ws not in roles for ws in water_sources)):
        raise ValueError('tool_cleanliness "water_sources" must be a nonempty list of declared roles')

    # Validate surface_levels
    surface_levels = cfg.get('surface_levels')
    if not isinstance(surface_levels, dict) or not surface_levels:
        raise ValueError('tool_cleanliness "surface_levels" must be a nonempty mapping from role to level')
    for surface, level in surface_levels.items():
        if surface not in roles:
            raise ValueError(f'tool_cleanliness surface_levels references unknown role: {surface!r}')
        if level not in VALID_SURFACE_LEVELS:
            raise ValueError(f'tool_cleanliness surface {surface!r} has invalid level {level!r}, must be 1, 2, or 3')


class ToolCleanlinessTracker:
    """Tracks dirtiness of cleaning tools across 3 monotonic cleanliness levels."""

    def __init__(
        self,
        tools: set[str],
        water_sources: set[str],
        surface_levels: dict[str, int],
        initial_dirtiness: int = 0,
    ):
        self.tools = set(tools)
        self.water_sources = set(water_sources)
        self.surface_levels = dict(surface_levels)
        self.dirtiness = int(initial_dirtiness)
        self.violation = False
        self.wipe_count = 0
        self.soak_count = 0
        self.history: list[dict[str, Any]] = []

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> ToolCleanlinessTracker | None:
        cfg = spec.get('tool_cleanliness')
        if cfg is None:
            return None
        tools = set()
        if 'tool' in cfg and cfg['tool']:
            tools.add(cfg['tool'])
        if 'tools' in cfg and cfg['tools']:
            tools.update(cfg['tools'])
        water_sources = set(cfg.get('water_sources', []))
        surface_levels = {k: int(v) for k, v in cfg.get('surface_levels', {}).items()}
        initial_dirtiness = int(cfg.get('initial_dirtiness', 0))
        return cls(
            tools=tools,
            water_sources=water_sources,
            surface_levels=surface_levels,
            initial_dirtiness=initial_dirtiness,
        )

    def is_water_source(self, target: str) -> bool:
        return target in self.water_sources

    def get_surface_level(self, surface: str) -> int | None:
        return self.surface_levels.get(surface)

    def record_wipe(self, surface_level: int, surface: str | None = None) -> bool:
        """Records wiping a surface of given cleanliness level.

        Requires D <= surface_level. If D > surface_level, marks violation = True.
        Updates D <- max(D, surface_level).
        Returns True if action was compliant (no violation triggered), False if violation occurred.
        """
        self.wipe_count += 1
        d_before = self.dirtiness
        if self.dirtiness > surface_level:
            self.violation = True
            compliant = False
        else:
            compliant = True
        self.dirtiness = max(self.dirtiness, surface_level)
        self.history.append({
            'action': 'wipe',
            'surface': surface,
            'surface_level': surface_level,
            'dirtiness_before': d_before,
            'dirtiness_after': self.dirtiness,
            'violation': self.violation,
            'compliant': compliant,
        })
        return compliant

    def record_soak(self, target: str) -> bool:
        """Records soaking in target. If target is a water source, resets D <- 0.

        Returns True if target was a water source and reset occurred, False otherwise.
        """
        self.soak_count += 1
        d_before = self.dirtiness
        is_water = self.is_water_source(target)
        if is_water:
            self.dirtiness = 0
        self.history.append({
            'action': 'soak',
            'target': target,
            'dirtiness_before': d_before,
            'dirtiness_after': self.dirtiness,
            'is_water_source': is_water,
            'violation': self.violation,
        })
        return is_water

    def has_violation(self) -> bool:
        return bool(self.violation)

    def public_receipt(self, event: str | None = None, *, compliant: bool | None = None) -> dict[str, Any]:
        """Benchmark-side factual receipt for the held cleaning tool's physical stain state."""
        state_label = LEVEL_STATES.get(self.dirtiness, f'level_{self.dirtiness}')
        if event == 'soak_reset':
            msg = 'Held cleaning tool was rinsed in water and is now clean (stain level 0).'
        elif compliant is False:
            msg = (
                'Cross-contamination occurred: wiped surface had a lower stain level than the held '
                f'cleaning tool. Held tool stain level is {self.dirtiness} ({state_label}).'
            )
        else:
            msg = f'Held cleaning tool stain state: level {self.dirtiness} ({state_label}).'
        return {
            'tool_dirtiness_level': int(self.dirtiness),
            'tool_state': state_label,
            'cross_contamination_occurred': bool(self.violation),
            'message': msg,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            'dirtiness': self.dirtiness,
            'violation': self.violation,
            'wipe_count': self.wipe_count,
            'soak_count': self.soak_count,
            'history': list(self.history),
        }
