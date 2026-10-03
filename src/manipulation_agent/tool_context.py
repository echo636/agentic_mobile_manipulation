"""Episode-scoped tool capabilities, not a security sandbox.

Simulator access stays on the harness owner thread. Transports only queue RPCs.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolContext:
    harness: Any = field(repr=False)
    request_id: str
    tool_name: str

    @property
    def profile(self): return self.harness.profile
    @property
    def revision(self): return self.harness.revision
    @property
    def skills(self): return self.harness.skills
    @property
    def surround(self): return self.harness.surround

    def refresh(self): return self.harness.refresh()
    def perform(self, primitive, target, revision, **options):
        return self.harness.perform(primitive, target, revision, **options)
    def finish(self, outcome, reason): return self.harness.finish(outcome, reason)
    def update_plan(self, reason, subgoals): return self.harness.update_plan(reason, subgoals)
    def remember(self, key, text, revision): return self.harness.remember(key, text, revision)
    def recall(self): return self.harness.recall()
