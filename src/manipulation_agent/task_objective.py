"""Benchmark-side scoring objective appended verbatim to every task instruction.

Added 2026-11-15 (benchmark side, identical for every tier, task and method): the
public instruction used to say only what to do, so models without a scheduling
facility were never told that robot time is scored. The sentence states the
objective only; ordering, batching or overlapping strategies stay in method arms.
"""
from __future__ import annotations

SCORING_OBJECTIVE = (
    'Scoring: completing every requirement of the task comes first. Among ways to '
    'complete it, a shorter total robot time (travel + manipulation + waiting) is better.'
)


def with_scoring_objective(instruction: str) -> str:
    text = (instruction or '').rstrip()
    if SCORING_OBJECTIVE in text:
        return text
    return f'{text}\n\n{SCORING_OBJECTIVE}' if text else SCORING_OBJECTIVE
