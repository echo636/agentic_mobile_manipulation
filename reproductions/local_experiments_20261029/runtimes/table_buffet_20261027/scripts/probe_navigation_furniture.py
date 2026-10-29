"""Private simulator probe: can a model-selected approach spare nearby furniture?

The start pose and selected world point come from an archived autonomous run.
This uses object handles for diagnosis only and is not a model task attempt.
"""
import argparse
import json
import math
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', required=True)
    parser.add_argument('--furniture', required=True)
    parser.add_argument('--start', nargs=3, required=True, type=float,
                        metavar=('X', 'Y', 'YAW'))
    parser.add_argument('--point', nargs=3, required=True, type=float,
                        metavar=('X', 'Y', 'Z'))
    parser.add_argument('--staged-held-item', help='Stage this item at the right hand before navigation')
    parser.add_argument('--preceding-reach-point', nargs=3, type=float,
                        metavar=('X', 'Y', 'Z'),
                        help='Reproduce an extended arm after a failed placement')
    parser.add_argument('--followup-point', nargs=3, type=float,
                        metavar=('X', 'Y', 'Z'),
                        help='Run a subsequent public-style navigation from the first reached pose')
    parser.add_argument('--public-navigation', action='store_true',
                        help='Replay a public navigate_to pixel (no resolved object, visual standoff)')
    parser.add_argument('--trace-furniture', action='store_true',
                        help='Record selected furniture motion after each navigation control step')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    if bool(args.staged_held_item) != bool(args.preceding_reach_point):
        parser.error('--staged-held-item and --preceding-reach-point must be used together')
    recorder = Recorder(args.output, {'task': args.task, 'instance': args.instance,
        'seed': args.seed, 'policy': 'scripted_private_navigation_furniture_probe',
        'model_used': False, 'task_success_assessed': False,
        'validation_level': 'real_simulator_executor_component'})
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        import omnigibson.utils.transform_utils as T
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed,
                             record_video=False, inside_placement='official_volume')
        recorder.run['backend'] = backend.provenance()
        write_json(args.output / 'run.json', recorder.run)
        furniture = backend.env.scene.object_registry('name', args.furniture)
        if furniture is None:
            raise AssertionError('Archived furniture object was not found')
        initial_robot,_ = backend.robot.get_position_orientation()
        start = initial_robot.clone()
        start[:2] = backend.torch.tensor(args.start[:2], device=start.device)
        orientation = T.euler2quat(backend.torch.tensor([0.,0.,args.start[2]],device=start.device))
        backend.robot.set_position_orientation(start,orientation)
        backend.robot.keep_still()
        point = backend.torch.tensor(args.point, device=start.device)
        staged = None
        if args.staged_held_item:
            item = backend.env.scene.object_registry('name', args.staged_held_item)
            if item is None:
                raise AssertionError('Staged item was not found')
            hand = backend.robot.eef_links['right'].get_position_orientation()[0]
            _, item_orientation = item.get_position_orientation()
            item.set_position_orientation(hand, item_orientation)
            item.keep_still()
            backend._demo_arm = 'right'
            backend._ideal_grasp(item, 50)
            preceding = backend.torch.tensor(args.preceding_reach_point, device=start.device)
            reach_steps = backend._demo_reach(preceding, 90, anchor=furniture,
                                             arm='right', action='probe_preceding_reach')
            staged = {'item': item.name, 'preceding_reach_point': args.preceding_reach_point,
                      'reach_steps': reach_steps,
                      'hand_target_error_m': backend._demo_last_reach_error}
        before_pos,before_quat = furniture.get_position_orientation()
        boxes,names = backend._dynamic_floor_obstacles(float(start[2]))
        if furniture.name not in names and not args.public_navigation:
            raise AssertionError('The selected furniture was not included in the dynamic obstacle overlay')
        from manipulation_agent.executors.gt_navigation import (
            GridMap, candidate_points, mask_dynamic_obstacles, plan_navigation)
        trav = backend.env.scene.trav_map
        occupancy = trav._erode_trav_map(trav.floor_map[0].clone()).cpu().numpy()
        height, width = occupancy.shape
        static_grid = GridMap(width, height, float(trav.map_resolution),
            (-width * trav.map_resolution / 2, -height * trav.map_resolution / 2),
            (occupancy != 0).astype('uint8').tobytes())
        xy = tuple(float(v) for v in start[:2])
        hint = tuple(float(v) for v in point[:2])
        diagnostic = {'boxes': dict(zip(names, boxes)), 'start_xy': xy,
                      'selected_xy': hint, 'margins': {}}
        for clearance in (.48, .38, .30, .22):
            grid, masked = mask_dynamic_obstacles(static_grid, boxes,
                                                  margin=clearance, start=xy)
            free_projections = sum(grid.snap(raw, .75) is not None
                for raw in candidate_points(hint, xy, .7))
            row = {'masked_cells': masked,
                   'free_projections_before_visibility': free_projections}
            if free_projections:
                try:
                    proposed = plan_navigation(grid, xy, hint, standoff=.7,
                        candidate_filter=lambda goal: backend._approach_visible(
                            goal, point, furniture, margin=.04, max_distance=1.4,
                            self_hulls=()))
                    row['visible_plan'] = {'goal': proposed.goal,
                        'points': proposed.points, 'geodesic_m': proposed.geodesic_m}
                except Exception as exc:
                    row['visible_plan_error'] = str(exc)
            diagnostic['margins'][str(clearance)] = row
        write_json(args.output / 'obstacle_diagnostic.json', diagnostic)
        anchor = SimpleNamespace(aabb=(point,point),
                                 get_position_orientation=lambda:(point,None),
                                 selected_object=None if args.public_navigation else furniture)
        tracked = {name: backend.env.scene.object_registry('name', name)
                   for name in names}
        before_poses = {name: obj.get_position_orientation()
                        for name, obj in tracked.items()}
        original_transport = backend._demo_transport_pose
        def observed_transport(*transport_args, **transport_kwargs):
            before = {name: obj.get_position_orientation()
                      for name, obj in tracked.items()}
            taken = original_transport(*transport_args, **transport_kwargs)
            changes = {}
            for name, obj in tracked.items():
                old_pos, old_quat = before[name]
                new_pos, new_quat = obj.get_position_orientation()
                changes[name] = {
                    'displacement_m': float(backend.torch.linalg.norm(new_pos-old_pos)),
                    'rotation_deg': 2*math.degrees(math.acos(
                        min(1.,abs(float((new_quat*old_quat).sum())))))}
            current_boxes, current_names = backend._dynamic_floor_obstacles(float(start[2]))
            write_json(args.output / 'transport_diagnostic.json', {
                'steps': taken, 'furniture_motion': changes,
                'boxes_after_transport': dict(zip(current_names, current_boxes))})
            return taken
        backend._demo_transport_pose = observed_transport
        trace = []
        original_step = backend._step
        if args.trace_furniture:
            def traced_step(action):
                original_step(action)
                current_pos, current_quat = furniture.get_position_orientation()
                trace.append({'env_step':backend.steps,
                    'displacement_m':float(backend.torch.linalg.norm(current_pos-before_pos)),
                    'rotation_deg':2*math.degrees(math.acos(min(1.,abs(float(
                        (current_quat*before_quat).sum()))))),
                    'position':current_pos.tolist()})
            backend._step = traced_step
        try:
            motion = backend._navigate(anchor,700,for_manipulation=not args.public_navigation)
        finally:
            backend._step = original_step
            if args.trace_furniture:
                write_json(args.output / 'furniture_trace.json', trace)
        followup_motion = None
        if args.followup_point:
            next_point = backend.torch.tensor(args.followup_point, device=start.device)
            next_anchor = SimpleNamespace(aabb=(next_point,next_point),
                get_position_orientation=lambda:(next_point,None), selected_object=None)
            followup_motion = backend._navigate(next_anchor,700)
        after_pos,after_quat = furniture.get_position_orientation()
        displacement = float(backend.torch.linalg.norm(after_pos-before_pos))
        angular = 2*math.degrees(math.acos(min(1.,abs(float((after_quat*before_quat).sum())))))
        furniture_motion = {}
        for name, obj in tracked.items():
            old_pos, old_quat = before_poses[name]
            new_pos, new_quat = obj.get_position_orientation()
            furniture_motion[name] = {
                'displacement_m': float(backend.torch.linalg.norm(new_pos-old_pos)),
                'rotation_deg': 2*math.degrees(math.acos(min(1.,abs(float((new_quat*old_quat).sum())))))}
        stable = all(row['displacement_m'] < .03 and row['rotation_deg'] < 2
                     for row in furniture_motion.values())
        stable = stable and displacement < .03 and angular < 2
        result = {'status':'passed' if stable else 'failed',
                  'model_used':False,'task_success_assessed':False,
                  'public_navigation':args.public_navigation,
                  'staged_carry': staged,
                  'archived_start_xy_yaw':args.start,
                  'archived_selected_world_point':args.point,
                  'furniture_aabb':[[float(v) for v in side] for side in furniture.aabb],
                  'dynamic_obstacle_count':len(boxes),
                  'selected_furniture_in_dynamic_mask':furniture.name in names,
                  'furniture_motion': furniture_motion,
                  'furniture_displacement_m':displacement,
                  'furniture_rotation_deg':angular,
                  'navigation':motion,'sim_steps':backend.steps}
        if followup_motion is not None:
            result['followup_navigation'] = followup_motion
            result['followup_selected_world_point'] = args.followup_point
        write_json(args.output / 'validation.json', result)
        recorder.finish({'status':result['status'],'task_success':None,'validation':result})
        print(json.dumps(result))
        return 0 if result['status']=='passed' else 1
    except Exception as exc:
        result = {'status':'failed','model_used':False,'task_success_assessed':False,
                  'error':str(exc),'failure_type':type(exc).__name__}
        write_json(args.output / 'validation.json', result)
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.finish({'status':'failed','task_success':None,'validation':result})
        print(json.dumps(result))
        return 1
    finally:
        if backend is not None:backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
