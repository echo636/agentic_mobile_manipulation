#!/usr/bin/env python3
"""Run one full privileged reference from a verified frozen custom scene.

Every invocation creates a fresh simulator and an immutable output directory.
The result explicitly distinguishes physical feasibility from model success.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))
from manipulation_agent.custom_tasks import load_spec, sha256
from manipulation_agent.records import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--order', required=True)
    args = parser.parse_args()
    spec, contract = load_spec(args.spec, require_frozen=True)
    if args.order not in spec['acceptance_cases']['reference_orders']:
        parser.error('Order is not in the frozen task contract')
    args.output.mkdir(parents=True, exist_ok=False)
    freeze = SOURCE / 'source_freeze.json'
    commit = (json.loads(freeze.read_text())['source_commit'] if freeze.is_file() else
              subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip())
    source_files = [*sorted((SOURCE / 'src/manipulation_agent').rglob('*.py')), Path(__file__)]
    source_hash = hashlib.sha256()
    for path in source_files:
        source_hash.update(str(path.relative_to(SOURCE)).encode() + b'\0' + path.read_bytes())
    record = {'status': 'running', 'started_at_unix': time.time(), 'task': spec['id'],
              'instance': spec['instance_id'], 'seed': spec['base']['seed'], 'order': args.order,
              'host': platform.node(), 'interpreter': sys.executable, 'pid': os.getpid(),
              'unit': os.environ.get('MAS_UNIT'), 'gpu_uuid': os.environ.get('MAS_GPU_UUID'),
              'source_commit': commit, 'source_sha256': source_hash.hexdigest(),
              'spec_sha256': sha256(args.spec), 'scene_sha256': spec['frozen_instance']['sha256'],
              'output_path': str(args.output.resolve()), 'model_used': False,
              'validation_level': 'real_simulator_checked_executor_privileged_full_reference',
              'contract_validation': contract}
    write_json(args.output / 'run_record.json', record)
    backend = None
    reference = None
    evaluation = None
    try:
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        from manipulation_agent.reference_tasks import ReferenceTask
        backend = CustomTaskBackend(args.spec, args.output, record_video=False,
                                    inside_placement='official_volume', max_steps=100000)
        record['backend'] = backend.provenance()
        backend.observe()
        reference = ReferenceTask(backend, args.output,
                                  max_attempts=spec['acceptance_cases']['max_reference_action_attempts'])
        reference.run(args.order)
        evaluation = backend.evaluate()
        backend.frames_revision = -1
        backend.observe()
        record['status'] = 'passed' if evaluation['task_success'] else 'failed'
    except BaseException as exc:
        record.update(status='failed', error=str(exc), error_code=getattr(exc, 'code', None))
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        if backend is not None:
            try:
                evaluation = backend.evaluate()
                backend.frames_revision = -1
                backend.observe()
            except Exception:
                (args.output / 'evaluation_error.txt').write_text(traceback.format_exc())
    finally:
        if backend is not None:
            try:
                backend.close()
                record['cleanup_status'] = 'passed'
            except BaseException as exc:
                record.update(status='failed', cleanup_status='failed', cleanup_error=str(exc))
                (args.output / 'cleanup_error.txt').write_text(traceback.format_exc())
        record.update(finished_at_unix=time.time(), action_attempts=len(reference.calls) if reference else 0,
                      evaluation=evaluation, autonomous_success_claimed=False)
        write_json(args.output / 'validation.json', record)
        write_json(args.output / 'run_record.json', record)
    print(json.dumps({'status': record['status'], 'output': str(args.output),
                      'action_attempts': record['action_attempts']}), flush=True)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
