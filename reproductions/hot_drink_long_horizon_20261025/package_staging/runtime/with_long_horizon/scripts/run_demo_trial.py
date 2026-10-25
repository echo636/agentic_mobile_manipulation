"""Run an RGB-only model trial, with optional visible manipulation.

The simulator and model use the public MCP act/finish interface. Credentials stay
in the installed Codex login; no key is accepted, copied or written to the run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


def health(url):
    try:
        with build_opener(ProxyHandler({})).open(url + '/healthz', timeout=3) as response:
            return json.load(response)
    except Exception:
        return None


def verify_experiment_provider(codex_home, profile, expected_host):
    """Fail before simulator startup if the run would use another credential."""
    home=Path(codex_home)
    config=tomllib.loads((home/'config.toml').read_text())
    provider=config.get('model_providers',{}).get(profile,{})
    host=urlsplit(provider.get('base_url','')).hostname
    auth=json.loads((home/'auth.json').read_text())
    if host != expected_host or auth.get('auth_mode')!='apikey' or not auth.get('OPENAI_API_KEY'):
        raise ValueError('The requested experiment provider and isolated API-key login are not configured')
    return host


def agent_profile(*, high_level_harness=False, semantic_magic=False,
                  long_horizon_planning_skill=False):
    """Keep visual actions and ordinary workflow skills in every RGB run."""
    if semantic_magic:
        return 'semantic'  # Historical named-action comparisons only.
    if long_horizon_planning_skill:
        return 'planning'
    return 'skills'


def prepare_bddl_overlay(behavior_root, task, bddl_text, run_root):
    """Use a run-local definition when it differs from the pinned public task."""
    public_definition = (behavior_root / 'bddl3' / 'bddl' / 'activity_definitions'
                         / task / 'problem0.bddl')
    if not public_definition.is_file():
        raise FileNotFoundError(public_definition)
    if bddl_text == public_definition.read_text():
        return None, False
    overlay = run_root / 'bddl_overlay'
    package = overlay / 'bddl'
    shutil.copytree(behavior_root / 'bddl3' / 'bddl', package, copy_function=os.link)
    definition = package / 'activity_definitions' / task / 'problem0.bddl'
    definition.unlink()  # Keep the public definition behind the hardlink intact.
    definition.write_text(bddl_text)
    return overlay, True


def main(*, demo_motion=True):
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--instruction', help='Natural-language task instruction shown to the model')
    parser.add_argument('--bddl-source', type=Path,
                        help='Run-local task definition; different goals use the pinned scene with custom native BDDL scoring')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--omnigibson-source', type=Path, required=True)
    parser.add_argument('--provider-profile', required=True,
                        help='Name of an existing Codex model provider; no credentials are copied')
    parser.add_argument('--model', default='gpt-6-astra',
                        help='Model identifier accepted by the configured provider')
    parser.add_argument('--reasoning-effort', choices=['low','medium','high','xhigh'], default='low')
    parser.add_argument('--codex-home', type=Path,
                        help='Isolated Codex home containing the experiment provider and login')
    parser.add_argument('--expected-provider-host',
                        help='Reject another provider before loading the simulator')
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--gpu', type=int, default=3)
    parser.add_argument('--cpu-dynamics', action='store_true',
                        help='Use CPU rigid dynamics for tasks that do not require particle systems')
    parser.add_argument('--appdata', type=Path, help='Existing OmniGibson cache for this GPU')
    parser.add_argument('--timeout', type=int, default=3600,
                        help='Hard wall-clock watchdog only; task budget is --max-actions')
    parser.add_argument('--unbounded-episode', action='store_true', help='No episode wall cutoff; startup and closure watchdogs remain')
    parser.add_argument('--max-actions', type=int, default=80,
                        help='Maximum model-selected act/look actions before the episode must finish')
    parser.add_argument('--max-sim-steps', type=int, default=20000,
                        help='Maximum simulator env steps; this is a secondary safety bound')
    parser.add_argument('--unfinished-restarts', type=int, default=1,
                        help='Fresh model continuations if it exits without a formal finish')
    parser.add_argument('--long-horizon-planning-skill', choices=['on','off'], default='off',
                        help='Enable the unified natural-language/BDDL goal DAG skill and required public decision records')
    parser.add_argument('--semantic-magic', action='store_true',
                        help='Use named high-level actions for the hot-drink planning comparison')
    parser.add_argument('--high-level-harness', action='store_true',
                        help='Score hot-drink semantic effects independently of contact and particle states')
    args = parser.parse_args()
    if args.timeout <= 0 or args.max_actions <= 0 or args.max_sim_steps <= 0:
        parser.error('--timeout, --max-actions and --max-sim-steps must be positive')
    if args.unfinished_restarts < 0:
        parser.error('--unfinished-restarts must be nonnegative')
    if not args.instruction and not args.bddl_source:
        parser.error('one of --instruction or --bddl-source is required')
    if args.task == 'prepare_and_serve_hot_drink' and os.environ.get('OG_CLEAR_WATER_PARTICLES'):
        parser.error('OG_CLEAR_WATER_PARTICLES removes the water required by this task')
    if args.cpu_dynamics and args.task == 'prepare_and_serve_hot_drink' and not args.high_level_harness:
        parser.error('--cpu-dynamics cannot initialize the native hot-drink water particles')
    if args.semantic_magic and args.task != 'prepare_and_serve_hot_drink':
        parser.error('--semantic-magic is scoped to prepare_and_serve_hot_drink')
    if args.semantic_magic and args.long_horizon_planning_skill == 'on':
        parser.error('--long-horizon-planning-skill requires the RGB pixel action protocol')
    if args.high_level_harness and not args.bddl_source:
        parser.error('--high-level-harness requires an explicit visual-scene BDDL source')
    bddl_text = args.bddl_source.read_text() if args.bddl_source else None
    if bddl_text:
        if f'(problem {args.task}-0)' not in bddl_text or '(:goal' not in bddl_text:
            parser.error('--bddl-source must be the requested task definition with a goal clause')
        if not args.instruction:
            catalog = json.loads((Path(__file__).resolve().parents[1] /
                                  'src/manipulation_agent/tasks.json').read_text())
            args.instruction = (catalog.get('tasks', {}).get(args.task, {}).get('instruction')
                                or bddl_text)
    if args.expected_provider_host and args.codex_home is None:
        parser.error('--expected-provider-host requires --codex-home')
    provider_host=(verify_experiment_provider(args.codex_home,args.provider_profile,args.expected_provider_host)
                   if args.expected_provider_host else None)
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source / 'src'))
    og = args.omnigibson_source.resolve()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    task_spec_file = root / 'task_spec.bddl' if args.bddl_source else None
    if task_spec_file is not None:
        task_spec_file.write_text(bddl_text)
    run, controller = root / 'run', root / 'controller'
    bddl_overlay, custom_bddl = (prepare_bddl_overlay(og.parent, args.task, bddl_text, root)
                                  if bddl_text else (None, False))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    environment = os.environ.copy()
    if args.codex_home is not None:
        environment['CODEX_HOME']=str(args.codex_home.resolve())
    environment['PYTHONPATH'] = os.pathsep.join((str(bddl_overlay) if bddl_overlay else '',
        str(source / 'src'), str(og),
        str(og.parent / 'bddl3'), str(og.parent / 'joylo'), environment.get('PYTHONPATH', '')))
    environment.update(CUDA_VISIBLE_DEVICES=str(args.gpu), GAP_BEHAVIOR_GPU_ID=str(args.gpu),
        MAS_LONG_HORIZON_PLANNING_SKILL='1' if args.long_horizon_planning_skill == 'on' else '0',
        OMNIGIBSON_DATA_PATH=str(Path(os.environ.get('MAS_TASK_DATA_PATH', og.parent / 'datasets')).resolve()),
        OMNIGIBSON_APPDATA_PATH=str((args.appdata or root.parent / f'appdata_gpu{args.gpu}').resolve()),
        MAS_DEMO_MOTION='1' if demo_motion else '0', MAS_VIDEO_RENDER_STRIDE='3',
        MAS_SEMANTIC_MAGIC='1' if args.semantic_magic else '0',
        MAS_HIGH_LEVEL_HARNESS='1' if args.high_level_harness else '0',
        MAS_CUSTOM_NATIVE_BDDL='1' if custom_bddl and not args.high_level_harness else '0',
        MAS_CPU_DYNAMICS='1' if args.cpu_dynamics else '0',
        OG_KINEMATIC_NAV_TELEPORT='0', MAS_NAV_RELAXED='0',
        OG_SKIP_PHYSICS_STEPS='0',
        OG_DISABLE_CONTACT_CACHE='1',
        MAS_RGB_IMAGE_SIZE='1024' if args.task == 'putting_up_Christmas_decorations_inside' else '512',
        MAS_RGB_PITCH_DEGREES='18' if args.task == 'can_meat' else '35')
    if args.cpu_dynamics:
        environment.pop('OG_DISABLE_CONTACT_CACHE', None)
    if args.task == 'prepare_and_serve_hot_drink':
        # The source system is initialized on GPU; CPU rigid dynamics avoids a
        # PhysX CUDA access fault during walking. The backend scopes the macro
        # needed for cooked-water initialization to that one future system.
        environment['OG_SWITCH_TO_CPU_AFTER_WATER'] = '1'
    else:
        environment.pop('OG_SWITCH_TO_CPU_AFTER_WATER', None)
    Path(environment['OMNIGIBSON_APPDATA_PATH']).mkdir(parents=True, exist_ok=True)
    # USD and Python tempfile create large per-scene scratch assets. Keep them
    # on the experiment data volume rather than the small /tmp user quota.
    scratch=(root.parent / f'tmp_gpu{args.gpu}').resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    environment['TMPDIR']=str(scratch)
    profile = agent_profile(high_level_harness=args.high_level_harness,
                            semantic_magic=args.semantic_magic,
                            long_horizon_planning_skill=args.long_horizon_planning_skill == 'on')
    simulator_command = [sys.executable, '-m', 'manipulation_agent.vision_cli',
        '--backend', 'omnigibson', '--policy', 'serve', '--agent-profile', profile,
        '--task', args.task, '--instance', str(args.instance), '--seed', str(args.seed),
        '--wall-seconds', str(args.timeout + 180),
        '--max-actions', str(args.max_actions),
        '--max-sim-steps', str(args.max_sim_steps),
        '--record-video', '--controller', f'codex:{args.model}', '--output', str(run),
        '--port', str(port)]
    if args.unbounded_episode:
        simulator_command += ['--unbounded-episode']
    simulator_command += ['--instruction', args.instruction]
    with (root / 'simulator.log').open('w') as sim_log, (root / 'controller.log').open('w') as model_log:
        simulator = subprocess.Popen(simulator_command, cwd=source, env=environment,
                                     stdout=sim_log, stderr=subprocess.STDOUT, start_new_session=True)
        model_code = None
        failure_type = None
        try:
            for _ in range(450):
                if simulator.poll() is not None:
                    raise RuntimeError('Simulator exited before MCP bridge was ready')
                state = health(url)
                if state and state.get('ready') and not state.get('closed'):
                    break
                time.sleep(2)
            else:
                raise TimeoutError('Simulator startup exceeded 900 seconds')
            command = [sys.executable, str(source / 'scripts/run_codex_controller.py'),
                '--model', args.model, '--reasoning-effort', args.reasoning_effort,
                '--model-provider-profile', args.provider_profile,
                '--instruction', args.instruction, '--mcp-command', sys.executable,
                '--mcp-args-json', json.dumps(['-m', 'manipulation_agent.mcp_server', '--bridge', url]),
                '--agent-profile', profile, '--timeout', str(args.timeout), '--output', str(controller)]
            if args.unbounded_episode:
                command += ['--unbounded-episode']
            command += ['--rate-limit-resumes', '3', '--resume-backoff-seconds', '120',
                        '--provider-history-restarts', '2', '--unfinished-restarts',
                        str(args.unfinished_restarts)]
            model = subprocess.Popen(command, cwd=source, env=environment,
                stdout=model_log, stderr=subprocess.STDOUT, start_new_session=True)
            execution_started = time.monotonic()
            closure_started = None
            try:
                while model.poll() is None:
                    state = health(url)
                    if args.unbounded_episode:
                        if state and (state.get('closed') or state.get('budget_exhausted')):
                            closure_started = closure_started or time.monotonic()
                        if simulator.poll() is not None:
                            closure_started = closure_started or time.monotonic()
                        if closure_started is not None and time.monotonic()-closure_started > 180:
                            raise TimeoutError('Post-budget/closure watchdog exceeded 180 seconds')
                    elif time.monotonic()-execution_started > args.timeout+180:
                        raise TimeoutError('Controller watchdog exceeded')
                    time.sleep(2)
                model_code = model.returncode
            finally:
                if model.poll() is None:
                    control_path = controller / 'controller.json'
                    child = json.loads(control_path.read_text()).get('pid') if control_path.exists() else None
                    for pid in (child, model.pid):
                        if pid:
                            try: os.killpg(pid, signal.SIGTERM)
                            except ProcessLookupError: pass
                    try: model.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(model.pid, signal.SIGKILL)
                        model.wait()
            if health(url) and not health(url).get('closed'):
                from manipulation_agent.bridge import rpc
                rpc(url, 'finish', {'outcome': 'aborted', 'reason': 'Model ended without formal finish'},
                    'demo-supervisor-finish', timeout=120)
            simulator.wait(timeout=180)
        except Exception as exc:
            failure_type = type(exc).__name__
        finally:
            if simulator.poll() is None:
                simulator.terminate()
                try:
                    simulator.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    simulator.kill(); simulator.wait()
        record = json.loads((run / 'run.json').read_text()) if (run / 'run.json').exists() else {}
        control = json.loads((controller / 'controller.json').read_text()) if (controller / 'controller.json').exists() else {}
        evaluation = record.get('evaluation') or {}
        summary = {'task': args.task, 'instance': args.instance, 'seed': args.seed,
                   'task_spec_format': 'bddl' if args.bddl_source else 'natural_language',
                   'task_definition_origin': 'custom_bddl_overlay' if custom_bddl else 'pinned_public',
                   'task_variant': args.bddl_source.parent.name if custom_bddl else None,
                   'cpu_dynamics': args.cpu_dynamics,
                   'long_horizon_planning_skill_enabled': args.long_horizon_planning_skill == 'on',
                   'bddl_source': str(args.bddl_source.resolve()) if args.bddl_source else None,
                   'bddl_sha256': hashlib.sha256(bddl_text.encode()).hexdigest() if bddl_text else None,
                   'instruction': args.instruction,
                   'model': args.model, 'reasoning_effort': args.reasoning_effort,
                   'provider_profile':args.provider_profile,'provider_host':provider_host,
                   'max_actions': args.max_actions, 'max_sim_steps': args.max_sim_steps,
                   'unfinished_restarts': args.unfinished_restarts,
                   'wall_timeout_seconds': None if args.unbounded_episode else args.timeout,
                   'unbounded_episode': args.unbounded_episode,
                   'demo_motion': demo_motion, 'controller_exit_code': model_code,
                   'semantic_magic':args.semantic_magic,
                   'high_level_harness':args.high_level_harness,
                   'skill_catalog_enabled': profile in {'skills','planning'},
                   'agent_profile':profile,
                   'target_selection':'model_named_target' if args.semantic_magic else 'model_rgb_pixel',
                   'navigation_selection':'executor_internal' if args.semantic_magic else 'model_act_navigate_to',
                   'observation_mode':'rgb_semantic_magic' if args.semantic_magic else 'rgb_only',
                   'supervisor_failure_type': failure_type,
                   'formal_finish_observed': control.get('formal_finish_observed'),
                   'official_task_success': evaluation.get('official_task_success'),
                   'custom_native_bddl_task_success': evaluation.get('custom_task_success'),
                   'video_status': (record.get('video') or {}).get('status')}
        (root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary))
    return 2 if failure_type else 0


if __name__ == '__main__':
    raise SystemExit(main())
