"""Private paired surface-placement diagnostic on public task scenes.

Replay an archived RGB prefix to recreate a held-object scene, then compare old
and current placement on the SAME simulator/Python snapshot and random seed.
Archived object names and world points are private diagnostic fixtures, never
model inputs. This is not an autonomous policy or a benchmark success result.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, now, write_json


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_from_archive(archive):
    run = json.loads((archive / 'run.json').read_text())
    calls = [e for e in jsonl(archive / 'events.jsonl')
             if e.get('kind') == 'tool_call' and e['name'] in ('act', 'look')]
    grounding = jsonl(archive / 'grounding_diagnostics.jsonl')
    def resolve(call):
        target = call['arguments'].get('target')
        if not target:
            return None
        matches = [g for g in grounding if g.get('image_ref') == target['image_ref']
                   and g.get('selected_pixel') == target['point']]
        if len(matches) != 1:
            raise ValueError(f"Expected unique archived grounding: {call['id']}")
        return matches[0]
    first = next(i for i, c in enumerate(calls)
                 if c['arguments'].get('primitive') == 'place_on_top')
    cases = []
    for c in calls[first:]:
        if c['arguments'].get('primitive') != 'place_on_top':
            continue
        g = resolve(c)
        cases.append({'event_id': c['id'], 'arguments': c['arguments'],
                      'selected_object': g['selected_object_prim'].rsplit('/', 1)[-1],
                      'point': g['hit_position'], 'grounding': g,
                      'yaw_degrees': c['arguments'].get('placement_yaw_degrees')})
    return {'archive': str(archive.resolve()), 'task': run['config']['task'],
            'instance': run['config']['instance'], 'seed': run['config']['seed'],
            'archive_source': run['source'], 'prefix': [dict(c, archived_grounding=resolve(c))
                                                       for c in calls[:first + 1]],
            'cases': cases, 'input_sha256': {n: sha(archive / n) for n in
                ('run.json', 'events.jsonl', 'grounding_diagnostics.jsonl')},
            'scope': 'All points/orientations tested from recreated first-placement state; '
                     'later archived robot poses are not claimed to be reproduced.'}


def clone_python(value, torch):
    """Clone tensors/containers while preserving simulator-object identity."""
    if torch.is_tensor(value):
        return value.clone()
    if isinstance(value, dict):
        return {k: clone_python(v, torch) for k, v in value.items()}
    if isinstance(value, list):
        return [clone_python(v, torch) for v in value]
    if isinstance(value, tuple):
        return tuple(clone_python(v, torch) for v in value)
    return value


PYTHON_STATE = ('_ideal_held', '_carry_relative', '_carry_contents', '_carry_dependencies',
                '_stabilized_containers', '_stabilized_container_payloads', '_base_target',
                '_object_anchor', '_demo_focus', '_demo_arm')


def snapshot(backend):
    return {'sim': backend.og.sim.dump_state(serialized=False),
            'python': {name: clone_python(getattr(backend, name), backend.torch)
                       for name in PYTHON_STATE if hasattr(backend, name)}}


def restore(backend, state, seed):
    backend.og.sim.load_state(copy.deepcopy(state['sim']), serialized=False)
    for name in PYTHON_STATE:
        if name in state['python']:
            setattr(backend, name, clone_python(state['python'][name], backend.torch))
        elif name in backend.__dict__:
            delattr(backend, name)
    backend.frames_revision = -1
    backend._carry_follow()
    random.seed(seed)
    import numpy as np
    np.random.seed(seed)
    backend.torch.manual_seed(seed)
    if backend.torch.cuda.is_available():
        backend.torch.cuda.manual_seed_all(seed)


@contextmanager
def methods(backend, cls):
    saved = {}
    try:
        for name, function in vars(cls).items():
            if not isinstance(function, types.FunctionType):
                continue
            saved[name] = (name in backend.__dict__, backend.__dict__.get(name))
            setattr(backend, name, types.MethodType(function, backend))
        yield
    finally:
        for name, (present, value) in saved.items():
            if present:
                setattr(backend, name, value)
            else:
                delattr(backend, name)


def pose(obj):
    return [v.detach().cpu().tolist() for v in obj.get_position_orientation()]


class FixtureCaptured(Exception):
    pass


def recreate_prefix(backend, recorder, fixture):
    captured = {}
    def intercept(self, target, max_steps, point=None, yaw_degrees=None):
        captured.update(target=target.name, point=point.cpu().tolist(),
                        held=self._get_held().name if self._get_held() else None,
                        robot_pose=pose(self.robot), held_pose=pose(self._get_held()))
        raise FixtureCaptured()
    backend._checked_place_on_top = types.MethodType(intercept, backend)
    try:
        for call in fixture['prefix']:
            observation = backend.observe()
            args = copy.deepcopy(call['arguments'])
            target = args.get('target')
            if target:
                view = target['image_ref'].rsplit('-', 1)[-1]
                target['image_ref'] = next(v['image_ref'] for v in observation['images'] if v['view'] == view)
                primitive = args.get('primitive')
                obj, point, grounding = backend._ground(target, require_object=primitive != 'navigate_to')
                expected = call['archived_grounding'].get('selected_object_prim')
                if expected and (obj is None or obj.name != expected.rsplit('/', 1)[-1]):
                    raise ValueError(f"RGB prefix target differs: {obj.name if obj else None} != {expected}")
                recorder.event('diagnostic_prefix_grounding', {'original_event': call['id'],
                    'object': obj.name if obj else None, 'point': point.cpu().tolist(),
                    'grounding': grounding, 'arguments': args})
            primitive = args.get('primitive', 'look')
            marker = recorder.event('diagnostic_prefix_call', {'original_event': call['id'], 'arguments': args})
            backend.mark_video_tool('diagnostic_prefix', args, marker)
            try:
                result = backend.execute_visual(primitive, target, 700,
                    **({'yaw_degrees': args['yaw_degrees']} if primitive == 'look' else
                       {'placement_yaw_degrees': args.get('placement_yaw_degrees')}))
            except FixtureCaptured:
                break
            recorder.event('diagnostic_prefix_result', {'result': result})
    finally:
        del backend._checked_place_on_top
    if not captured:
        raise ValueError('Prefix did not reach surface placement')
    if captured['target'] != fixture['cases'][0]['selected_object']:
        raise ValueError('First placement support differs from archive')
    recorder.event('diagnostic_fixture_ready', captured)
    return captured


def relation_evidence(backend, held, target, selected_point):
    from omnigibson.object_states import OnTop, Touching
    position = held.get_position_orientation()[0]
    return {'held_pose': pose(held), 'target_pose': pose(target),
            'official_on_top': bool(held.states[OnTop].get_value(target)),
            'touching': bool(held.states[Touching].get_value(target)),
            'linear_speed_m_s': float(backend.torch.linalg.norm(held.get_linear_velocity())),
            'angular_speed_rad_s': float(backend.torch.linalg.norm(held.get_angular_velocity())),
            'finite': bool(backend.torch.isfinite(position).all()),
            'released': backend._get_held() is None,
            'root_xy_distance_to_click_m': float(backend.torch.linalg.norm(position[:2] - selected_point[:2]))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive-run', type=Path, required=True)
    p.add_argument('--baseline-placement', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--seeds', nargs='+', type=int, default=[0])
    p.add_argument('--execution-seconds', type=float, default=1800)
    p.add_argument('--settling-steps', type=int, default=60)
    p.add_argument('--wait-ready-file', type=Path,
                   help='Warm up baseline scene/prefix, then wait for candidate_placement JSON (private diagnostic only)')
    p.add_argument('--candidate-placement', type=Path,
                   help='Load candidate class after warmup; defaults to this source checkout')
    p.add_argument('--negative-control', action='store_true',
                   help='Also test a point 1m above the selected surface, which must be rejected')
    p.add_argument('--fixture-only', action='store_true')
    a = p.parse_args()
    fixture = fixture_from_archive(a.archive_run)
    if a.fixture_only:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(a.output, fixture)
        return 0
    current_file = Path(__file__).resolve().parents[1] / 'src/manipulation_agent/executors/placement.py'
    config = {'task': fixture['task'], 'instance': fixture['instance'], 'seed': fixture['seed'],
        'policy': 'private_scripted_paired_placement', 'model_used': False, 'not_a_benchmark_attempt': True,
        'validation_level': 'real_simulator_paired_component_diagnostic', 'record_video': True,
        'observation_mode': 'rgb_only', 'baseline_placement_path': str(a.baseline_placement),
        'baseline_placement_sha256': sha(a.baseline_placement), 'current_placement_sha256': sha(current_file),
        'script_sha256': sha(Path(__file__)), 'comparison_seeds': a.seeds,
        'extra_free_settling_steps': a.settling_steps}
    recorder = Recorder(a.output, config)
    write_json(a.output / 'fixture.json', fixture)
    validation = {'status': 'running', 'validation_level': config['validation_level'],
                  'model_used': False, 'benchmark_task_success_assessed': False, 'cases': []}
    write_json(a.output / 'validation.json', validation)
    backend = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.executors.placement import CheckedPlacement
        spec = importlib.util.spec_from_file_location('manipulation_agent.executors._diagnostic_baseline_placement', a.baseline_placement)
        baseline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(baseline)
        backend = RGBBackend(fixture['task'], fixture['instance'], a.output, seed=fixture['seed'],
                             max_steps=20000, inside_placement='official_volume', record_video=True)
        backend.deadline.arm_local(a.execution_seconds)
        recorder.run['backend'] = backend.provenance()
        recorder.run['execution_clock'] = backend.deadline.clock
        write_json(a.output / 'run.json', recorder.run)
        prefix_started = time.monotonic()
        validation['fixture_actual'] = recreate_prefix(backend, recorder, fixture)
        prefix_seconds = time.monotonic() - prefix_started
        held = backend._get_held()
        common = snapshot(backend)
        backend.torch.save(common['sim'], a.output / 'fixture_sim_state.pt')
        write_json(a.output / 'validation.json', validation)
        ready = {}
        if a.wait_ready_file:
            recorder.event('diagnostic_waiting_candidate', {'ready_file': str(a.wait_ready_file),
                'prefix_elapsed_seconds': prefix_seconds, 'waiting_time_excluded_from_execution_budget': True})
            write_json(a.output / 'fixture_ready.json', {'at': now(), 'status': 'running',
                'fixture_ready': True, 'prefix_elapsed_seconds': prefix_seconds,
                'waiting_for': str(a.wait_ready_file)})
            while not a.wait_ready_file.exists():
                time.sleep(1)
            ready = json.loads(a.wait_ready_file.read_text())
            from manipulation_agent.deadline import EpisodeDeadline
            backend.deadline = EpisodeDeadline()
            backend.deadline.arm_local(max(1, a.execution_seconds - prefix_seconds))
            recorder.run['execution_clock'] = backend.deadline.clock
        candidate_path = Path(ready.get('candidate_placement', a.candidate_placement or current_file))
        if ready.get('sha256') and sha(candidate_path) != ready['sha256']:
            raise ValueError('Ready candidate SHA256 does not match file')
        if candidate_path != current_file:
            import manipulation_agent.executors as executor_package
            # New private geometry helpers resolve from the frozen candidate source.
            executor_package.__path__.insert(0, str(candidate_path.parent))
            name = 'manipulation_agent.executors._diagnostic_candidate_placement'
            spec = importlib.util.spec_from_file_location(name, candidate_path)
            candidate = importlib.util.module_from_spec(spec)
            sys.modules[name] = candidate
            spec.loader.exec_module(candidate)
            CheckedPlacement = candidate.CheckedPlacement
        snapshot_dir = a.output / 'candidate_executor_snapshot'
        snapshot_dir.mkdir()
        candidate_hashes = {}
        for source in sorted(candidate_path.parent.glob('*.py')):
            shutil.copy2(source, snapshot_dir / source.name)
            candidate_hashes[source.name] = sha(source)
        recorder.run['candidate_source'] = {'placement': str(candidate_path), 'source_record': ready,
            'executor_sha256': candidate_hashes, 'prefix_seconds': prefix_seconds}
        write_json(a.output / 'run.json', recorder.run)
        cases = list(fixture['cases'])
        if a.negative_control:
            control = copy.deepcopy(cases[0])
            control['event_id'] = 'synthetic_wrong_height_negative_control'
            control['point'][2] += 1.
            control['expected_rejection'] = True
            cases.append(control)
        for case in cases:
            target = backend.env.scene.object_registry('name', case['selected_object'])
            if target is None:
                raise ValueError('Archived target object absent')
            point = backend.torch.tensor(case['point'], dtype=backend.torch.float32)
            for seed in a.seeds:
                for label, cls in [('baseline', baseline.CheckedPlacement), ('geometry_fix', CheckedPlacement)]:
                    restore(backend, common, seed)
                    item = {'case_event': case['event_id'], 'variant': label, 'seed': seed,
                            'point': case['point'], 'yaw_degrees': case['yaw_degrees'],
                            'held': held.name, 'target': target.name, 'status': 'running',
                            'expected_rejection': case.get('expected_rejection', False)}
                    validation['cases'].append(item)
                    before = backend.observe()
                    item['before_observation'] = before
                    item['before_evidence'] = relation_evidence(backend, held, target, point)
                    marker = recorder.event('diagnostic_placement_call', item)
                    backend.mark_video_tool('diagnostic_placement', {k: item[k] for k in
                        ('case_event', 'variant', 'seed', 'point', 'yaw_degrees')}, marker)
                    started = time.monotonic()
                    try:
                        with methods(backend, cls):
                            item['result'] = backend._checked_place_on_top(target, 700, point, case['yaw_degrees'])
                        item['immediate_evidence'] = relation_evidence(backend, held, target, point)
                        for _ in range(a.settling_steps):
                            backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
                        item['settled_evidence'] = relation_evidence(backend, held, target, point)
                        evidence = item['settled_evidence']
                        item['checks'] = {'released': evidence['released'], 'finite': evidence['finite'],
                            'official_on_top_after_extra_settle': evidence['official_on_top'],
                            'linear_speed_below_0_1': evidence['linear_speed_m_s'] <= .1,
                            'angular_speed_below_0_5': evidence['angular_speed_rad_s'] <= .5}
                        item['status'] = 'passed' if all(item['checks'].values()) else 'failed'
                    except Exception as exc:
                        item.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                                    error_code=getattr(exc, 'code', None), traceback=traceback.format_exc())
                        item['failure_evidence'] = relation_evidence(backend, held, target, point)
                    if item['expected_rejection']:
                        item['negative_control_passed'] = item.get('error_code') in ('sampling_error', 'postcondition_error')
                    item['elapsed_seconds'] = time.monotonic() - started
                    item['after_observation'] = backend.observe()
                    recorder.event('diagnostic_placement_result', item)
                    write_json(a.output / 'validation.json', validation)
                    print(json.dumps({k: item.get(k) for k in ('case_event','variant','seed','status','error','elapsed_seconds')}), flush=True)
                    backend.deadline.check(changed=True)
        validation['summary'] = {label: {'attempts': sum(c['variant'] == label and not c['expected_rejection'] for c in validation['cases']),
            'passed': sum(c['variant'] == label and not c['expected_rejection'] and c['status'] == 'passed' for c in validation['cases']),
            'negative_control_passed': all(c.get('negative_control_passed') for c in validation['cases']
                                           if c['variant'] == label and c['expected_rejection'])}
            for label in ('baseline', 'geometry_fix')}
        # Diagnostic success means comparison completed, not that both implementations pass.
        validation['status'] = 'passed'
        validation['comparison_completed'] = True
    except Exception as exc:
        validation.update(status='failed', comparison_completed=False, error_type=type(exc).__name__, error=str(exc))
        (a.output / 'traceback.txt').write_text(traceback.format_exc())
        recorder.event('diagnostic_failure', {'type': type(exc).__name__, 'error': str(exc)})
    finally:
        if backend is not None:
            try:
                recorder.run['video'] = backend.finalize_video()
            except Exception as exc:
                validation['video_error'] = str(exc)
            recorder.run['sim_steps'] = backend.steps
        write_json(a.output / 'validation.json', validation)
        recorder.finish({'status': validation['status'], 'component_validation': validation, 'task_success': None}, render=False)
        if backend is not None:
            backend.close()
        else:
            from manipulation_agent.startup_cleanup import shutdown_partial_simulator
            recorder.event('startup_cleanup', shutdown_partial_simulator())
    return 0 if validation['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
