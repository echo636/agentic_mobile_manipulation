"""Private short simulator probe for a selected-surface placement failure.

The object handles and world point are taken from an archived autonomous
attempt. This is executor diagnosis, never a model run or task success claim.
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
    parser.add_argument('--held', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--point', nargs=3, required=True, type=float)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    recorder = Recorder(args.output, {'task': args.task, 'instance': args.instance,
        'seed': args.seed, 'policy': 'scripted_private_surface_seating_probe',
        'model_used': False, 'task_success_assessed': False,
        'validation_level': 'real_simulator_executor_component'})
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from omnigibson.object_states import OnTop
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed,
                             record_video=False, inside_placement='official_volume')
        recorder.run['backend'] = backend.provenance()
        write_json(args.output / 'run.json', recorder.run)
        held = backend.env.scene.object_registry('name', args.held)
        target = backend.env.scene.object_registry('name', args.target)
        if held is None or target is None:
            raise AssertionError('Archived probe object was not found')
        point = backend.torch.tensor(args.point, device=held.get_position_orientation()[0].device)
        backend._ideal_grasp(held, 50)
        placement = backend._checked_place_on_top(target, 150, point)
        supported, detail = backend._selected_surface_support(
            held, target, point, False)
        if not supported:
            raise AssertionError('Selected-surface support did not pass after placement')
        diagnostics = [json.loads(line) for line in
                       (args.output / 'placement_diagnostics.jsonl').read_text().splitlines()]
        corrections = [row for row in diagnostics if row['status'] == 'preseat_adjusted']
        if not corrections:
            raise AssertionError('Sampler-clearance correction was not exercised')
        result = {'status': 'passed', 'model_used': False, 'task_success_assessed': False,
                  'selected_world_point_from_archived_model_pixel': args.point,
                  'initial_bottom_gap_m': corrections[-1]['initial_bottom_gap_m'],
                  'lowered_m': corrections[-1]['lowered_m'],
                  'final_bottom_gap_m': detail['bottom_gap_m'],
                  'official_on_top': bool(held.states[OnTop].get_value(target)),
                  'placement': placement, 'sim_steps': backend.steps}
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
