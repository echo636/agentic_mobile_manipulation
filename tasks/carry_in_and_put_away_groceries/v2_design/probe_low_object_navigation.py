"""Private before/after RGB dispatch probe; no model or benchmark score.

Object/robot placement and target projection use private geometry. Only the
subsequent grounding, navigation and grasp exercise the actual RGB executor.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import traceback

from manipulation_agent.executors.omnigibson_rgb import RGBBackend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--before-backend', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        'manipulation_agent.executors._groc007_before', args.before_backend)
    before_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before_module)
    old_class = before_module.RGBBackend
    report = {'kind': 'scripted_private_geometry_probe', 'model_used': False,
              'benchmark_result': False, 'checks': [], 'cases': [],
              'scope': 'Private pose resets and projected pixels; real RGB grounding/navigation/grasp'}
    backend = None

    def save():
        (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')

    def check(name, passed):
        report['checks'].append({'name': name, 'passed': bool(passed)})
        save()

    try:
        backend = RGBBackend('carrying_in_groceries', 301, args.output,
                             seed=args.seed, record_video=True)
        from omnigibson.utils import transform_utils as T
        torch = backend.torch
        objects = {obj.name: obj for obj in backend.env.scene.objects}
        milk = objects['carton_of_milk_83']
        fridge = objects['fridge_petcxr_0']
        start, _ = backend.robot.get_position_orientation()
        start = start.clone()
        # Recorded pre-call-91 navigation endpoint and yaw. These are a private
        # probe setup, not an autonomous policy or an exact episode-state replay.
        start[:2] = torch.tensor([7.25, .85], device=start.device)
        quat = T.euler2quat(torch.tensor([0., 0., .5697742818], device=start.device))
        item_pos = torch.tensor([7.07, -.318, .3], device=start.device)
        item_quat = T.euler2quat(torch.tensor([math.pi / 2, 0., 0.], device=start.device))

        def reset():
            backend.__class__ = RGBBackend
            if backend._get_held() is not None:
                backend.execute_visual('release', None, 100)
            milk.set_position_orientation(item_pos, item_quat)
            position, orientation = milk.get_position_orientation()
            position = position.clone()
            position[2] += .007 - milk.aabb[0][2]
            milk.set_position_orientation(position, orientation)
            milk.keep_still()
            backend.robot.set_position_orientation(start, quat)
            backend.robot.keep_still()
            with backend._anchored_operation():
                for _ in range(10):
                    backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
            backend.observe()

        def pixel_for(world, wanted=None):
            # Return only a projected ray that actually resolves to the wanted
            # visible owner. No neighboring-pixel or semantic search is used.
            for ref, frame in backend.current_frames.items():
                local = T.quat2mat(frame['orientation'].cpu()).T @ (world.cpu() - frame['position'].cpu())
                depth = -float(local[2])
                if depth <= .02:
                    continue
                intrinsic = frame['intrinsic'].cpu()
                x = (float(intrinsic[0, 0]) * float(local[0]) / depth + float(intrinsic[0, 2])) / (frame['width'] - 1)
                y = (float(intrinsic[1, 2]) - float(intrinsic[1, 1]) * float(local[1]) / depth) / (frame['height'] - 1)
                if not (.02 < x < .98 and .02 < y < .98):
                    continue
                target = {'image_ref': ref, 'point': [x, y]}
                try:
                    owner, point, diagnostic = backend._ground(target, require_object=True)
                except Exception:
                    continue
                if (owner is wanted if wanted is not None else owner.category in {'floor', 'floors'}):
                    return target, diagnostic
            return None, None

        for label, cls in (('before_low_item', old_class), ('after_low_item', RGBBackend)):
            reset()
            target, diagnostic = pixel_for(sum(milk.aabb) / 2, milk)
            if target is None:
                raise RuntimeError('Probe setup cannot see milk on a projected exact ray')
            backend.__class__ = cls
            initial_pose = milk.get_position_orientation()[0].tolist()
            outcome = backend.execute_visual('navigate_to', target, 3000)
            backend.observe()
            fresh, _ = pixel_for(sum(milk.aabb) / 2, milk)
            plan = json.loads((args.output / 'navigation_plans.jsonl').read_text().splitlines()[-1])
            case = {'name': label, 'target': target, 'selected_surface': diagnostic,
                    'initial_item_position': initial_pose,
                    'final_item_position': milk.get_position_orientation()[0].tolist(),
                    'floor_waypoint': plan['floor_waypoint'],
                    'standoff': plan['visual_standoff_m'],
                    'target_selectable_after_navigation': fresh is not None,
                    'navigation': outcome}
            if label == 'after_low_item' and fresh is not None:
                case['grasp'] = backend.execute_visual('grasp', fresh, 300)
            report['cases'].append(case)
            save()
        before, after = report['cases']
        check('before_low_item_misclassified_as_floor', before['floor_waypoint'])
        check('after_low_item_not_floor', not after['floor_waypoint'])
        check('after_low_item_still_selectable', after['target_selectable_after_navigation'])
        check('after_low_item_can_be_grasped', 'grasp' in after)

        reset()
        floor_point = start.clone()
        floor_point[:2] = torch.tensor([6.3, 1.0], device=start.device)
        floor_point[2] = .005
        target, _ = pixel_for(floor_point)
        if target is None:
            raise RuntimeError('Probe setup cannot see the floor control point')
        backend.execute_visual('navigate_to', target, 3000)
        backend.observe()
        plan = json.loads((args.output / 'navigation_plans.jsonl').read_text().splitlines()[-1])
        check('actual_floor_still_waypoint', plan['floor_waypoint'])

        reset()
        target, _ = pixel_for(sum(fridge.aabb) / 2, fridge)
        if target is None:
            raise RuntimeError('Probe setup cannot see the elevated fridge control')
        backend.execute_visual('navigate_to', target, 3000)
        backend.observe()
        plan = json.loads((args.output / 'navigation_plans.jsonl').read_text().splitlines()[-1])
        check('elevated_object_not_floor', not plan['floor_waypoint'])
        report['status'] = 'passed' if all(c['passed'] for c in report['checks']) else 'failed'
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                      traceback=traceback.format_exc())
        traceback.print_exc()
    finally:
        save()
        if backend is not None:
            backend.__class__ = RGBBackend
            backend.close()
    print(json.dumps({'status': report['status'], 'checks': report['checks']}), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
