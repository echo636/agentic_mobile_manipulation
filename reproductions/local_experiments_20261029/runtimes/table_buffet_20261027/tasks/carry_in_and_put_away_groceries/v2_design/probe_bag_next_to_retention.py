"""Private relation-retention repro, not an autonomous score or exact episode replay.

Uses real placement/state helpers, with private object relocation to remove food
payloads and isolate a bag beside the refrigerator. No production behavior changes.
"""
import argparse
import json
from pathlib import Path
import traceback

from manipulation_agent.executors.omnigibson_rgb import RGBBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'kind': 'scripted_private_geometry_probe', 'model_used': False,
              'benchmark_result': False, 'states': [], 'checks': [],
              'scope': 'Empty bag, recorded selected fridge point, real helpers; '
                       'private staging, not exact autonomous state replay.'}
    backend = None

    def save():
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')

    try:
        backend = RGBBackend('carrying_in_groceries', 301, args.output,
                             seed=args.seed, record_video=True)
        from omnigibson.object_states import NextTo, Open
        objects = {obj.name: obj for obj in backend.env.scene.objects}
        bag = objects['paper_bag_82']
        fridge = objects['fridge_petcxr_0']
        # Remove contents privately before grasp, without pretending they were
        # discovered or placed by the policy. Keep them outside the test area.
        for i, name in enumerate(('carton_of_milk_83', 'butter_cookie_85',
                                  'butter_cookie_86')):
            obj = objects[name]
            pos, quat = obj.get_position_orientation()
            pos = pos.clone(); pos[0] -= 2.0 + i * .3
            obj.set_position_orientation(pos, quat); obj.keep_still()
        pos, quat = backend.robot.get_position_orientation()
        pos = pos.clone()
        pos[:2] = backend.torch.tensor([6.45, .85], device=pos.device)
        backend.robot.set_position_orientation(pos, quat); backend.robot.keep_still()

        def snapshot(label):
            row = {'label': label, 'next_to': bool(bag.states[NextTo].get_value(fridge)),
                   'fridge_open': bool(fridge.states[Open].get_value()),
                   'bag_pose': [v.tolist() for v in bag.get_position_orientation()],
                   'bag_aabb': [v.tolist() for v in bag.aabb],
                   'fridge_pose': [v.tolist() for v in fridge.get_position_orientation()],
                   'fridge_aabb': [v.tolist() for v in fridge.aabb],
                   'fridge_joints': fridge.get_joint_positions().tolist()}
            report['states'].append(row); save()
            backend.observe()
            print(json.dumps({'label': label, 'next_to': row['next_to'],
                              'fridge_open': row['fridge_open']}), flush=True)
            return row

        backend._ideal_state_action('open', fridge, 100)
        grasp = backend._ideal_grasp(bag, 100)
        if grasp.get('contained_rigid_objects') != 0:
            raise RuntimeError('Probe setup invalid: bag still has payload')
        point = backend.torch.tensor([4.7271428108, -.4277050197, .9704863429],
                                     device=pos.device)
        backend._checked_place_next_to(fridge, 300, point)
        initial = snapshot('after_place_open_fridge')
        report['checks'].append({'name': 'placement_initially_next_to',
                                 'passed': initial['next_to']})
        for primitive, label in [('close', 'after_first_close'),
                                 ('open', 'after_reopen'),
                                 ('close', 'after_second_close')]:
            backend._ideal_state_action(primitive, fridge, 100)
            row = snapshot(label)
            report['checks'].append({'name': 'relation_retained_' + label,
                                     'passed': row['next_to']})
            save()
        report['status'] = ('passed' if all(c['passed'] for c in report['checks'])
                            else 'failed')
        report['reproduced_relation_loss'] = (initial['next_to'] and
            any(not row['next_to'] for row in report['states'][1:]))
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc())
        traceback.print_exc()
    finally:
        save()
        if backend is not None:
            try:
                backend.close()
            except SystemExit:
                pass
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
