"""Run an RGB-only model trial with recorded visible manipulation.

The simulator and model use the public MCP act/finish interface. Credentials stay
in the installed Codex login; no key is accepted, copied or written to the run.
"""
import argparse
import json
import os
from pathlib import Path
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--instruction', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--omnigibson-source', type=Path, required=True)
    parser.add_argument('--provider-profile', required=True,
                        help='Name of an existing Codex model provider; no credentials are copied')
    parser.add_argument('--codex-home', type=Path,
                        help='Isolated Codex home containing the experiment provider and login')
    parser.add_argument('--expected-provider-host',
                        help='Reject another provider before loading the simulator')
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--gpu', type=int, default=3)
    parser.add_argument('--appdata', type=Path, help='Existing OmniGibson cache for this GPU')
    parser.add_argument('--timeout', type=int, default=1500)
    args = parser.parse_args()
    if args.expected_provider_host and args.codex_home is None:
        parser.error('--expected-provider-host requires --codex-home')
    provider_host=(verify_experiment_provider(args.codex_home,args.provider_profile,args.expected_provider_host)
                   if args.expected_provider_host else None)
    source = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(source / 'src'))
    og = args.omnigibson_source.resolve()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    run, controller = root / 'run', root / 'controller'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    environment = os.environ.copy()
    if args.codex_home is not None:
        environment['CODEX_HOME']=str(args.codex_home.resolve())
    environment['PYTHONPATH'] = os.pathsep.join((str(source / 'src'), str(og),
        str(og.parent / 'bddl3'), str(og.parent / 'joylo'), environment.get('PYTHONPATH', '')))
    environment.update(CUDA_VISIBLE_DEVICES=str(args.gpu), GAP_BEHAVIOR_GPU_ID=str(args.gpu),
        OMNIGIBSON_DATA_PATH=str(og.parent / 'datasets'),
        OMNIGIBSON_APPDATA_PATH=str((args.appdata or root.parent / f'appdata_gpu{args.gpu}').resolve()),
        MAS_DEMO_MOTION='1', MAS_VIDEO_RENDER_STRIDE='3')
    Path(environment['OMNIGIBSON_APPDATA_PATH']).mkdir(parents=True, exist_ok=True)
    simulator_command = [sys.executable, '-m', 'manipulation_agent.vision_cli',
        '--backend', 'omnigibson', '--policy', 'serve', '--agent-profile', 'skills',
        '--task', args.task, '--instance', str(args.instance), '--seed', str(args.seed),
        '--instruction', args.instruction, '--wall-seconds', str(args.timeout + 180),
        '--record-video', '--controller', 'codex:gpt-6-astra', '--output', str(run),
        '--port', str(port)]
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
                '--model', 'gpt-6-astra', '--reasoning-effort', 'low',
                '--model-provider-profile', args.provider_profile,
                '--instruction', args.instruction, '--mcp-command', sys.executable,
                '--mcp-args-json', json.dumps(['-m', 'manipulation_agent.mcp_server', '--bridge', url]),
                '--agent-profile', 'skills', '--timeout', str(args.timeout), '--output', str(controller)]
            command += ['--rate-limit-resumes', '3', '--resume-backoff-seconds', '120']
            model_code = subprocess.run(command, cwd=source, env=environment,
                stdout=model_log, stderr=subprocess.STDOUT, timeout=args.timeout + 180).returncode
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
        summary = {'task': args.task, 'instance': args.instance, 'seed': args.seed,
                   'model': 'gpt-6-astra', 'reasoning_effort': 'low',
                   'provider_profile':args.provider_profile,'provider_host':provider_host,
                   'demo_motion': True, 'controller_exit_code': model_code,
                   'supervisor_failure_type': failure_type,
                   'formal_finish_observed': control.get('formal_finish_observed'),
                   'official_task_success': (record.get('evaluation') or {}).get('official_task_success'),
                   'video_status': (record.get('video') or {}).get('status')}
        (root / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary))
        return 2 if failure_type else 0


if __name__ == '__main__':
    raise SystemExit(main())
