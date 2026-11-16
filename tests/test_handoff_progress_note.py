"""Harness option: factual handoff note at rolling checkpoints (off by default)."""
from pathlib import Path
import re
import sys

from manipulation_agent.agent.state_memory import StateMemory
from manipulation_agent.mcp_server import HANDOFF_NOTE_REQUEST

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import run_codex_controller as controller  # noqa: E402


def test_handoff_context_unchanged_without_note_and_verbatim_with_note():
    mem = StateMemory()
    base = mem.build_handoff_context('Do the task.', segment_index=1, steps_remaining=220)
    assert 'Handoff note' not in base
    assert mem.build_handoff_context('Do the task.', segment_index=1, steps_remaining=220,
                                     progress_note='  ') == base
    note = 'Done: drawer closed with the pencil case inside (loc_2).\nRemaining: dry both garments.'
    out = mem.build_handoff_context('Do the task.', segment_index=1, steps_remaining=220, progress_note=note)
    assert note in out
    assert out.index(note) < out.index('Next actions:') < out.index('Original Task Instruction:')


def test_last_agent_message_takes_final_nonempty_text_and_caps_length():
    events = [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'first'}},
        {'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'tool': 'act'}},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'x' * 9000}},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '   '}},
        {'type': 'turn.completed'},
    ]
    assert controller.last_agent_message(events) == 'x' * controller.HANDOFF_NOTE_MAX_CHARS
    assert controller.last_agent_message([]) is None


def test_note_request_asks_for_facts_not_strategy():
    banned = r'\b(parallel|overlap|batch|tray|carrier|while|meanwhile|order|first do|combine|trip|plan|faster|efficient)\b'
    assert re.search(banned, HANDOFF_NOTE_REQUEST, re.IGNORECASE) is None
    for required in ('already completed', 'not yet completed', 'described visually', 'holding'):
        assert required in HANDOFF_NOTE_REQUEST


def test_option_is_wired_off_by_default_through_controller_and_server():
    ctrl = (REPO / 'scripts/run_codex_controller.py').read_text()
    server = (REPO / 'src/manipulation_agent/mcp_server.py').read_text()
    assert "p.add_argument('--handoff-progress-note', choices=['off','on'], default='off'" in ctrl
    assert "'--handoff-progress-note', 'on'" in ctrl
    assert "if args.handoff_progress_note == 'on' else None" in ctrl
    assert "parser.add_argument('--handoff-progress-note', choices=('off', 'on'), default='off'" in server
    assert server.count("(HANDOFF_NOTE_REQUEST if handoff_note else '')") == 2
