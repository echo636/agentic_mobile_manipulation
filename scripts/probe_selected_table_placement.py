"""Private component probe for a cane placed after a visible table reach.

The robot start, RGB-grounded table point and object names are archived from an
autonomous attempt. This stages a synthetic held item and does not assess task
success or count as a model experiment.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--held', required=True)
    parser.add_argument('--table', required=True)
    parser.add_argument('--start', nargs=3, required=True, type=float)
    parser.add_argument('--point', nargs=3, required=True, type=float)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--trace-contact', action='store_true',
                        help='Record official contact after placement and every retreat/free physics step')
    args = parser.parse_args()
    recorder = Recorder(args.output, {'task': args.task, 'instance': args.instance,
        'seed': args.seed, 'policy': 'scripted_private_selected_table_placement_probe',
        'model_used': False, 'task_success_assessed': False,
        'validation_level': 'real_simulator_executor_component'})
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from omnigibson.object_states import OnTop, Touching
        import omnigibson.utils.transform_utils as T
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed,
                             record_video=False, inside_placement='official_volume')
        recorder.run['backend'] = backend.provenance()
        write_json(args.output / 'run.json', recorder.run)
        held = backend.env.scene.object_registry('name', args.held)
        table = backend.env.scene.object_registry('name', args.table)
        if held is None or table is None:
            raise AssertionError('Archived object was not found')
        start,_ = backend.robot.get_position_orientation()
        start = start.clone()
        start[:2] = backend.torch.tensor(args.start[:2], device=start.device)
        orientation = T.euler2quat(backend.torch.tensor([0.,0.,args.start[2]], device=start.device))
        backend.robot.set_position_orientation(start, orientation)
        backend.robot.keep_still()
        point = backend.torch.tensor(args.point, device=start.device)
        hand = backend.robot.eef_links['right'].get_position_orientation()[0]
        _, item_orientation = held.get_position_orientation()
        held.set_position_orientation(hand, item_orientation)
        held.keep_still()
        backend._demo_arm = 'right'
        backend._ideal_grasp(held, 50)
        reach_steps = backend._demo_reach(point, 90, anchor=table, arm='right',
                                         action='probe_preceding_table_reach')
        _, names = backend._dynamic_floor_obstacles(float(start[2]))
        tracked = {name: backend.env.scene.object_registry('name', name)
                   for name in names}
        before = {name: obj.get_position_orientation() for name,obj in tracked.items()}
        from omnigibson.utils.sampling_utils import raytest
        ray_start = point.clone();ray_start[2] += .2
        ray_end = point.clone();ray_end[2] -= .2
        ignored = [link.prim_path for obj in (held,backend.robot)
                   for link in obj.links.values()]
        initial_hit = raytest(ray_start,ray_end,ignore_bodies=ignored)
        initial_support = {'hit': bool(initial_hit['hit']),
                           'body': initial_hit.get('rigidBody'),
                           'support_z': float(initial_hit['position'][2]) if initial_hit['hit'] else None}
        original_record = backend._placement_record
        def observed_placement_record(data):
            if data.get('status') == 'postcondition_check':
                furniture_at_check = {}
                for name,obj in tracked.items():
                    old_pos,old_quat = before[name]
                    new_pos,new_quat = obj.get_position_orientation()
                    furniture_at_check[name] = {
                        'displacement_m': float(backend.torch.linalg.norm(new_pos-old_pos)),
                        'rotation_deg': 2*math.degrees(math.acos(
                            min(1.,abs(float((new_quat*old_quat).sum())))))}
                write_json(args.output / 'pre_rollback_furniture.json', {
                    'motion': furniture_at_check,
                    'table_aabb': [[float(v) for v in side] for side in table.aabb]})
            original_record(data)
        backend._placement_record = observed_placement_record
        placement = None
        placement_error = None
        contact_trace = []
        original_step = None
        phase = 'retreat'
        def contact_snapshot(label):
            item_position = held.get_position_orientation()[0]
            support_position = table.get_position_orientation()[0]
            hand_position = backend.robot.eef_links['right'].get_position_orientation()[0]
            return {'phase': label, 'env_step': backend.steps,
                    'official_on_top': bool(held.states[OnTop].get_value(table)),
                    'touching_support': bool(held.states[Touching].get_value(table)),
                    'item_position': item_position.tolist(),
                    'item_bottom_z': float(held.aabb[0][2]),
                    'support_position': support_position.tolist(),
                    'hand_distance_m': float(backend.torch.linalg.norm(hand_position-item_position))}
        try:
            placement = backend._checked_place_on_top(table, 150, point)
            if args.trace_contact:
                contact_trace.append(contact_snapshot('after_place'))
                original_step = backend._step
                def traced_step(action):
                    original_step(action)
                    contact_trace.append(contact_snapshot(phase))
                backend._step = traced_step
            backend._demo_transport_pose(90,
                anchor=backend._nearby_transport_anchors(table))
            phase = 'free'
            for _ in range(10):
                backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
        except Exception as exc:
            placement_error = {'type': type(exc).__name__, 'message': str(exc),
                               'code': getattr(exc, 'code', None)}
        finally:
            if original_step is not None:
                backend._step = original_step
            if args.trace_contact:
                write_json(args.output / 'contact_trace.json', contact_trace)
        motion = {}
        for name,obj in tracked.items():
            old_pos,old_quat = before[name]
            new_pos,new_quat = obj.get_position_orientation()
            motion[name] = {
                'displacement_m': float(backend.torch.linalg.norm(new_pos-old_pos)),
                'rotation_deg': 2*math.degrees(math.acos(
                    min(1.,abs(float((new_quat*old_quat).sum())))))}
        official = bool(held.states[OnTop].get_value(table))
        diagnostics = [json.loads(row) for row in
                       (args.output / 'placement_diagnostics.jsonl').read_text().splitlines()]
        postchecks = [row for row in diagnostics if row.get('status') == 'postcondition_check']
        stable = all(row['displacement_m'] < .03 and row['rotation_deg'] < 2
                     for row in motion.values())
        status = 'passed' if placement_error is None and official and stable else 'failed'
        result = {'status': status, 'model_used': False, 'task_success_assessed': False,
                  'synthetic_held_setup': True, 'archived_start_xy_yaw': args.start,
                  'archived_selected_world_point': args.point,
                  'reach_steps': reach_steps,
                  'initial_support_ray': initial_support,
                  'hand_target_error_m': backend._demo_last_reach_error,
                  'placement': placement, 'placement_error': placement_error,
                  'official_on_top': official, 'furniture_motion': motion,
                  'last_postcondition_check': postchecks[-1] if postchecks else None,
                  'sim_steps': backend.steps}
        write_json(args.output / 'validation.json', result)
        recorder.finish({'status':status,'task_success':None,'validation':result})
        print(json.dumps(result))
        return 0 if status == 'passed' else 1
    except Exception as exc:
        result = {'status': 'failed', 'model_used': False,
                  'task_success_assessed': False, 'error': str(exc),
                  'failure_type': type(exc).__name__}
        write_json(args.output / 'validation.json', result)
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.finish({'status':'failed','task_success':None,'validation':result})
        print(json.dumps(result))
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
