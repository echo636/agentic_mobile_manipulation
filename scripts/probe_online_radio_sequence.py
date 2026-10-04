"""Replay the real Astra radio actions to regress its post-toggle navigation.

The fixture contains only recorded RGB view names and normalized model pixels.
It never restores a GT pose, selects an object by name, or reads a GT map.
This is an executor regression, not an independent model benchmark attempt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, now, write_json
from probe_online_navigation import forbid_traversability, save_map_diagnostics


FIXTURE = (
    ('navigate_to', 'left', (.557, .628)),
    ('navigate_to', 'front', (.173, .741)),
    ('toggle_on', 'right', (.508, .677)),
    ('navigate_to', 'back', (.51, .81)),
)
FIXTURE_RUN = 'mas_online_navigation_original_000_turning_on_radio_i301_s0_r1'
FIXTURE_SOURCE = '73b1b9a45275893c273fe5253a72f0ad968ebdd5'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--task', choices=['turning_on_radio'], default='turning_on_radio')
    p.add_argument('--instance', type=int, choices=[301], default=301)
    p.add_argument('--seed', type=int, choices=[0], default=0)
    p.add_argument('--execution-seconds', type=float, default=1800)
    p.add_argument('--minimum-travel-m', type=float, default=.5)
    args = p.parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (args.execution_seconds, args.minimum_travel_m)):
        p.error('Positive finite duration and distance required')
    recorder = Recorder(args.output, {'backend':'omnigibson', 'task':args.task,
        'instance':args.instance, 'seed':args.seed, 'model_used':False,
        'policy':'recorded_rgb_action_fixture', 'not_a_benchmark_attempt':True,
        'fixture_run':FIXTURE_RUN, 'fixture_source':FIXTURE_SOURCE,
        'validation_level':'real_executor_regression_of_recorded_post_toggle_navigation',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    validation = {'status':'running', 'at':now(), 'model_used':False,
                  'checks':{}, 'actions':[], 'trav_map_guard':{'reads':0}}
    write_json(args.output/'validation.json', validation)
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        backend = RGBBackend(args.task, args.instance, args.output, seed=args.seed,
            max_steps=20000, inside_placement='official_volume', record_video=True)
        recorder.run['backend'] = backend.provenance()
        with forbid_traversability(backend.env.scene, validation['trav_map_guard']):
            # The original episode captures once at startup and once for the
            # agent's asynchronous initial observation, before the first act.
            backend.observe()
            observation = backend.observe()
            initial = save_map_diagnostics(backend, 'initial')
            validation['initial_map'] = initial
            backend.deadline.arm_local(args.execution_seconds)
            for index, (primitive, view, pixel) in enumerate(FIXTURE, 1):
                frame = next(i for i in observation['images'] if i['view'] == view)
                target = {'image_ref':frame['image_ref'], 'point':list(pixel)}
                call = {'primitive':primitive, 'target':target,
                        'fixture_action':index, 'at':now()}
                event = recorder.event('diagnostic_fixture_action', call)
                backend.mark_video_tool('act', call, event)
                action = {'index':index, 'call':call, 'status':'running'}
                validation['actions'].append(action)
                try:
                    action['result'] = backend.execute_visual(primitive, target, 20000-backend.steps)
                    action['status'] = 'passed'
                except Exception as exc:
                    action.update(status='failed', error_type=type(exc).__name__,
                                  error=str(exc), code=getattr(exc, 'code', None))
                    raise
                finally:
                    action['finished_at'] = now()
                    # Preserve the exact post-action state, including a safe
                    # stop before an action has reached its selected endpoint.
                    observation = backend.observe()
                    action['observation'] = observation
                    action['map'] = save_map_diagnostics(backend, f'action_{index:02d}')
                    recorder.event('diagnostic_fixture_result', action)
                    write_json(args.output/'validation.json', validation)
            validation['checks'].update(
                four_recorded_actions_completed=len(validation['actions'])==4 and
                    all(a['status']=='passed' for a in validation['actions']),
                four_rgb_after_each_action=all(len(a['observation']['images'])==4 for a in validation['actions']),
                maps_advanced=validation['actions'][-1]['map']['sequence'] > initial['sequence'],
                total_navigation_at_least_minimum=backend.navigation_distance >= args.minimum_travel_m,
                final_navigation_reached=validation['actions'][-1]['result'].get('nav_status')=='reached',
                no_precomputed_map_reads=validation['trav_map_guard']['reads']==0,
                finite_robot_joints=bool(backend.torch.isfinite(backend.robot.get_joint_positions()).all()))
    except Exception as exc:
        validation.update(error_type=type(exc).__name__, error=str(exc))
        (args.output/'traceback.txt').write_text(traceback.format_exc())
        recorder.event('diagnostic_failure', {'error_type':type(exc).__name__, 'error':str(exc)})
    finally:
        if backend is not None:
            try:
                evaluation = backend.evaluate()
                recorder.run['evaluation'] = evaluation
                write_json(args.output/'independent_evaluation.json', evaluation)
                validation['checks']['independent_score_saved'] = True
                validation['checks']['radio_task_success'] = evaluation.get('task_success') is True
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
            validation['navigation_distance_m'] = backend.navigation_distance
        validation.update(status='passed' if not validation.get('error') and
            bool(validation['checks']) and all(validation['checks'].values()) else 'failed',
            finished_at=now(), cleanup={'status':'requested','verification':'launcher_owned_unit_exit'})
        write_json(args.output/'validation.json', validation)
        recorder.run['component_validation'] = validation
        recorder.finish({'status':validation['status'], 'task_success':None}, render=False)
        # Persist above: OmniGibson application shutdown may not return.
        if backend is not None:
            backend.close()
        else:
            from manipulation_agent.startup_cleanup import shutdown_partial_simulator
            shutdown_partial_simulator()
    return 0 if validation['status']=='passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
