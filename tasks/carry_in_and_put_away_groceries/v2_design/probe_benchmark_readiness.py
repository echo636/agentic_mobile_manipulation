"""Private simulator checks, not an autonomous policy or benchmark score.

Uses named objects and direct motor helpers to isolate scene/predicate mechanics.
The robot is repositioned explicitly for inspection; no navigation claim is made.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import traceback

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = {'kind': 'scripted_private_geometry_probe', 'model_used': False,
              'benchmark_result': False, 'seed': args.seed, 'checks': [],
              'scope': 'Direct motor helpers and explicit robot repositioning; not RGB discovery or navigation acceptance'}
    backend = None

    def save():
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')

    def check(name, passed, **details):
        result['checks'].append({'name': name, 'passed': bool(passed), **details})
        save()
        print(json.dumps(result['checks'][-1]), flush=True)

    try:
        backend = RGBBackend('carrying_in_groceries', 301, args.output, seed=args.seed,
                             record_video=True, inside_placement='official_volume')
        from omnigibson.object_states import Inside
        objects = {obj.name: obj for obj in backend.env.scene.objects}
        bags = [objects[f'paper_bag_{i}'] for i in (81, 82)]
        fridge = objects['fridge_petcxr_0']
        cabinets = [objects[f'top_cabinet_tynnnw_{i}'] for i in (0, 1)]
        cold = [objects[name] for name in ('beefsteak_tomato_80', 'carton_of_milk_79',
                                          'carton_of_milk_83', 'swiss_cheese_84')]
        cookies = [objects[f'butter_cookie_{i}'] for i in (85, 86)]
        expected = [(cold[0], bags[0]), (cold[1], bags[0]), (cold[3], bags[0]),
                    (cold[2], bags[1]), (cookies[0], bags[1]), (cookies[1], bags[1])]

        def containment():
            return {child.name: bool(child.states[Inside].get_value(bag)) for child, bag in expected}

        def wait(steps):
            with backend._anchored_operation():
                for _ in range(steps):
                    backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))

        backend.observe()
        result['initial_objects'] = {obj.name: {'position': obj.get_position_orientation()[0].tolist(),
            'aabb': [x.tolist() for x in obj.aabb]} for obj in (*bags, *cold, *cookies)}
        initial = containment()
        check('all_six_foods_initially_in_intended_bags', all(initial.values()), relations=initial)
        wait(60)
        settled = containment()
        check('initial_containment_after_60_steps', all(settled.values()), relations=settled)

        # Exercise the real RGB dispatch with an injected, private selection;
        # no pixel perception claim is made by this diagnostic.
        ground = backend._ground
        before = backend.robot.get_position_orientation()[0].clone()
        cabinet = cabinets[0]
        backend._ground = lambda *a, **kw: (cabinet, cabinet.get_position_orientation()[0], {})
        rejected = False
        try:
            backend.execute_visual('open', {}, 300)
        except SkillError as exc:
            rejected = exc.code == 'out_of_reach' and not exc.changed
        finally:
            backend._ground = ground
        after = backend.robot.get_position_orientation()[0]
        check('far_operation_rejected_without_base_motion', rejected and backend.torch.equal(before, after))

        # Private staging relocation isolates carrier payload preservation from
        # walking. Real cross-room navigation is covered by the separate probe.
        pos, quat = backend.robot.get_position_orientation()
        pos = pos.clone(); pos[:2] = backend.torch.tensor([7.45, .85], device=pos.device)
        backend.robot.set_position_orientation(pos, quat)
        backend.robot.keep_still()
        for bag in bags:
            backend._ideal_grasp(bag, 100)
            if bag is bags[0]:
                payload = [bag, *[child for child, _ in backend._carry_contents]]
                poses = {obj: tuple(v.clone() for v in obj.get_position_orientation()) for obj in payload}
                try:
                    with backend._placement_context():
                        contents = list(backend._carry_contents)
                        backend._carry_detach()
                        displaced = poses[bag][0].clone(); displaced[0] += .5
                        bag.set_position_orientation(displaced, poses[bag][1])
                        backend._relocate_contents(bag, contents)
                        wait(5)
                        raise SkillError('probe_injected_failure', 'Test rollback after carrier release', changed=True)
                except SkillError as exc:
                    if exc.code != 'probe_injected_failure':
                        raise
                restored = backend._get_held() is bag and all(
                    backend.torch.allclose(actual, expected_pose, atol=.001, rtol=0)
                    for obj, pose in poses.items()
                    for actual, expected_pose in zip(obj.get_position_orientation(), pose))
                check('injected_failure_restores_carrier_payload_and_grasp', restored,
                      payload=[obj.name for obj in payload])
            target_point = sum(fridge.aabb) / 2
            backend._checked_place_next_to(fridge, 100, target_point)
            before_relations = containment()
            wait(90)
            after_relations = containment()
            children = [child.name for child, parent in expected if parent is bag]
            check(f'{bag.name}_payload_after_floor_staging',
                  all(after_relations[name] for name in children),
                  before=before_relations, after=after_relations)
            backend.observe()

        for container in (fridge, *cabinets):
            backend._ideal_state_action('open', container, 100)
        # Top-first must span BOTH storage classes: a cookie can rest on milk.
        # This is an author-only feasibility order, never a model instruction.
        placements = [(obj, fridge) for obj in cold] + list(zip(
            sorted(cookies, key=lambda item: float(sum(item.aabb)[2]), reverse=True), cabinets))
        for obj, destination in sorted(placements, key=lambda pair: float(sum(pair[0].aabb)[2]), reverse=True):
            grasp = backend._ideal_grasp(obj, 100)
            check(f'{obj.name}_individual_grasp', grasp.get('contained_rigid_objects') == 0)
            placement = backend._checked_place_inside(destination, 700)
            result.setdefault('placement_methods', []).append({
                'object': obj.name, 'destination': destination.name, 'result': placement})
            check(f'{obj.name}_inside_{destination.name}', obj.states[Inside].get_value(destination))
        for container in (objects['car_ssxsje_0'], fridge, *cabinets):
            backend._ideal_state_action('close', container, 100)
        score = backend.evaluate()
        check('split_cabinet_native_goal_success', score['task_success'], evaluation=score)
        backend.observe()

        for cabinet in cabinets:
            backend._ideal_state_action('open', cabinet, 100)
            check(f'open_{cabinet.name}_rejects_finish', not backend.evaluate()['task_success'])
            backend._ideal_state_action('close', cabinet, 100)
        wait(90)
        check('goals_remain_true_after_90_steps', backend.evaluate()['task_success'])
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
        print(json.dumps(result['error']), flush=True)
    finally:
        result['status'] = ('passed' if result['checks'] and all(c['passed'] for c in result['checks'])
                            and 'error' not in result else 'failed')
        save()
        if backend is not None:
            try:
                backend.close()
            except SystemExit:
                pass
    if result['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
