"""Benchmark-side scoring objective: identical for every tier, objective only, no strategy."""
from pathlib import Path
import re

from manipulation_agent.task_objective import SCORING_OBJECTIVE, with_scoring_objective

REPO = Path(__file__).resolve().parents[1]


def test_objective_appended_once_and_task_text_kept():
    task = 'Prepare lunch for two.\nServe it on the table.'
    out = with_scoring_objective(task)
    assert out.startswith(task) and out.endswith(SCORING_OBJECTIVE)
    assert with_scoring_objective(out) == out


def test_objective_states_goal_not_strategy():
    banned = r'\b(parallel|overlap|batch|tray|carrier|while|meanwhile|order|first do|combine|trip|plan)\b'
    assert re.search(banned, SCORING_OBJECTIVE, re.IGNORECASE) is None
    assert 'robot time' in SCORING_OBJECTIVE and 'completing every requirement' in SCORING_OBJECTIVE


def test_controller_applies_objective_before_any_client_or_handoff_use():
    src = (REPO / 'scripts/run_codex_controller.py').read_text()
    applied = src.index('args.instruction = with_scoring_objective(args.instruction)')
    for consumer in ('system_prompt(args.agent_profile', 'ClientConfig(client, args.model, args.instruction',
                     'build_handoff_context('):
        assert applied < src.index(consumer)
