"""Run and audit the first ten pinned public tasks with visible model actions.

Existing attempt directories are immutable. A preexisting incomplete attempt is
observed until it has a summary; this supports resuming after a supervisor
restart without launching a second simulator on the same GPU.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def has_unfinished_trial(active):
    return any((entry['process'] is not None and entry['process'].poll() is None)
               or (entry['trial'].exists() and not (entry['trial'] / 'summary.json').exists()
                   and not (entry['trial'] / 'scheduler_error.json').exists())
               for entry in active.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--omnigibson-source', type=Path, required=True)
    parser.add_argument('--provider-profile', required=True)
    parser.add_argument('--timeout', type=int, default=1500)
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--retry-failures', action='store_true',
                        help='For attempt N>1, run only tasks without a passed earlier audit')
    parser.add_argument('--poll-seconds', type=int, default=15)
    args = parser.parse_args()
    if args.attempt < 1 or (args.retry_failures and args.attempt == 1):
        parser.error('retry-failures requires attempt >= 2')
    repo = Path(__file__).resolve().parents[1]
    tasks = list(json.loads((repo / 'src/manipulation_agent/tasks.json').read_text())['tasks'].items())[:10]
    args.output.mkdir(parents=True, exist_ok=True)
    # The provider used by this cohort returns 429 under parallel model calls.
    # Preserve existing concurrently launched attempts, then serialize new ones.
    gpu_slots = (3, 0, 2)
    active = {}
    for index, (task, spec) in enumerate(tasks, 1):
        gpu = gpu_slots[(index - 1) % len(gpu_slots)]
        trial = args.output / f'{index:02d}_{task}_r{args.attempt}'
        previous_passed = False
        if args.retry_failures:
            for prior in range(1, args.attempt):
                audit = args.output / f'{index:02d}_{task}_r{prior}' / 'demo_audit.json'
                if audit.exists() and json.loads(audit.read_text()).get('passed') is True:
                    previous_passed = True
                    break
        active[index] = {'task': task, 'instruction': spec['instruction'],
                         'gpu': gpu, 'trial': trial, 'process': None,
                         'audited': previous_passed or (trial / 'demo_audit.json').exists()}
    while not all(item['audited'] for item in active.values()):
        for index, item in active.items():
            if item['audited']:
                continue
            trial = item['trial']
            if not trial.exists():
                if has_unfinished_trial(active):
                    continue
                command = [sys.executable, str(repo / 'scripts/run_demo_trial.py'),
                           '--task', item['task'], '--instruction', item['instruction'],
                           '--output', str(trial), '--omnigibson-source', str(args.omnigibson_source),
                           '--provider-profile', args.provider_profile, '--gpu', str(item['gpu']),
                           '--appdata', str(args.output / f'appdata_gpu{item["gpu"]}'),
                           '--timeout', str(args.timeout)]
                log = (args.output / f'{index:02d}_{item["task"]}_r{args.attempt}_launcher.log').open('w')
                item['process'] = subprocess.Popen(command, cwd=repo, stdout=log,
                                                   stderr=subprocess.STDOUT, start_new_session=True)
                log.close()
                print(f'LAUNCHED {index} {item["task"]} GPU {item["gpu"]} PID {item["process"].pid}', flush=True)
            elif (trial / 'summary.json').exists():
                try:
                    with (args.output / f'{index:02d}_{item["task"]}_r{args.attempt}_audit.log').open('w') as log:
                        result = subprocess.run([sys.executable, str(repo / 'scripts/audit_demo_trial.py'),
                                                 str(trial)], cwd=repo, stdout=log,
                                                stderr=subprocess.STDOUT, timeout=600)
                    if result.returncode:
                        raise RuntimeError(f'audit exited {result.returncode}')
                except Exception as exc:
                    (trial / 'audit_error.json').write_text(json.dumps({'error': str(exc)}) + '\n')
                item['audited'] = True
                print(f'AUDITED {index} {item["task"]}', flush=True)
            elif item['process'] is not None and item['process'].poll() is not None:
                (trial / 'scheduler_error.json').write_text(json.dumps({
                    'error': 'trial exited without summary', 'returncode': item['process'].returncode}) + '\n')
                item['audited'] = True
                print(f'FAILED {index} {item["task"]} no summary', flush=True)
        if not all(item['audited'] for item in active.values()):
            time.sleep(args.poll_seconds)


if __name__ == '__main__':
    main()
