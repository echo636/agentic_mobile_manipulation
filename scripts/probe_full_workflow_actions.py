#!/usr/bin/env python3
"""Run targeted checked actions on a fresh frozen task; never full acceptance.

The input adds diagnostic_plan={actions, expected} to a normal frozen task.
No initial assertions, collision checks, or physical predicates are bypassed.
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
    parser.add_argument('--order', choices=['diagnostic'], required=True)
    parser.add_argument('--record-video', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.worker:
        if args.output.exists():
            parser.error('Use a fresh immutable output directory')
        child = subprocess.run([sys.executable, '-B', '-u', str(Path(__file__).resolve()),
                                *sys.argv[1:], '--worker'])
        return 0 if complete_reference_exit(args.output, child.returncode) else 1
    spec, _ = load_spec(args.spec, require_frozen=True)
    plan = spec['diagnostic_plan']
    if not plan.get('actions') or not plan.get('expected'):
        parser.error('Diagnostic actions and explicit physical postconditions are required')
    args.output.mkdir(parents=True, exist_ok=False)
    freeze = SOURCE / 'source_freeze.json'
    commit = (json.loads(freeze.read_text())['source_commit'] if freeze.exists() else
              subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip())
    record = dict(status='running', started_at_unix=time.time(), task=spec['id'],
                  host=platform.node(), interpreter=sys.executable, pid=os.getpid(),
                  unit=os.environ.get('MAS_UNIT'), gpu_uuid=os.environ.get('MAS_GPU_UUID'),
                  source_commit=commit, source_sha256=release_source_digest(SOURCE),
                  spec_sha256=sha256(args.spec), scene_sha256=spec['frozen_instance']['sha256'],
                  output_path=str(args.output), diagnostic_plan=plan, model_used=False,
                  full_task_executed=False, benchmark_ready=False, autonomous_success_claimed=False,
                  validation_level='real_simulator_targeted_workflow_actions_not_full_acceptance',
                  evaluation=None)
    write_json(args.output / 'run_record.json', record)
    backend = reference = None
    try:
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        from manipulation_agent.reference_tasks import ReferenceTask
        from manipulation_agent.full_workflow_reference import FullWorkflowReference
        from manipulation_agent.long_horizon import expression
        backend = CustomTaskBackend.__new__(CustomTaskBackend)
        backend.__init__(args.spec, args.output, record_video=args.record_video,
                         inside_placement='official_volume', max_steps=100000)
        record['backend'] = backend.provenance()
        backend.observe()
        reference = ReferenceTask(backend, args.output, max_attempts=100)
        workflow = FullWorkflowReference(reference)
        for action in plan['actions']:
            workflow.action(action)
        values = backend._predicate_values()
        record['checks'] = {key: bool(expression(condition, values))
                            for key, condition in plan['expected'].items()}
        record['status'] = 'passed' if all(record['checks'].values()) else 'failed'
    except BaseException as exc:
        record.update(status='failed', error=str(exc), error_code=getattr(exc, 'code', None))
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
    finally:
        if backend is not None and hasattr(backend, 'env'):
            try:
                record['evaluation'] = backend.evaluate()
                backend.env.scene.save(json_path=str(args.output / 'diagnostic_final_scene.json'))
                write_json(args.output / 'diagnostic_final_geometry.json', {
                    obj.name: dict(category=obj.category, aabb=[v.tolist() for v in obj.aabb],
                                   pose=[v.tolist() for v in obj.get_position_orientation()])
                    for obj in backend.env.scene.objects})
                backend.frames_revision = -1
                backend.observe()
            except Exception:
                (args.output / 'diagnostic_evidence_error.txt').write_text(traceback.format_exc())
        record.update(action_attempts=len(reference.calls) if reference else 0, finished_at_unix=time.time())
        close_reference(backend, record, args.output)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
