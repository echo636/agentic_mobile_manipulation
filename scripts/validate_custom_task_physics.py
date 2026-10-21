#!/usr/bin/env python3
"""Run one full privileged reference from a verified frozen custom scene.

Every invocation creates a fresh simulator and an immutable output directory.
The result explicitly distinguishes physical feasibility from model success.
"""
import argparse
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
from manipulation_agent.records import write_json, release_source_digest
from manipulation_agent.reference_evidence import close_reference, complete_reference_exit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--order')
    mode.add_argument('--negative-case')
    parser.add_argument('--record-video', action='store_true',
                        help='Preserve continuous simulator video for reference diagnosis')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker:
        if args.output.exists():
            parser.error('Output directory already exists; use a new immutable run directory')
        # Native Isaac shutdown may call exit(0) before Python finally blocks
        # return. Keep an independent parent to finalize the saved score only
        # after observing process termination, including crashes and failures.
        child = subprocess.run([sys.executable, '-B', '-u', str(Path(__file__).resolve()),
                                *sys.argv[1:], '--worker'])
        return 0 if complete_reference_exit(args.output, child.returncode) else 1
    spec, contract = load_spec(args.spec, require_frozen=True)
    if args.order and args.order not in spec['acceptance_cases']['reference_orders']:
        parser.error('Order is not in the frozen task contract')
    if args.negative_case and args.negative_case not in spec['acceptance_cases']['physical_negative_cases']:
        parser.error('Negative case is not in the frozen task contract')
    args.output.mkdir(parents=True, exist_ok=False)
    freeze = SOURCE / 'source_freeze.json'
    commit = (json.loads(freeze.read_text())['source_commit'] if freeze.is_file() else
              subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip())
    record = {'status': 'running', 'started_at_unix': time.time(), 'task': spec['id'],
              'instance': spec['instance_id'], 'seed': spec['base']['seed'], 'order': args.order,
              'negative_case': args.negative_case,
              'host': platform.node(), 'interpreter': sys.executable, 'pid': os.getpid(),
              'unit': os.environ.get('MAS_UNIT'), 'gpu_uuid': os.environ.get('MAS_GPU_UUID'),
              'source_commit': commit, 'source_sha256': release_source_digest(SOURCE),
              'spec_sha256': sha256(args.spec), 'scene_sha256': spec['frozen_instance']['sha256'],
              'output_path': str(args.output.resolve()), 'model_used': False,
              'record_video': args.record_video,
              'validation_level': ('real_simulator_physical_negative_case' if args.negative_case else
                                   'real_simulator_checked_executor_privileged_full_reference'),
              'contract_validation': contract}
    write_json(args.output / 'run_record.json', record)
    backend = None
    reference = None
    evaluation = None
    try:
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        from manipulation_agent.reference_tasks import ReferenceTask
        backend = CustomTaskBackend(args.spec, args.output, record_video=args.record_video,
                                    inside_placement='official_volume', max_steps=100000)
        record['backend'] = backend.provenance()
        backend.observe()
        reference = ReferenceTask(backend, args.output,
                                  max_attempts=spec['acceptance_cases']['max_reference_action_attempts'])
        if args.order:
            reference.run(args.order)
        else:
            record['negative_result'] = reference.negative(args.negative_case)
        evaluation = backend.evaluate()
        backend.frames_revision = -1
        backend.observe()
        accepted = (evaluation['task_success'] if args.order else
                    record['negative_result']['status'] == 'passed')
        record['status'] = 'passed' if accepted else 'failed'
    except BaseException as exc:
        record.update(status='failed', error=str(exc), error_code=getattr(exc, 'code', None))
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        if backend is not None:
            # Keep a post-failure scene for focused diagnostics. Restoring it
            # is a continuation, never a fresh full-reference acceptance run.
            try:
                scene = args.output / 'failure_scene.json'
                backend.env.scene.save(json_path=str(scene))
                record['failure_scene'] = {'path':scene.name,'sha256':sha256(scene),
                    'scope':'post-failure diagnostic continuation only; action may have rolled back'}
            except Exception:
                (args.output / 'failure_scene_error.txt').write_text(traceback.format_exc())
            try:
                evaluation = backend.evaluate()
                backend.frames_revision = -1
                backend.observe()
            except Exception:
                (args.output / 'evaluation_error.txt').write_text(traceback.format_exc())
    finally:
        record.update(finished_at_unix=time.time(), action_attempts=len(reference.calls) if reference else 0,
                      evaluation=evaluation, autonomous_success_claimed=False)
        close_reference(backend, record, args.output)
    print(json.dumps({'status': record['status'], 'output': str(args.output),
                      'action_attempts': record['action_attempts']}), flush=True)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
