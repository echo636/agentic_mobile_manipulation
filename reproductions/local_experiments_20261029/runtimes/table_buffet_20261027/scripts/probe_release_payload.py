"""Privileged, short simulator probe for payload-preserving release.

This selects a known object handle. It is never an autonomous model attempt or
a whole-task success claim.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--container', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    recorder = Recorder(args.output, {'task': args.task, 'instance': args.instance,
        'seed': args.seed, 'policy': 'scripted_private_release_probe',
        'model_used': False, 'task_success_assessed': False,
        'validation_level': 'real_simulator_executor_component'})
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed,
                             record_video=False, inside_placement='official_volume')
        recorder.run['backend'] = backend.provenance()
        write_json(args.output / 'run.json', recorder.run)
        container = backend.env.scene.object_registry('name', args.container)
        if container is None:
            raise AssertionError(f'Container {args.container} was not found')
        dependencies, _ = backend._grasp_payload_snapshot(container)
        if not dependencies:
            raise AssertionError('The chosen container has no initial rigid payload')
        before = [{'child': child.name, 'parent': parent.name, 'relation': relation}
                  for child, parent, relation in dependencies]
        backend._ideal_grasp(container, 50)
        release = backend._ideal_release(30)
        backend._verify_payload(dependencies)
        result = {'status': 'passed', 'model_used': False,
                  'task_success_assessed': False, 'payload': before,
                  'release': release, 'sim_steps': backend.steps}
        write_json(args.output / 'validation.json', result)
        recorder.finish({'status': 'passed', 'task_success': None, 'validation': result})
        print(json.dumps(result))
    except Exception as exc:
        result = {'status': 'failed', 'model_used': False,
                  'task_success_assessed': False, 'error': str(exc),
                  'failure_type': type(exc).__name__}
        write_json(args.output / 'validation.json', result)
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.finish({'status': 'failed', 'task_success': None, 'validation': result})
        print(json.dumps(result))
        return 1
    finally:
        if backend is not None:
            backend.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
