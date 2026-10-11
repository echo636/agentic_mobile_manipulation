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
from dataclasses import asdict, replace

from manipulation_agent.clients import ClientConfig, get_adapter
from manipulation_agent.clients.events import write_derived_events

from manipulation_agent.tools import tool_specs
from manipulation_agent.vision_policy import system_prompt
from manipulation_agent.records import now, write_json, source_version
from manipulation_agent.model_trace import export_summaries
from manipulation_agent.mcp_preflight import check_server
from manipulation_agent.deadline import EpisodeDeadline, validate_execution_clock


def rate_limit_error(events):
    return any(event.get('type') in {'error', 'turn.failed'} and
               '429 Too Many Requests' in str(event.get('message') or event.get('error') or '')
               for event in events)


def provider_history_error(events):
    """Recognize the provider's malformed tool history, which cannot be resumed."""
    return any(event.get('type') in {'error', 'turn.failed'} and
               'custom_tool_call' in str(event.get('message') or event.get('error') or '') and
               'required' in str(event.get('message') or event.get('error') or '') and
               'reasoning' in str(event.get('message') or event.get('error') or '')
               for event in events)


def codex_thread_id(events):
    return next((event.get('thread_id') for event in events
                 if event.get('type') == 'thread.started' and event.get('thread_id')), None)


def main(argv=None, *, allow_client=False):
    argv = list(sys.argv[1:] if argv is None else argv)
    probing = allow_client and '--probe' in argv
    p = argparse.ArgumentParser()
    if allow_client:
        p.add_argument('--client', choices=['codex', 'opencode', 'kimi'], default='codex')
        p.add_argument('--probe', action='store_true', help='Report installed CLI capabilities; does not call a model')
    p.add_argument("--model", required=not probing)
    p.add_argument("--instruction", required=not probing)
    p.add_argument("--mcp-command", required=not probing)
    p.add_argument("--mcp-args-json", required=not probing)
    p.add_argument("--output", type=Path, required=not probing)
    p.add_argument("--timeout", type=int, default=900)
    timing=p.add_mutually_exclusive_group()
    timing.add_argument('--deadline-unix', type=float,
                        help='Legacy absolute deadline; preserved for existing run configurations')
    timing.add_argument('--execution-clock-command-json',
                        help='Private argv JSON: atomically arm remote execution clock after MCP handshake')
    p.add_argument('--isolate-client-storage', action='store_true',
                   help='Use per-run native sessions and logs without changing HOME, CODEX_HOME or authentication')
    p.add_argument('--agent-profile', choices=['minimal','skills','workflow'], default='skills')
    p.add_argument('--reasoning-effort', choices=['low','medium','high','xhigh'])
    p.add_argument('--model-provider-profile', help='Use only this provider from the existing Codex config')
    p.add_argument('--rate-limit-resumes', type=int, default=0,
                   help='Resume the same Codex session after a provider 429, retaining its MCP episode')
    p.add_argument('--resume-backoff-seconds', type=int, default=90)
    p.add_argument('--provider-history-restarts', type=int, default=0,
                   help='Start a fresh Codex thread in the same live episode after malformed provider tool history')
    p.add_argument('--unfinished-restarts', type=int, default=0,
                   help='Start a fresh Codex thread when the model ends without calling finish')
    args = p.parse_args(argv)
    client = getattr(args, 'client', 'codex')
    adapter = get_adapter(client)
    if probing:
        capability = adapter.probe()
        print(json.dumps(asdict(capability)))
        return 0 if capability.status == 'available' else 3
    deadline=(EpisodeDeadline() if args.execution_clock_command_json is not None else
              EpisodeDeadline(args.deadline_unix) if args.deadline_unix is not None else EpisodeDeadline.from_env())
    if args.timeout<=0:p.error('--timeout must be positive')
    if (args.rate_limit_resumes < 0 or args.resume_backoff_seconds < 1 or
            args.provider_history_restarts < 0 or args.unfinished_restarts < 0):
        p.error('Rate-limit resume count must be nonnegative and backoff positive')
    if (args.rate_limit_resumes or args.provider_history_restarts or args.unfinished_restarts) and client != 'codex':
        p.error('Provider recovery is supported only for Codex')
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(__file__).resolve().parents[1] / "src", args.output / "source_snapshot",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    config = ClientConfig(client, args.model, args.instruction, args.mcp_command,
                          json.loads(args.mcp_args_json), args.output, args.timeout,
                          args.agent_profile, args.isolate_client_storage,
                          args.reasoning_effort, args.model_provider_profile)
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
        project = adapter.prepare_project(config, system_prompt(args.agent_profile),
                                          [t['name'] for t in tool_specs(args.agent_profile)])
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
                "model_reasoning_effort": args.reasoning_effort, "observation_mode": "rgb_only",
                "episode_deadline_unix":deadline.unix,"requested_timeout_seconds":args.timeout,
                "agent_profile": args.agent_profile,
                "host": os.uname().nodename, "controller": client + "_cli", "command": command,
                "instruction": args.instruction, "source": source_version(),
                "client_version": capability.version, "client_capability": asdict(capability)}
    if project.client_storage is not None:
        metadata['client_storage'] = project.client_storage
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
        metadata['mcp_preflight']=check_server(mcp_command,mcp_args,tool_specs(args.agent_profile),args.output,
                                               timeout=deadline.remaining(90))
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
    process = adapter.run(project, args.output, deadline.remaining(args.timeout), on_start)
    raw_events=args.output/'model_events.jsonl'
    first_events=adapter.parse(raw_events).raw_events
    thread_id=codex_thread_id(first_events) if client == 'codex' else None
    segment_events=first_events
    continuations=[]
    for attempt in range(1,args.rate_limit_resumes+1):
        remaining=metadata['effective_timeout_seconds']-(time.monotonic()-policy_started_monotonic)
        if (process.returncode == 0 or process.timed_out or not thread_id or
                not rate_limit_error(segment_events) or
                remaining <= args.resume_backoff_seconds+30):
            break
        time.sleep(args.resume_backoff_seconds)
        remaining=metadata['effective_timeout_seconds']-(time.monotonic()-policy_started_monotonic)
        if remaining <= 30:
            break
        segment_dir=args.output/'continuations'/str(attempt)
        segment_dir.mkdir(parents=True,exist_ok=False)
        resume_prompt=('Continue the same robot task in the same simulator episode. '
                       'A temporary model service rate limit interrupted the previous turn. '
                       'Use the prior conversation and current RGB tool feedback; continue choosing '
                       'actions yourself, then call finish. Do not restart the task or claim success '
                       'without visual evidence.')
        resume_project=adapter.prepare_resume(project,thread_id,resume_prompt)
        resumed=adapter.run(resume_project,segment_dir,remaining,on_start)
        segment_path=segment_dir/'model_events.jsonl'
        segment_events=adapter.parse(segment_path).raw_events
        with raw_events.open('ab') as combined,segment_path.open('rb') as segment:
            combined.write(segment.read())
        continuations.append({'attempt':attempt,'thread_id':thread_id,
                              'returncode':resumed.returncode,'timed_out':resumed.timed_out,
                              'raw_events':str(segment_path.relative_to(args.output))})
        process.returncode=resumed.returncode
        process.timed_out=process.timed_out or resumed.timed_out
        process.policy_finished_at_unix=resumed.policy_finished_at_unix
        process.duration_seconds=time.monotonic()-policy_started_monotonic
    if continuations:
        metadata['rate_limit_continuations']=continuations
    fresh_restarts=[]
    history_count=0
    unfinished_count=0
    for attempt in range(1,args.provider_history_restarts+args.unfinished_restarts+1):
        remaining=metadata['effective_timeout_seconds']-(time.monotonic()-policy_started_monotonic)
        if process.timed_out or remaining <= 60:
            break
        if provider_history_error(segment_events) and history_count<args.provider_history_restarts:
            reason='provider_tool_history_protocol_error'
            history_count+=1
        elif (process.returncode == 0 and
              not adapter.parse(raw_events).formal_finish_observed and
              unfinished_count<args.unfinished_restarts):
            reason='model_ended_without_formal_finish'
            unfinished_count+=1
        else:
            break
        segment_dir=args.output/'fresh_restarts'/str(attempt)
        segment_dir.mkdir(parents=True,exist_ok=False)
        restart_instruction=(args.instruction + '\n\nA previous model thread ended during this same '
                             'live simulator episode (' + reason + '). Call initialize to inspect the '
                             'current RGB observation; it does not reset the episode. Infer which goals '
                             'remain from visible evidence, choose all actions yourself, and call finish '
                             'with an evidence-based outcome before final text. Do not assume any earlier '
                             'action succeeded without checking.')
        restart_config=replace(config,output=segment_dir,instruction=restart_instruction,
                               timeout=remaining)
        restart_project=adapter.prepare_project(restart_config,system_prompt(args.agent_profile),
                                                [t['name'] for t in tool_specs(args.agent_profile)])
        restarted=adapter.run(restart_project,segment_dir,remaining,on_start)
        segment_path=segment_dir/'model_events.jsonl'
        segment_events=adapter.parse(segment_path).raw_events
        with raw_events.open('ab') as combined,segment_path.open('rb') as segment:
            combined.write(segment.read())
        fresh_restarts.append({'attempt':attempt,'reason':reason,
                               'thread_id':codex_thread_id(segment_events),
                               'returncode':restarted.returncode,'timed_out':restarted.timed_out,
                               'raw_events':str(segment_path.relative_to(args.output))})
        process.returncode=restarted.returncode
        process.timed_out=process.timed_out or restarted.timed_out
        process.policy_finished_at_unix=restarted.policy_finished_at_unix
        process.duration_seconds=time.monotonic()-policy_started_monotonic
    if fresh_restarts:
        metadata['fresh_thread_restarts']=fresh_restarts
        metadata['provider_history_restarts']=[entry for entry in fresh_restarts
                                               if entry['reason']=='provider_tool_history_protocol_error']
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
    metadata.update(status="passed" if process.returncode == 0 and closed and not metadata.get('timeout') else "failed", exit_code=process.returncode,
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
    return process.returncode or (124 if metadata.get('timeout') else 0 if closed else 2)


if __name__ == "__main__":
    raise SystemExit(main())
