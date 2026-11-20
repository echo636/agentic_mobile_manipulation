"""Pluggable CLI policies sharing setup, execution budgets and public event records."""
from .codex import CodexAdapter
from .opencode import OpenCodeAdapter
from .kimi import KimiAdapter
from .types import ClientConfig, ClientCapability, PreparedProject, ClientRunResult, CanonicalEvent


def get_adapter(name: str):
    adapters = {"codex": CodexAdapter, "opencode": OpenCodeAdapter, "kimi": KimiAdapter}
    if name not in adapters:
        raise ValueError(f"Unsupported client: {name}")
    return adapters[name]()
