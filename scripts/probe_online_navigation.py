"""Real sensor-map/navigation component probe; no LLM or benchmark success.

The selected target is a floor pixel in the current rendered depth/RGB pair.
Only sensor-built occupancy is used to choose a reachable diagnostic target.
After initialization, reading scene.trav_map is trapped until navigation ends.
All simulator access stays on this process's owner thread. No scene object
list, archived robot pose, precomputed map or task goal is used for selection.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, now, write_json


@contextmanager
def forbid_traversability(scene, audit):
    """Block the property lookup itself, including inherited descriptors."""
    cls = type(scene)
    if cls.__name__ == 'ObservedNavigationScene' and 'trav_map' in cls.__dict__:
        # The production scene already prevents loading or consulting a
        # precomputed map. Keep its property intact instead of replacing it
        # just for a redundant diagnostic; any access raises during the probe.
        audit.update(installed=True, native_scene_guard=True, scene_class=cls.__name__, started_at=now())
        try:
            yield
        finally:
            audit.update(restored=True, finished_at=now(), property_unchanged=True)
        return
    missing = object()
    previous = cls.__dict__.get('trav_map', missing)
    def denied(_):
        audit['reads'] += 1
        raise RuntimeError('Diagnostic rejected precomputed scene.trav_map access')
    setattr(cls, 'trav_map', property(denied))
    audit.update(installed=True, scene_class=cls.__name__, started_at=now())
    try:
        yield
    finally:
        if previous is missing:
            delattr(cls, 'trav_map')
        else:
            setattr(cls, 'trav_map', previous)
        audit.update(restored=True, finished_at=now())


def save_map_diagnostics(backend, label):
    import numpy as np
    snapshot = backend._online_snapshot
    if snapshot is None:
        raise RuntimeError('Online navigation did not produce an occupancy snapshot')
    values = np.frombuffer(snapshot.occupancy, dtype=np.uint8).reshape(snapshot.height, snapshot.width)
    grid = backend._online_grid(snapshot)
    position, orientation = backend.robot.get_position_orientation()
    xy = position[:2].detach().cpu().tolist()
    cell = grid.cell(xy)
    lower, upper = backend.robot.base_footprint_link.aabb
    record = {'at':now(), 'audience':'executor_private', 'sequence':snapshot.sequence,
              'metadata':snapshot.metadata, 'width':snapshot.width, 'height':snapshot.height,
              'resolution':snapshot.resolution, 'origin':list(snapshot.origin),
              'occupancy_counts':{str(v):int(np.count_nonzero(values == v)) for v in (0, 100, 255)},
              'occupancy_sha256':hashlib.sha256(snapshot.occupancy).hexdigest(),
              'inflated_free_cells':sum(grid.free), 'current_cell':list(cell),
              'current_cell_raw_value':int(values[cell]) if grid.contains(cell) else None,
              'current_footprint_navigable':grid.navigable(cell),
              'robot_world_position':position.detach().cpu().tolist(),
              'robot_world_orientation':orientation.detach().cpu().tolist(),
              'base_footprint_aabb':[lower.detach().cpu().tolist(), upper.detach().cpu().tolist()],
              'robot_radius_m':backend._online_robot_radius,
              'floor_height_m':backend._online_floor_height,
              'rig_height_m':backend.rig_height,
              'sim_step':backend.steps}
    arrays = {'occupancy':values, 'inflated_free':np.frombuffer(grid.free, dtype=np.uint8).reshape(values.shape)}
    for ref, frame in backend.current_frames.items():
        for key in ('depth_linear', 'intrinsic', 'position', 'orientation'):
            arrays[ref+'_'+key] = frame[key].detach().cpu().numpy()
    np.savez_compressed(backend.output / (label+'_map_and_depth.npz'), **arrays)
    write_json(backend.output / (label+'_map_diagnostics.json'), record)
    return record


def select_visible_floor_target(backend, minimum_travel):
    """Private component fixture from actual rendered pixels, not a policy."""
    import numpy as np
    import omnigibson.utils.transform_utils as T
    from manipulation_agent.executors.gt_navigation import NavigationError
    position, _ = backend.robot.get_position_orientation()
    base = position.detach().cpu().numpy()
    floor_height = backend._online_floor_height
    candidates = []
    for ref, frame in backend.current_frames.items():
        depth = frame['depth_linear'].detach().cpu().numpy()
        intrinsic = frame['intrinsic'].detach().cpu().numpy()
        origin = frame['position'].detach().cpu().numpy()
        rotation = T.quat2mat(frame['orientation']).detach().cpu().numpy()
        height, width = depth.shape
        rows, cols = np.meshgrid(np.arange(12, height-12, 12), np.arange(12, width-12, 12), indexing='ij')
        pixels = np.column_stack((cols.ravel(), rows.ravel()))
        ranges = depth[rows, cols].ravel()
        valid = np.isfinite(ranges) & (ranges > .05) & (ranges < 30.)
        pixels, ranges = pixels[valid], ranges[valid]
        local = np.column_stack(((pixels[:, 0]-intrinsic[0, 2])/intrinsic[0, 0],
                                 -(pixels[:, 1]-intrinsic[1, 2])/intrinsic[1, 1], -np.ones(len(pixels))))
        points = (local * ranges[:, None]) @ rotation.T + origin
        distances = np.linalg.norm(points[:, :2]-base[:2], axis=1)
        valid = ((np.abs(points[:, 2]-floor_height) <= .05)
                 & (distances >= minimum_travel+.85) & (distances <= 3.5))
        for pixel, point, distance in zip(pixels[valid], points[valid], distances[valid]):
            candidates.append({'target':{'image_ref':ref, 'point':[float(pixel[0]/(width-1)), float(pixel[1]/(height-1))]},
                               'world_point':point.tolist(), 'pixel':pixel.tolist(),
                               'target_distance_m':float(distance), 'rank':abs(float(distance)-2.)})
    # Spatially de-duplicate measured points before trying plans. This is a
    # diagnostic fixture selection, not unrecorded model retries or exploration.
    selected, seen = [], set()
    for item in sorted(candidates, key=lambda c:(c['rank'],c['target']['image_ref'],c['pixel'])):
        key = tuple(round(v/.2) for v in item['world_point'][:2])
        if key not in seen:
            seen.add(key)
            selected.append(item)
    attempts = []
    for item in selected:
        backend.deadline.check()
        point = backend.torch.tensor(item['world_point'], dtype=position.dtype, device=position.device)
        try:
            _, plan = backend._online_plan(backend._online_snapshot, position, point)
            distance = math.dist(base[:2].tolist(), plan.goal)
            if distance < minimum_travel:
                attempts.append({'target':item['target'], 'reason':'planned_displacement_too_short', 'distance':distance})
                continue
            item.update(planned_goal=list(plan.goal), planned_displacement_m=distance,
                        planned_geodesic_m=plan.geodesic_m, source='current_rendered_floor_pixel_private_component_fixture')
            write_json(backend.output/'target_selection.json', {'status':'passed','selected':item,'attempts':attempts,
                                                              'measured_floor_candidates':len(selected)})
            return item
        except NavigationError as exc:
            # A genuine episode deadline must propagate, not become another
            # candidate rejection. Ordinary no-route errors remain evidence.
            backend.deadline.check()
            attempts.append({'target':item['target'], 'error_type':type(exc).__name__, 'error':str(exc)})
            if getattr(exc, 'code', None) == 'navigation_invalid_start':
                break  # Trying other endpoints cannot make an invalid start free.
    write_json(backend.output/'target_selection.json', {'status':'failed','attempts':attempts,
                                                      'measured_floor_candidates':len(selected)})
    raise RuntimeError('No current visible floor pixel yielded a sensor-map route of sufficient displacement')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', default='turning_on_radio')
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--execution-seconds', type=float, default=1800)
    parser.add_argument('--minimum-travel-m', type=float, default=.5)
    args = parser.parse_args()
    if any(not math.isfinite(v) or v <= 0 for v in (args.execution_seconds,args.minimum_travel_m)):
        parser.error('Positive finite execution duration and travel distance required')
    recorder = Recorder(args.output, {'backend':'omnigibson','task':args.task,'instance':args.instance,'seed':args.seed,
        'policy':'private_sensor_navigation_component','model_used':False,'not_a_benchmark_attempt':True,
        'observation_mode':'rgb_only','record_video':True,'minimum_travel_m':args.minimum_travel_m,
        'validation_level':'real_depth_cartographer_navigation_component',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    validation = {'status':'running','at':now(),'checks':{},'model_used':False,
                  'benchmark_task_success_assessed':False,'trav_map_guard':{'reads':0}}
    backend = None
    write_json(args.output/'validation.json', validation)
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.deadline import write_execution_clock
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed, max_steps=20000,
                             inside_placement='official_volume', record_video=True)
        if not getattr(backend, '_online_enabled', False):
            raise RuntimeError('This runtime did not enable the online sensor mapping executor')
        recorder.run['backend'] = backend.provenance()
        with forbid_traversability(backend.env.scene, validation['trav_map_guard']):
            observation = backend.observe()
            if backend._online_snapshot is None:
                backend._update_online_map(rendered=True)
            initial = save_map_diagnostics(backend, 'initial')
            validation['initial_map'] = initial
            recorder.event('diagnostic_initial_observation', {'observation':observation})
            if backend.deadline.clock_path is not None:
                write_execution_clock(backend.deadline.clock_path, args.execution_seconds)
            else:
                backend.deadline.arm_local(args.execution_seconds)
            recorder.run['execution_clock'] = backend.deadline.clock
            selection = select_visible_floor_target(backend, args.minimum_travel_m)
            event = recorder.event('diagnostic_navigation_selected', {'selection':selection})
            backend.mark_video_tool('diagnostic_navigation', {'primitive':'navigate_to','target':selection['target']}, event)
            start = backend.robot.get_position_orientation()[0][:2].detach().cpu().tolist()
            result = backend.execute_visual('navigate_to', selection['target'], 20000)
            validation['navigation'] = result
            after = backend.observe()
            final = save_map_diagnostics(backend, 'final')
            end = backend.robot.get_position_orientation()[0][:2].detach().cpu().tolist()
            validation['final_map'] = final
            validation['checks'].update(four_rgb_before=len(observation['images'])==4,
                four_rgb_after=len(after['images'])==4, reached=result.get('nav_status')=='reached',
                displacement_at_least_minimum=math.dist(start,end)>=args.minimum_travel_m,
                map_sequence_advanced=final['sequence']>initial['sequence'],
                replanned_during_motion=result.get('replans',0)>0,
                no_precomputed_traversability_reads=validation['trav_map_guard']['reads']==0,
                reported_no_precomputed_walkability=result.get('precomputed_walkability') is False,
                finite_robot_joints=bool(backend.torch.isfinite(backend.robot.get_joint_positions()).all()))
            validation['displacement_m'] = math.dist(start,end)
            recorder.event('diagnostic_navigation_completed', {'result':result,'observation':after})
    except Exception as exc:
        validation.update(status='failed',error_type=type(exc).__name__,error=str(exc))
        (args.output/'traceback.txt').write_text(traceback.format_exc())
        recorder.event('diagnostic_failure', {'error_type':type(exc).__name__,'error':str(exc)})
    finally:
        if backend is not None:
            try:
                evaluation = backend.evaluate()
                recorder.run['evaluation'] = evaluation
                write_json(args.output/'independent_evaluation.json', evaluation)
                validation['checks']['independent_score_saved'] = True
            except Exception as exc:
                validation['checks']['independent_score_saved'] = False
                validation['evaluation_error'] = str(exc)
            try:
                recorder.run['video'] = backend.finalize_video()
                validation['checks']['video_finalized'] = recorder.run['video'].get('status')=='passed'
            except Exception as exc:
                validation['checks']['video_finalized'] = False
                validation['video_error'] = str(exc)
            recorder.run['sim_steps'] = backend.steps
            try:
                backend.close()
                validation['checks']['backend_closed'] = True
            except Exception as exc:
                validation['checks']['backend_closed'] = False
                validation['close_error'] = str(exc)
        else:
            from manipulation_agent.startup_cleanup import shutdown_partial_simulator
            recorder.event('startup_cleanup', shutdown_partial_simulator())
        passed = (validation.get('error') is None and bool(validation['checks'])
                  and all(validation['checks'].values()))
        validation.update(status='passed' if passed else 'failed', finished_at=now())
        write_json(args.output/'validation.json', validation)
        recorder.run['component_validation'] = validation
        recorder.finish({'status':validation['status'],'task_success':None}, render=False)
    print(json.dumps(validation), flush=True)
    return 0 if validation['status']=='passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
