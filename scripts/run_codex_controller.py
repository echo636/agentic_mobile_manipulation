"""Run the installed model client against a project MCP endpoint using its existing login.

This is a policy under evaluation, not a coding worker. Shell, plugins, other agents,
web, browser and filesystem image tools are disabled. No authentication file is copied.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import shutil
import signal
import sys
import itertools
import re
import tomllib
from dataclasses import asdict

_SRC_ROOT = str(Path(__file__).resolve().parents[1] / 'src')
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from manipulation_agent.task_objective import with_scoring_objective
from manipulation_agent.clients import ClientConfig, get_adapter
from manipulation_agent.clients.events import write_derived_events
from manipulation_agent.agent.temporal_graph import DISPATCH_POLICIES

from manipulation_agent.tools import tool_specs
from manipulation_agent.tools.intent import PROTOCOL as INTENT_PROTOCOL, LEGACY_PROTOCOL
from manipulation_agent.agent.vision_policy import system_prompt
from manipulation_agent.agent.state_memory import StateMemory
from manipulation_agent.agent.planning_subagent import ACTOR_INSTRUCTION, PLANNER_TOOL
from manipulation_agent.records import now, write_json, source_version
from manipulation_agent.reporting.model_trace import export_summaries
from manipulation_agent.transport.mcp_preflight import check_server
from manipulation_agent.runtime.deadline import EpisodeDeadline, validate_execution_clock


def rate_limit_error(events):
    """Resume only after the latest terminal provider failure is transient."""
    terminal = next((event for event in reversed(events)
                     if event.get('type') in {'error', 'turn.failed', 'turn.completed'}), None)
    if terminal is None or terminal.get('type') != 'turn.failed':
        return False
    message = str(terminal.get('message') or terminal.get('error') or '')
    if any(marker in message for marker in ('PERMANENT_', '401 Unauthorized', '403 Forbidden',
                                            'refresh token was revoked')):
        return False
    return (bool(re.search(r'\b(?:429 Too Many Requests|(?:status|HTTP) (?:429|500|502|503|504)\b)', message))
            or 'API_KEY_CONCURRENCY_QUEUE_' in message
            or 'selected model is at capacity' in message.lower()
            or 'overloaded' in message.lower()
            or 'rate limit' in message.lower()
            or 'resource_exhausted' in message.lower()
            or 'stream disconnected before completion' in message)


# Harness option (--unfinished-turn-nudges, default 0 = off): a turn that completed normally without an accepted
# finish is continued in the same thread with a neutral reminder. Counterpart of tools/finish_guard.py: after a
# deferred finish the model may simply end its turn, which exits the client and leaves the episode open.
NUDGE_PROMPT = ('The episode is still open: your previous turn ended without an accepted finish call, and the '
                'simulator is waiting for your next tool call. Continue the instruction from the current state with '
                'tool calls. Call finish only when the whole instruction is done, or when an obstacle persists after '
                'materially different attempts.')


def _receipt_dicts_in_event(event):
    item = (event.get('item') or {}) if isinstance(event, dict) else {}
    res = item.get('result') if isinstance(item.get('result'), dict) else {}
    out = []
    for block in (res.get('content') or []):
        if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str):
            try:
                val = json.loads(block['text'])
                if isinstance(val, dict):
                    out.append(val)
            except ValueError:
                pass
    return out


def segment_robot_calls(events):
    """Completed step-charging act/look MCP calls in one client segment."""
    count = 0
    for event in events:
        if (event.get('type') != 'item.completed'
                or (event.get('item') or {}).get('type') != 'mcp_tool_call'
                or (event.get('item') or {}).get('tool') not in {'act', 'look'}):
            continue
        receipts = _receipt_dicts_in_event(event)
        if any((r.get('error') or {}).get('action_step_used') is False for r in receipts if isinstance(r.get('error'), dict)):
            continue
        count += 1
    return count


def saw_rolling_handoff_checkpoint(events):
    for event in events:
        if event.get('type') != 'item.completed':
            continue
        for r in _receipt_dicts_in_event(event):
            if (r.get('rolling_handoff') or {}).get('due') is True:
                return True
            if isinstance(r.get('error'), dict) and r['error'].get('code') == 'rolling_handoff_checkpoint':
                return True
    return False


HANDOFF_NOTE_MAX_CHARS = 4000


def last_agent_message(events):
    """The segment's final model-written text, as it appeared in the public transcript."""
    for event in reversed(events):
        item = event.get('item') if isinstance(event, dict) else None
        if (event.get('type') == 'item.completed' and isinstance(item, dict)
                and item.get('type') == 'agent_message' and str(item.get('text') or '').strip()):
            return str(item['text']).strip()[:HANDOFF_NOTE_MAX_CHARS]
    return None


def latest_steps_remaining(events):
    for event in reversed(events):
        if event.get('type') != 'item.completed':
            continue
        for r in _receipt_dicts_in_event(event):
            budget = r.get('execution_budget')
            if isinstance(budget, dict) and isinstance(budget.get('steps_remaining'), int):
                return budget['steps_remaining']
    return None


def rolling_handoff_due(events, *, returncode, finish_received, timed_out, rolling_handoff_steps,
                        cleanup_reason=None, handoffs=0, max_handoffs=40):
    """True when the segment reached its rolling step window without finishing the episode."""
    if rolling_handoff_steps <= 0 or finish_received or timed_out or handoffs >= max_handoffs:
        return False
    if cleanup_reason == 'rolling_handoff_step_limit':
        return True
    return returncode == 0 and (
        segment_robot_calls(events) >= rolling_handoff_steps
        or saw_rolling_handoff_checkpoint(events)
    )


def turn_completed_without_error(events):
    terminal = next((event for event in reversed(events)
                     if event.get('type') in {'error', 'turn.failed', 'turn.completed'}), None)
    return terminal is not None and terminal.get('type') == 'turn.completed'


def unfinished_turn_nudge(events, *, returncode, finish_received, timed_out, nudges, max_nudges):
    """True when a normally completed turn left the episode open and a reminder is still allowed.

    `nudges` counts consecutive reminders whose resumed segment made no act/look call.
    """
    return (max_nudges > 0 and returncode == 0 and not finish_received and not timed_out
            and nudges < max_nudges and turn_completed_without_error(events))


def codex_thread_id(events):
    return next((event.get('thread_id') for event in events
                 if event.get('type') == 'thread.started' and event.get('thread_id')), None)


def session_thread_id(events, client: str):
    if client == 'codex':
        return codex_thread_id(events)
    if client == 'claude':
        return next((event.get('session_id') for event in events
                     if isinstance(event, dict) and event.get('session_id')), None)
    if client == 'agy':
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get('conversation_id'):
                return event['conversation_id']
            su = event.get('step_update') or event.get('result') or {}
            if isinstance(su, dict) and su.get('conversation_id'):
                return su['conversation_id']
    return None


def provider_recovery_mode(events, *, resumes, forks, max_resumes, max_forks):
    """Only the observed unavailable sticky route may fork; auth failures cannot."""
    terminal = next((event for event in reversed(events)
                     if event.get('type') in {'error', 'turn.failed', 'turn.completed'}), None)
    if not terminal or terminal.get('type') != 'turn.failed':
        return None
    message = str(terminal.get('message') or terminal.get('error') or '')
    if (forks < max_forks and 'OPENAI_PERMANENT_STICKY_ACCOUNT_UNAVAILABLE' in message
            and not any(value in message for value in ('401 Unauthorized', '403 Forbidden',
                                                       'refresh token was revoked'))):
        pending = {}
        for event in events:
            item = event.get('item') or {}
            if item.get('type') != 'mcp_tool_call':
                continue
            if event.get('type') == 'item.started':
                pending[item.get('id')] = item
            elif event.get('type') == 'item.completed':
                pending.pop(item.get('id'), None)
        if not pending:
            return 'fork'
        # A visual call may finish after the client loses its response. The
        # bridge queues initialize behind it; old revisions cannot execute an
        # act twice. Recover from current RGB, without replaying the lost call.
        if len(pending) == 1:
            item = next(iter(pending.values()))
            revision = (item.get('arguments') or {}).get('revision')
            if item.get('tool') in {'act', 'look'} and type(revision) is int and revision >= 0:
                return 'fork'
        return None
    if rate_limit_error(events) and (max_resumes == -1 or resumes < max_resumes):
        return 'resume'
    return None


def preflight_server(command, args, expected, output, deadline, *, attempt_timeout=90):
    """Retry one transport timeout before policy start; never issue tool calls.

    Keep each attempt and its stderr. Schema/process failures remain immediate
    failures, and legacy episode deadlines still bound each attempt.
    """
    attempts = []
    for attempt in (1, 2):
        attempt_output = output / 'mcp_preflight_attempts' / str(attempt)
        attempt_output.mkdir(parents=True, exist_ok=False)
        try:
            result = check_server(command, args, expected, attempt_output,
                                  timeout=deadline.remaining(attempt_timeout))
        except TimeoutError:
            if attempt == 2 or deadline.expired:
                raise
        else:
            result['attempts'] = [*attempts, str(attempt_output.relative_to(output))]
            return result
        finally:
            attempts.append(str(attempt_output.relative_to(output)))
            for name in ('mcp_preflight.json', 'mcp_preflight.stderr.log'):
                path = attempt_output / name
                if path.exists():
                    shutil.copyfile(path, output / name)


def main(argv=None, *, allow_client=False):
    argv = list(sys.argv[1:] if argv is None else argv)
    probing = allow_client and '--probe' in argv
    p = argparse.ArgumentParser()
    if allow_client:
        p.add_argument('--client', choices=['codex', 'opencode', 'kimi', 'claude', 'agy'], default='codex')
        p.add_argument('--probe', action='store_true', help='Report installed CLI capabilities; does not call a model')
    p.add_argument("--model", default='gpt-6.1-sol')
    p.add_argument("--instruction", required=not probing)
    p.add_argument("--mcp-command", required=not probing)
    p.add_argument("--mcp-args-json", required=not probing)
    p.add_argument("--output", type=Path, required=not probing)
    p.add_argument("--timeout", type=int, default=900,help='Controller wall timeout; 0 disables it for step-budget episodes')
    timing=p.add_mutually_exclusive_group()
    timing.add_argument('--deadline-unix', type=float,
                        help='Legacy absolute deadline; preserved for existing run configurations')
    timing.add_argument('--execution-clock-command-json',
                        help='Private argv JSON: atomically arm remote execution clock after MCP handshake')
    p.add_argument('--isolate-client-storage', action='store_true',
                   help='Use per-run native sessions and logs without changing HOME, CODEX_HOME or authentication')
    p.add_argument('--rgb-reference-review', action='store_true', help='Enable the episode RGB comparison tool parameter.')
    p.add_argument('--agent-profile', choices=['minimal','basic-skills','goal-tree','skills','skills_inline','workflow'], default='skills')
    p.add_argument('--goal-skill',choices=['hierarchical','tree','none'],default='hierarchical')
    p.add_argument('--scheduling-mode', choices=['none','text','program'], default='none',
                   help='Harness option: strategy supplementation mode (default none)')
    p.add_argument('--state-memory', choices=['off','on'], default='off',
                   help='Harness option: enable structured public state memory prompt and handoff tracking (default off)')
    p.add_argument('--rolling-handoff-steps', type=int, default=0,
                   help='Harness option: roll over to a fresh client session with state_memory handoff summary every N act/look steps (0 disables)')
    p.add_argument('--planning-mode', choices=['off','task_graph'], default='off',
                   help='Harness option: general precondition and cost-aware task graph planner (default off)')
    p.add_argument('--handoff-progress-note', choices=['off','on'], default='off',
                   help='Harness option: at each rolling checkpoint ask the session for a factual handoff note and '
                        'pass it to the next session (default off)')
    p.add_argument('--autonomy-instruction', choices=['off','on'], default='off',
                   help='Harness option: append the autonomous-episode operating instruction to the system prompt')
    p.add_argument('--require-tool-intent',action='store_true',
                   help='Match the simulator public_tool_intent_v1 tool schemas and prompt')
    p.add_argument('--reasoning-effort', choices=['low','medium','high','xhigh','max','ultra'])
    p.add_argument('--model-provider-profile', help='Use only this provider from the existing Codex config')
    p.add_argument('--model-provider-gateway', help='Optional loopback request queue for this provider')
    p.add_argument('--mcp-tool-exposure', choices=['client_default', 'direct'], default='client_default',
                   help='Codex only: direct exposes MCP tools as model functions instead of code-mode exec')
    p.add_argument('--codex-context', choices=['client_default', 'minimal'], default='client_default',
                   help='Codex only: minimal removes Codex base prompt, skills, environment and native tools')
    p.add_argument('--planner-model', help='Optional independent visual planning model; basic-skills profile only')
    p.add_argument('--planner-reasoning-effort', default='xhigh', choices=['low','medium','high','xhigh','max','ultra'])
    p.add_argument('--planner-python', default=sys.executable, help='Controller-side interpreter with the MCP extra')
    p.add_argument('--rate-limit-resumes', '--provider-error-resumes', type=int, default=None,
                   help='Same-session transient provider recovery count; -1 is unbounded (default for unbounded Codex episodes)')
    p.add_argument('--resume-backoff-seconds', type=int, default=90)
    p.add_argument('--unfinished-turn-nudges', type=int, default=0,
                   help='Harness option, Codex only: continue a normally completed turn that left the episode open; '
                        'the value bounds consecutive reminders that produce no act/look call (0 disables)')
    p.add_argument('--permanent-route-forks', type=int, default=0,
                   help='Opt-in native-history forks for an unavailable sticky route; retains the same live robot episode')
    p.add_argument('--action-feedback', choices=['off', 'sensor_only', 'evidence'], default='off',
                   help='Select the RGB feedback contract for a minimal/basic-skills episode')
    p.add_argument('--dispatch-policy', choices=DISPATCH_POLICIES, help='Use the event-driven ST-DAG controller')
    p.add_argument('--fast-vision-port', type=int, help='Use the local Qwen2.5-VL inference server')
    p.add_argument('--no-repair-inspection', action='store_true', help='Disable the current-RGB local repair crop')
    args = p.parse_args(argv)
    if args.dispatch_policy:
        if not args.planner_model:
            p.error('--dispatch-policy requires --planner-model')
        if args.reasoning_effort is None:
            args.reasoning_effort = 'low'
        from manipulation_agent.clients.temporal import run
        return run(args)
    feedback_mode = args.action_feedback
    if feedback_mode != 'off' and args.agent_profile not in ('minimal', 'basic-skills'):
        p.error('--action-feedback requires minimal or basic-skills')
    client = getattr(args, 'client', 'codex')
    if client == 'codex' and args.reasoning_effort is None:args.reasoning_effort='low'
    adapter = get_adapter(client)
    if probing:
        capability = adapter.probe()
        print(json.dumps(asdict(capability)))
        return 0 if capability.status == 'available' else 3
    deadline=(EpisodeDeadline() if args.execution_clock_command_json is not None else
              EpisodeDeadline(args.deadline_unix) if args.deadline_unix is not None else EpisodeDeadline.from_env())
    if args.timeout<0:p.error('--timeout must be nonnegative')
    if args.rolling_handoff_steps < 0:
        p.error('--rolling-handoff-steps must be nonnegative')
    if args.timeout==0:args.timeout=None
    if args.rate_limit_resumes is None:
        args.rate_limit_resumes = -1 if client in {'codex', 'claude', 'agy'} and args.timeout is None else 0
    if args.rate_limit_resumes < -1 or args.resume_backoff_seconds < 1:
        p.error('Provider resume count must be -1 or nonnegative and backoff positive')
    if args.rate_limit_resumes and client not in {'codex', 'claude', 'agy'}:
        p.error('Rate-limit resume is supported only for Codex, Claude and Agy')
    if args.permanent_route_forks < 0 or (args.permanent_route_forks and client != 'codex'):
        p.error('Permanent route forks require Codex and a nonnegative count')
    if args.planner_model and (args.agent_profile != 'basic-skills' or not args.model_provider_profile
                              or not args.model_provider_gateway):
        p.error('The planning subagent requires basic-skills and an explicit provider/gateway')
    # Benchmark side (all tiers): the scoring objective travels with the task text,
    # including rolling-handoff prompts that restate args.instruction.
    args.instruction = with_scoring_objective(args.instruction)
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(__file__).resolve().parents[1] / "src", args.output / "source_snapshot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    instructions = system_prompt(args.agent_profile, rgb_reference_review=args.rgb_reference_review, goal_skill=args.goal_skill,
                                 require_tool_intent=args.require_tool_intent,
                                 autonomy=args.autonomy_instruction == 'on',
                                 scheduling_mode=args.scheduling_mode,
                                 state_memory=args.state_memory == 'on',
                                 planning_mode=args.planning_mode)
    catalog = tool_specs(args.agent_profile, rgb_reference_review=args.rgb_reference_review, require_intent=args.require_tool_intent,
                         scheduling_mode=args.scheduling_mode,
                         planning_mode=args.planning_mode)
    raw_mcp_args = json.loads(args.mcp_args_json)
    if isinstance(raw_mcp_args, list) and (
        'manipulation_agent.mcp_server' in raw_mcp_args
        or any(str(item).endswith('mcp_server.py') for item in raw_mcp_args)
    ):
        if args.state_memory == 'on' and '--state-memory' not in raw_mcp_args:
            raw_mcp_args = [*raw_mcp_args, '--state-memory', 'on']
        if args.rolling_handoff_steps > 0 and '--rolling-handoff-steps' not in raw_mcp_args:
            raw_mcp_args = [*raw_mcp_args, '--rolling-handoff-steps', str(args.rolling_handoff_steps)]
        if args.planning_mode == 'task_graph' and '--planning-mode' not in raw_mcp_args:
            raw_mcp_args = [*raw_mcp_args, '--planning-mode', 'task_graph']
        if args.handoff_progress_note == 'on' and '--handoff-progress-note' not in raw_mcp_args:
            raw_mcp_args = [*raw_mcp_args, '--handoff-progress-note', 'on']
        args.mcp_args_json = json.dumps(raw_mcp_args)
    planner_config = None
    mcp_env_vars = ()
    if feedback_mode != 'off':
        from manipulation_agent.agent.action_evidence import INSPECT_TOOL, SENSOR_INSTRUCTION, EVIDENCE_INSTRUCTION
        feedback_path = (args.output / 'feedback_config.json').resolve()
        write_json(feedback_path, dict(mode=feedback_mode, state=str(args.output.resolve() / 'feedback_state.json'),
                                       upstream_command=args.mcp_command, upstream_args=json.loads(args.mcp_args_json)))
        args.mcp_command = args.planner_python
        args.mcp_args_json = json.dumps(['-m', 'manipulation_agent.evidence_server', '--config', str(feedback_path)])
        mcp_env_vars = ('PYTHONPATH',)
        instructions += SENSOR_INSTRUCTION
        if feedback_mode == 'evidence':
            catalog = [*catalog, INSPECT_TOOL]
            instructions += EVIDENCE_INSTRUCTION
    if args.planner_model:
        provider_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
        provider = tomllib.loads(provider_path.read_text())['model_providers'][args.model_provider_profile]
        # Codex filters MCP subprocess environments. Forward names, never write credential values.
        mcp_env_vars = ('PYTHONPATH', 'CODEX_HOME', 'MAS_CLIENT_STATE_ROOT', 'no_proxy', 'NO_PROXY', provider['env_key'])
        planner_config = dict(model=args.planner_model, effort=args.planner_reasoning_effort,
            instruction=args.instruction, provider=args.model_provider_profile, gateway=args.model_provider_gateway,
            upstream_command=args.mcp_command, upstream_args=json.loads(args.mcp_args_json),
            output=str((args.output / 'planner').resolve()))
        planner_path = (args.output / 'planner_config.json').resolve()
        write_json(planner_path, planner_config)
        args.mcp_command = args.planner_python
        args.mcp_args_json = json.dumps(['-m', 'manipulation_agent.planner_server', '--config', str(planner_path)])
        instructions += ACTOR_INSTRUCTION
        catalog = [*catalog, PLANNER_TOOL]
    config = ClientConfig(client, args.model, args.instruction, args.mcp_command,
                          json.loads(args.mcp_args_json), args.output, args.timeout,
                          args.agent_profile, args.isolate_client_storage,
                          args.reasoning_effort, args.model_provider_profile, args.model_provider_gateway,
                          args.mcp_tool_exposure, args.codex_context, mcp_env_vars=mcp_env_vars)
    capability = adapter.probe()
    if capability.status != 'available':
        write_json(args.output / 'controller.json', {
            'started_at': now(), 'finished_at': now(), 'status': capability.status,
            'controller': client + '_cli', 'client_capability': asdict(capability),
            'model': args.model, 'model_reasoning_effort': args.reasoning_effort,
            'agent_profile': args.agent_profile, 'exit_code': 3,
            'formal_finish_observed': False, 'tools_called': [], 'task_success': None})
        print(json.dumps(asdict(capability)))
        return 3
    try:
        project = adapter.prepare_project(config, instructions, [t['name'] for t in catalog])
    except (ValueError, RuntimeError) as exc:
        write_json(args.output / 'controller.json', {
            'started_at': now(), 'finished_at': now(), 'status': 'unsupported',
            'controller': client + '_cli', 'client_capability': asdict(capability),
            'model': args.model, 'model_reasoning_effort': args.reasoning_effort,
            'agent_profile': args.agent_profile, 'exit_code': 3,
            'failure_stage': 'prepare_project', 'failure_type': type(exc).__name__, 'reason': str(exc),
            'formal_finish_observed': False, 'tools_called': [], 'task_success': None})
        return 3
    command = project.argv
    native_sessions = project.native_sessions
    metadata = {"started_at": now(), "status": "running", "model": args.model,
                "model_reasoning_effort": args.reasoning_effort, "observation_mode": "rgb_only", "action_feedback": feedback_mode,
                "episode_deadline_unix":deadline.unix,"requested_timeout_seconds":args.timeout,
                "agent_profile": args.agent_profile, "goal_skill": args.goal_skill,
                "rgb_reference_review": args.rgb_reference_review,
                "require_tool_intent": args.require_tool_intent, "mcp_tool_exposure": args.mcp_tool_exposure,
                "codex_context": args.codex_context,
                "tool_intent_protocol": INTENT_PROTOCOL if args.require_tool_intent else LEGACY_PROTOCOL,
                "host": os.uname().nodename, "controller": client + "_cli", "command": command,
                "instruction": args.instruction, "source": source_version(),
                "client_version": capability.version, "client_capability": asdict(capability)}
    if planner_config is not None:
        metadata['planning_subagent'] = planner_config
    if project.client_storage is not None:
        metadata['client_storage'] = project.client_storage
    metadata['provider_recovery'] = dict(max_resumes=args.rate_limit_resumes,
        max_permanent_route_forks=args.permanent_route_forks,
        backoff_seconds=args.resume_backoff_seconds, gateway=args.model_provider_gateway,
        scope='Same live MCP episode; transient same-thread resumes and explicitly enabled native-history route forks')
    metadata['unfinished_turn_nudges'] = args.unfinished_turn_nudges
    metadata['autonomy_instruction'] = args.autonomy_instruction
    metadata['scheduling_mode'] = args.scheduling_mode
    metadata['state_memory'] = args.state_memory
    metadata['rolling_handoff_steps'] = args.rolling_handoff_steps
    metadata['planning_mode'] = args.planning_mode
    metadata['handoff_progress_note'] = args.handoff_progress_note
    write_json(args.output / "controller.json", metadata)
    def expired_before_policy(stage):
        metadata.update(status='failed',timeout=True,termination_reason='episode_deadline_exceeded',
                        failure_stage=stage,finished_at=now(),formal_finish_observed=False,
                        tools_called=[],task_success=None,exit_code=124,effective_timeout_seconds=0)
        write_json(args.output/'controller.json',metadata)
        return 124
    if deadline.expired:return expired_before_policy('before_mcp_handshake')
    try:
        mcp_command=args.mcp_command; mcp_args=json.loads(args.mcp_args_json)
        if native_sessions is not None:
            # Validate the same namespace the model will use, including SSH's
            # access to /dev/null. A host-only handshake misses mount failures.
            prefix=project.mcp_prefix
            mcp_command=prefix[0];mcp_args=prefix[1:]+['--',args.mcp_command,*mcp_args]
        metadata['mcp_preflight']=preflight_server(mcp_command,mcp_args,catalog,
                                                  args.output,deadline)
        metadata['mcp_preflight']['inside_client_storage_namespace']=native_sessions is not None
    except Exception as exc:
        if deadline.expired:return expired_before_policy('mcp_handshake')
        metadata.update(status='failed',failure_stage='mcp_handshake',failure_type=type(exc).__name__,
                        finished_at=now(),formal_finish_observed=False,tools_called=[],task_success=None)
        write_json(args.output/'controller.json',metadata)
        return 2
    if deadline.expired:return expired_before_policy('before_model_start')
    if args.execution_clock_command_json is not None:
        try:
            clock_command=json.loads(args.execution_clock_command_json)
            if not isinstance(clock_command,list) or not clock_command or any(not isinstance(v,str) or not v for v in clock_command):
                raise ValueError('Execution clock command must be a nonempty argv array')
            armed=subprocess.run(clock_command,capture_output=True,text=True,timeout=30,check=True)
            clock=validate_execution_clock(json.loads(armed.stdout))
            deadline=EpisodeDeadline(clock['episode_deadline_unix'])
            metadata.update(execution_clock=clock,execution_started_at_unix=clock['execution_started_at_unix'],
                episode_deadline_unix=clock['episode_deadline_unix'],execution_budget_seconds=clock['execution_budget_seconds'],
                timing_origin='model_execution_after_mcp_preflight',startup_excluded_from_execution_budget=True)
        except Exception as exc:
            metadata.update(status='failed',failure_stage='execution_clock',failure_type=type(exc).__name__,
                            finished_at=now(),formal_finish_observed=False,tools_called=[],task_success=None)
            write_json(args.output/'controller.json',metadata)
            return 2
        if deadline.expired:return expired_before_policy('execution_clock_already_expired')
    metadata['effective_timeout_seconds']=deadline.remaining(args.timeout)
    metadata['policy_started_at']=now();write_json(args.output/'controller.json',metadata)
    def on_start(pid):
        metadata['pid'] = pid
        write_json(args.output / 'controller.json', metadata)
    policy_started_monotonic=time.monotonic()
    hard_robot_cap = (args.rolling_handoff_steps + 2) if args.rolling_handoff_steps > 0 else 0
    episode_memory = (StateMemory(output_dir=args.output)
                      if (args.state_memory == 'on' or args.rolling_handoff_steps > 0) else None)
    process = adapter.run(project, args.output, deadline.remaining(args.timeout), on_start,
                          max_robot_calls=hard_robot_cap)
    raw_events=args.output/'model_events.jsonl'
    first_parsed=adapter.parse(raw_events)
    thread_id=session_thread_id(first_parsed.raw_events, client)
    segment_events=first_parsed.compat
    if episode_memory is not None:
        episode_memory.ingest_compat_events(segment_events, namespace='segment0')
    continuations=[]
    resume_count = fork_count = nudge_count = handoff_count = 0
    for attempt in itertools.count(1):
        remaining=(None if metadata['effective_timeout_seconds'] is None else
                   metadata['effective_timeout_seconds']-(time.monotonic()-policy_started_monotonic))
        mode = provider_recovery_mode(segment_events, resumes=resume_count, forks=fork_count,
            max_resumes=args.rate_limit_resumes, max_forks=args.permanent_route_forks)
        if mode is None and client in {'codex', 'claude', 'agy'} and rolling_handoff_due(
                segment_events, returncode=process.returncode, finish_received=process.finish_received,
                timed_out=process.timed_out, rolling_handoff_steps=args.rolling_handoff_steps,
                cleanup_reason=process.cleanup_reason, handoffs=handoff_count):
            mode = 'handoff'
        if mode is None and client in {'codex', 'claude', 'agy'} and unfinished_turn_nudge(
                segment_events, returncode=process.returncode, finish_received=process.finish_received,
                timed_out=process.timed_out, nudges=nudge_count, max_nudges=args.unfinished_turn_nudges):
            mode = 'nudge'
        backoff = 0 if mode in {'nudge', 'handoff'} else args.resume_backoff_seconds
        if ((process.returncode == 0 and mode not in {'nudge', 'handoff'}) or process.finish_received or process.timed_out or
                (not thread_id and mode != 'handoff') or mode is None or
                (remaining is not None and remaining <= backoff+30)):
            break
        metadata['provider_recovery'].update(state='waiting', next_attempt=attempt, next_mode=mode)
        write_json(args.output/'controller.json',metadata)
        time.sleep(backoff)
        remaining=(None if metadata['effective_timeout_seconds'] is None else
                   metadata['effective_timeout_seconds']-(time.monotonic()-policy_started_monotonic))
        if remaining is not None and remaining <= 30:
            break
        segment_dir=args.output/'continuations'/str(attempt)
        segment_dir.mkdir(parents=True,exist_ok=False)
        if mode == 'handoff':
            mem_for_handoff = episode_memory or StateMemory()
            resume_prompt = mem_for_handoff.build_handoff_context(
                args.instruction,
                segment_index=handoff_count + 1,
                steps_remaining=latest_steps_remaining(segment_events),
                planning_mode=args.planning_mode,
                progress_note=(last_agent_message(segment_events)
                               if args.handoff_progress_note == 'on' else None),
            )
            resume_project = adapter.prepare_handoff(project, resume_prompt)
        else:
            resume_prompt=NUDGE_PROMPT if mode == 'nudge' else (
                           'Continue the same robot task in the same simulator episode. '
                           'A model service error interrupted the previous turn. '
                           'Use the prior conversation and current RGB tool feedback; continue choosing '
                           'actions yourself, then call finish. Do not restart the task or claim success '
                           'without visual evidence.')
            if mode == 'fork':
                resume_prompt += (' A previous tool response may have been lost. Do not replay any pending call. '
                                  'First call initialize to read the current RGB and your task notes; '
                                  'initialize does not reset or advance the scene. Then continue from that observation.')
            resume_project=(adapter.prepare_fork(project,thread_id,resume_prompt) if mode == 'fork'
                            else adapter.prepare_resume(project,thread_id,resume_prompt))
        resumed=adapter.run(resume_project,segment_dir,remaining,on_start,max_robot_calls=hard_robot_cap)
        segment_path=segment_dir/'model_events.jsonl'
        seg_parsed=adapter.parse(segment_path)
        segment_events=seg_parsed.compat
        if episode_memory is not None:
            episode_memory.ingest_compat_events(segment_events, namespace=f'segment{attempt}')
        with raw_events.open('ab') as combined,segment_path.open('rb') as segment:
            combined.write(segment.read())
        continued_thread = session_thread_id(seg_parsed.raw_events, client)
        continuations.append({'attempt':attempt,'mode':mode,'parent_thread_id':thread_id,
                              'thread_id':continued_thread or thread_id,
                              'returncode':resumed.returncode,'timed_out':resumed.timed_out,
                              'raw_events':str(segment_path.relative_to(args.output))})
        metadata['provider_recovery'].update(state='resumed', attempts=continuations)
        write_json(args.output/'controller.json',metadata)
        process.returncode=resumed.returncode
        process.timed_out=process.timed_out or resumed.timed_out
        process.policy_finished_at_unix=resumed.policy_finished_at_unix
        process.finish_received=resumed.finish_received
        process.cleanup_reason=resumed.cleanup_reason
        process.duration_seconds=time.monotonic()-policy_started_monotonic
        if mode == 'fork':
            fork_count += 1
            if not continued_thread or continued_thread == thread_id:
                metadata['provider_recovery']['state']='fork_identity_unconfirmed'
                write_json(args.output/'controller.json',metadata)
                break
            thread_id = continued_thread
        elif mode == 'handoff':
            handoff_count += 1
            if continued_thread:
                thread_id = continued_thread
        elif mode == 'nudge':
            nudge_count = 0 if segment_robot_calls(segment_events) else nudge_count + 1
        else:
            resume_count += 1
    if continuations:
        metadata['rate_limit_continuations']=continuations
    policy_finished_at = process.policy_finished_at_unix
    if process.timed_out:
        metadata['timeout'] = True
        metadata['termination_reason'] = ('episode_deadline_exceeded' if deadline.unix is not None
            and policy_finished_at >= deadline.unix else 'controller_timeout')
    metadata['policy_finished_at_unix'] = policy_finished_at
    parsed = adapter.parse(args.output / 'model_events.jsonl')
    write_derived_events(args.output, parsed, client)
    events = parsed.raw_events
    if parsed.invalid_lines:
        metadata['event_decode_errors'] = {'line_numbers': parsed.invalid_lines, 'raw_preserved': True}
    closed = parsed.formal_finish_observed
    calls = parsed.tools_called
    metadata['event_records'] = {'raw': 'model_events.jsonl', 'canonical': 'canonical_events.jsonl',
        'replay': 'model_events.jsonl' if client == 'codex' else 'model_events.compat.jsonl'}
    if deadline.unix is not None and policy_finished_at>=deadline.unix:
        metadata.update(timeout=True,termination_reason='episode_deadline_exceeded')
    logical_complete = closed and not metadata.get('timeout') and (process.returncode == 0 or process.finish_received)
    metadata.update(status="passed" if logical_complete else "failed", exit_code=process.returncode,
                    client_cleanup_reason=process.cleanup_reason,
                    client_cleanup_status='forced_after_finish' if process.cleanup_reason else 'normal_exit',
                    duration_seconds=process.duration_seconds, finished_at=now(),
                    task_success=None, formal_finish_observed=closed, tools_called=calls,
                    validation_note="Client exit alone does not prove task success; join with simulator run.json")
    try:
        metadata['reasoning_trace']=(export_summaries(args.output,events,metadata['started_at'],metadata['finished_at'],sessions_root=native_sessions) if client == 'codex' else {'status': 'not_collected', 'reason': 'No run-owned provider summary export; raw events preserved'})
    except Exception as exc:
        metadata['reasoning_trace']={'status':'failed','error_type':type(exc).__name__,
                                     'reason':'Run-local summary export failed; raw controller events preserved'}
    write_json(args.output / "controller.json", metadata)
    print(json.dumps({k: metadata[k] for k in ("status", "exit_code", "model", "duration_seconds")}))
    return 0 if logical_complete else process.returncode or (124 if metadata.get('timeout') else 2)


if __name__ == "__main__":
    raise SystemExit(main())
