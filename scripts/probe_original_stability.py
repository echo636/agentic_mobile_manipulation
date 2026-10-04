"""Private Original component probes; no model and no benchmark success claim.

recorded-navigation reuses archived plans with explicitly reset WORLD starts.
interrupt-action sends real HTTP act(wait) and finish calls on separate threads;
only the main bridge owner touches simulator APIs. Initialization is outside the
execution clock. An external supervisor must still bound stuck native calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import threading
import time
import traceback
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, now, write_json


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def angle(value):
    return math.atan2(math.sin(value), math.cos(value))


def quat_yaw(quat):
    x, y, z, w = quat
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def load_navigation_fixture(archive):
    """Read only three named archive files; do not import the simulator."""
    run = json.loads((archive / 'run.json').read_text())
    plans = read_jsonl(archive / 'navigation_plans.jsonl')
    motion = read_jsonl(archive / 'base_motion.jsonl')
    if len(plans) != 2:
        raise ValueError('This regression requires the archived pair of navigation plans')
    timing = run['evaluation']['official_metrics']['time']
    dt = timing['simulator_time'] / timing['simulator_steps']
    cases = []
    for index, record in enumerate(plans):
        rows = [row for row in motion if record['at'] < row['at'] and
                (index + 1 == len(plans) or row['at'] < plans[index + 1]['at'])]
        if not rows:
            raise ValueError('Archived plan has no recorded motion')
        first = rows[0]
        xy = record['plan']['points'][0]
        if math.dist(xy, first['commanded_position'][:2]) > 1e-7:
            raise ValueError('Expected a rotation-only first archived command')
        prior = next((row for row in motion if row['env_step'] == first['env_step'] - 1), None)
        if prior is not None and math.dist(xy, prior['actual_position'][:2]) < 1e-7:
            yaw = quat_yaw(prior['actual_orientation'])
            yaw_source = 'previous_recorded_actual_pose_at_same_xy_and_adjacent_env_step'
        else:
            following = record['plan']['points'][1]
            heading = math.atan2(following[1] - xy[1], following[0] - xy[0])
            residual = angle(heading - first['commanded_yaw'])
            turn_step = math.pi / 3 * dt
            if abs(residual) <= turn_step:
                raise ValueError('Cannot reconstruct unrecorded initial yaw from an unsaturated first turn')
            yaw = first['commanded_yaw'] - math.copysign(turn_step, residual)
            yaw_source = 'reconstructed_from_first_saturated_60deg_per_second_turn_and_archived_dt'
        cases.append({'index': index, 'archived_plan': record,
                      'start_position': [*xy, first['commanded_position'][2]],
                      'start_yaw': yaw, 'start_yaw_source': yaw_source,
                      'archived_motion_steps': len(rows), 'archived_sim_dt': dt})
    return {'archive_run': str(archive.resolve()), 'archive_source': run['source'],
            'task': run['config']['task'], 'instance': run['config']['instance'],
            'seed': run['config']['seed'], 'cases': cases,
            'input_sha256': {name: hashlib.sha256((archive / name).read_bytes()).hexdigest()
                             for name in ('run.json', 'navigation_plans.jsonl', 'base_motion.jsonl')}}


def recorded_navigation(backend, recorder, fixture):
    from manipulation_agent.executors.gt_navigation import GridMap, NavigationPlan
    import omnigibson.utils.transform_utils as T
    cases = []
    for case in fixture['cases']:
        before = backend.steps
        item = {'index': case['index'], 'status': 'running', 'checks': {}}
        cases.append(item)
        try:
            record = case['archived_plan']
            trav = backend.env.scene.trav_map
            occupancy = trav._erode_trav_map(trav.floor_map[record['floor']].clone()).cpu().numpy()
            height, width = occupancy.shape
            grid = GridMap(width, height, float(trav.map_resolution),
                           (-width * trav.map_resolution / 2, -height * trav.map_resolution / 2),
                           (occupancy != 0).astype('uint8').tobytes())
            item['actual_map_sha256'] = hashlib.sha256(grid.free).hexdigest()
            item['checks']['same_archived_map'] = item['actual_map_sha256'] == record['map_sha256']
            item['checks']['same_sim_dt'] = abs(backend.og.sim.get_sim_step_dt() - case['archived_sim_dt']) < 1e-10
            if not item['checks']['same_archived_map']:
                raise ValueError('Current traversability map differs from the archived regression')
            values = dict(record['plan'])
            for key in ('points', 'goal', 'target'):
                values[key] = tuple(tuple(p) for p in values[key]) if key == 'points' else tuple(values[key])
            plan = NavigationPlan(**values)
            if not all(grid.segment_free(a, b) for a, b in zip(plan.points, plan.points[1:])):
                raise ValueError('Archived path is no longer collision-free on this grid')
            previous = [v.detach().cpu().tolist() for v in backend.robot.get_position_orientation()]
            position = backend.torch.tensor(case['start_position'], dtype=backend.torch.float32)
            orientation = T.euler2quat(backend.torch.tensor([0., 0., case['start_yaw']]))
            backend.robot.set_position_orientation(position, orientation)
            backend.robot.keep_still()
            item['actual_start_world_pose'] = [v.detach().cpu().tolist() for v in backend.robot.get_position_orientation()]
            recorder.event('diagnostic_pose_reset', {'audience': 'executor_private', 'case': case,
                           'previous_world_pose': previous, 'requested_world_pose': [position.tolist(), orientation.tolist()],
                           'actual_world_pose': item['actual_start_world_pose'], 'roll_pitch_policy': 'zero, as original planar follower commands',
                           'scope': 'private scripted component fixture, not a model-selected action'})
            observation = backend.observe()
            item['checks']['four_rgb_before'] = len(observation['images']) == 4
            marker = recorder.event('diagnostic_navigation_started', {'case': case['index'], 'observation': observation})
            backend.mark_video_tool('diagnostic_navigation', {'primitive': 'navigate_to', 'case': case['index']}, marker)
            with (backend.output / 'navigation_plans.jsonl').open('a') as stream:
                stream.write(json.dumps({**record, 'at': now(), 'source': 'archived_private_regression'}) + '\n')
            item['result'] = backend._execute_gt_plan(grid, plan, 700)
            actual_pos, actual_quat = backend.robot.get_position_orientation()
            item['actual_final_world_pose'] = [actual_pos.cpu().tolist(), actual_quat.cpu().tolist()]
            item['checks'].update(reached=item['result']['nav_status'] == 'reached',
                                 goal_within_2mm=math.dist(actual_pos[:2].cpu().tolist(), plan.goal) <= .002,
                                 fewer_than_archived_steps=item['result']['motion_steps'] < case['archived_motion_steps'],
                                 fewer_than_690_steps=item['result']['motion_steps'] < 690,
                                 finite_joints=bool(backend.torch.isfinite(backend.robot.get_joint_positions()).all()))
            observation = backend.observe()
            item['checks']['four_rgb_after'] = len(observation['images']) == 4
            recorder.event('diagnostic_navigation_result', {'case': case['index'], 'result': item['result'], 'observation': observation})
            item['status'] = 'passed' if all(item['checks'].values()) else 'failed'
        except Exception as exc:
            item.update(status='failed', error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
            recorder.event('diagnostic_navigation_failure', item)
        finally:
            item['actual_env_steps'] = backend.steps - before
            write_json(backend.output / 'component_cases.json', {'cases': cases})
        if backend.deadline.expired:
            break
    return {'cases': cases, 'checks': {'both_recorded_paths_passed': len(cases) == 2 and
                                      all(c['status'] == 'passed' for c in cases)}}


def interrupt_action(backend, recorder, args):
    from manipulation_agent import bridge
    from manipulation_agent.contracts import Budget
    from manipulation_agent.vision_harness import VisionHarness
    harness = VisionHarness(backend, recorder, Budget(wall_seconds=args.execution_seconds), profile='skills')
    results = {}
    start_steps = backend.steps
    url = f'http://127.0.0.1:{args.port}'
    revision = harness.revision
    timeout = args.execution_seconds + 120

    def action_client():
        try:
            results['act'] = bridge.rpc(url, 'act', {'primitive': 'wait', 'target': None, 'revision': revision,
                                         'placement_yaw_degrees': None, 'wait_seconds': 20}, 'probe-wait', timeout)
        except Exception as exc:
            results['act_error'] = {'type': type(exc).__name__, 'message': str(exc)}

    def finish_client():
        action = None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            ready_until = time.monotonic() + 30
            while True:
                try:
                    with opener.open(url + '/healthz', timeout=1) as response:
                        if json.load(response).get('ready'): break
                except OSError:
                    if time.monotonic() >= ready_until: raise
                time.sleep(.05)
            action = threading.Thread(target=action_client, daemon=True)
            action.start()
            observe_until = time.monotonic() + min(120, args.execution_seconds)
            # Python counter only: no thread reads simulator objects or calls OG.
            while backend.steps - start_steps < 3 and action.is_alive() and time.monotonic() < observe_until:
                time.sleep(.01)
            results['steps_at_finish_request'] = backend.steps - start_steps
            results['finish_requested_at'] = now()
            requested = time.monotonic()
            results['finish'] = bridge.rpc(url, 'finish', {'outcome': 'aborted',
                'reason': 'Private cooperative interruption regression, not model task completion'}, 'probe-finish', timeout)
            results['finish_reply_seconds'] = time.monotonic() - requested
        except Exception as exc:
            results['finish_error'] = {'type': type(exc).__name__, 'message': str(exc)}
        finally:
            if action is not None: action.join(timeout=timeout)

    client = threading.Thread(target=finish_client, daemon=True)
    client.start()
    try:
        bridge.serve(harness, args.port)
    finally:
        client.join(timeout=2)
        harness.finalize_recording()
    checks = {'actual_physics_started': results.get('steps_at_finish_request', 0) >= 3,
              'act_cooperatively_cancelled': results.get('act', {}).get('error', {}).get('code') == 'episode_cancelled',
              'finish_http_returned': results.get('finish', {}).get('closed') is True,
              'owner_saved_independent_score': recorder.run.get('scoring', {}).get('status') == 'passed',
              'stopped_before_wait_completed': backend.steps - start_steps < round(20 / backend.og.sim.get_sim_step_dt()),
              'base_anchor_restored': backend._base_target is None,
              'client_returned': not client.is_alive()}
    write_json(backend.output / 'interruption_rpc.json', results)
    return {'checks': checks, 'rpc': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('recorded-navigation', 'interrupt-action'), required=True)
    parser.add_argument('--archive-run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task', default='scrubbing_bathroom_floor')
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--execution-seconds', type=float, default=1800)
    parser.add_argument('--port', type=int, default=29983)
    args = parser.parse_args()
    if not math.isfinite(args.execution_seconds) or args.execution_seconds <= 0:
        parser.error('--execution-seconds must be positive and finite')
    if args.mode == 'recorded-navigation' and args.archive_run is None:
        parser.error('--archive-run is required for recorded-navigation')
    fixture = load_navigation_fixture(args.archive_run) if args.mode == 'recorded-navigation' else None
    if fixture is not None:
        args.task, args.instance, args.seed = (fixture[k] for k in ('task', 'instance', 'seed'))
    config = {'backend': 'omnigibson', 'task': args.task, 'instance': args.instance, 'seed': args.seed,
              'policy': 'private_scripted_component', 'mode': args.mode, 'model_used': False,
              'not_a_benchmark_attempt': True, 'observation_mode': 'rgb_only', 'record_video': True,
              'validation_level': 'real_simulator_original_component_only',
              'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    recorder = Recorder(args.output, config)
    backend = None
    validation = {'status': 'running', 'mode': args.mode, 'checks': {}, 'model_used': False,
                  'benchmark_task_success_assessed': False, 'validation_level': config['validation_level']}
    write_json(args.output / 'validation.json', validation)
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.deadline import write_execution_clock
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed, max_steps=20000,
                             inside_placement='official_volume', record_video=True)
        recorder.run['backend'] = backend.provenance()
        observation = backend.observe()
        recorder.event('diagnostic_initial_observation', {'observation': observation})
        if backend.deadline.clock_path is not None:
            write_execution_clock(backend.deadline.clock_path, args.execution_seconds)
        else:
            backend.deadline.arm_local(args.execution_seconds)
        recorder.run['execution_clock'] = backend.deadline.clock
        write_json(args.output / 'run.json', recorder.run)
        if fixture is not None:
            write_json(args.output / 'archived_navigation_fixture.json', fixture)
            validation.update(recorded_navigation(backend, recorder, fixture))
            evaluation = backend.evaluate()
            write_json(args.output / 'independent_evaluation.json', evaluation)
            validation['checks']['independent_score_saved'] = True
            recorder.event('diagnostic_independent_evaluation', {'evaluation': evaluation,
                           'scope': 'private component final state; not a benchmark task outcome'})
            recorder.run['evaluation'] = evaluation
        else:
            validation.update(interrupt_action(backend, recorder, args))
        validation['checks']['four_initial_rgb'] = len(observation['images']) == 4
        validation['status'] = 'passed' if all(validation['checks'].values()) else 'failed'
    except Exception as exc:
        validation.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.event('component_failure', {'type': type(exc).__name__, 'message': str(exc)})
    finally:
        if backend is not None:
            try:
                if 'video' not in recorder.run: recorder.run['video'] = backend.finalize_video()
                validation['checks']['video_finalized'] = (recorder.run.get('video') or {}).get('status') == 'passed'
                if not validation['checks']['video_finalized']: validation['status'] = 'failed'
            except Exception as exc:
                validation.update(status='failed', video_failure=str(exc))
            recorder.run['sim_steps'] = backend.steps
        write_json(args.output / 'validation.json', validation)
        recorder.run['component_validation'] = validation
        if recorder.run['status'] == 'running':
            recorder.finish({'status': validation['status'], 'task_success': None}, render=False)
        else:
            write_json(args.output / 'run.json', recorder.run)
        if backend is not None:
            backend.close()
        else:
            from manipulation_agent.startup_cleanup import shutdown_partial_simulator
            recorder.event('startup_cleanup', shutdown_partial_simulator())
    print(json.dumps(validation), flush=True)
    return 0 if validation['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
