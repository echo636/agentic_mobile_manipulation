"""Private real-simulator chopped-onion carry regression, not a policy trial."""
import argparse
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.records import Recorder, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    recorder = Recorder(args.output, dict(task='chop_an_onion', instance=301, seed=0,
        policy='private_surface_particle_payload_probe', model_used=False, not_a_benchmark_attempt=True))
    validation = {'status': 'running', 'validation_level': 'real_simulator_private_surface_particle_component',
                  'model_used': False, 'whole_task_success_claimed': False, 'checks': {}, 'stages': []}
    write_json(args.output/'validation.json', validation)
    backend = harness = None

    def stage(name, **data):
        validation['stages'].append(dict(stage=name, **data))
        write_json(args.output/'validation.json', validation)
        print(json.dumps(validation['stages'][-1]), flush=True)

    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.executors.particle_payload import (
            snapshot_particles, verify_particles, particle_count, snapshot_particle_state, restore_particle_state,
            _support_at)
        from manipulation_agent.vision_harness import VisionHarness
        from manipulation_agent.contracts import Budget
        from omnigibson.object_states import OnTop
        from omnigibson.utils.sampling_utils import raytest
        import omnigibson.utils.transform_utils as T

        backend = RGBBackend('chop_an_onion', 301, args.output, seed=0,
                             max_steps=40000, inside_placement='official_volume', record_video=True)
        harness = VisionHarness(backend, recorder, Budget(wall_seconds=None), profile='skills')
        torch = backend.torch
        scene = backend.env.scene
        board = next(obj for obj in scene.objects if obj.category in {'cutting_board', 'chopping_board'})
        onion = next(obj for obj in scene.objects if 'onion' in obj.category and not obj.category.startswith('half_'))
        knife = next(obj for obj in scene.objects if 'slicer' in obj._abilities)
        stage('objects', board=board.name, onion=onion.name, knife=knife.name,
              categories={obj.name: obj.category for obj in (board, onion, knife)})
        knife_pose = tuple(p.clone() for p in knife.get_position_orientation())
        backend._ideal_grasp(onion, 6)
        backend._checked_place_on_top(board, 6000)
        backend._ideal_grasp(knife, 6)
        half_category = 'half_' + onion.category
        cuts = [backend._checked_cut(onion, 6)]
        halves = [obj for obj in scene.objects if obj.category == half_category]
        for half in halves:
            cuts.append(backend._checked_cut(half, 6))
        backend._carry_detach()
        knife.set_position_orientation(*knife_pose); knife.keep_still()
        for _ in range(60): backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
        # The component setup above uses direct state-selected actions. Approach
        # the board before testing base motion, so it is not swung around the
        # robot from the original two-metre-away spawn position.
        approach = backend._navigate(board, 6000)
        stage('approached_board', result=approach, robot_position=backend.robot.get_position_orientation()[0].tolist())
        harness.refresh()
        systems = [system for system in scene.active_systems.values()
                   if getattr(system, 'particles_view', None) is not None and system.n_particles]
        # Independent column check ignores all macro particles, but does not
        # ignore any intervening rigid object. This scene's settled diced pile
        # should all be carried, not just its bottom layer.
        paths = [path for system in systems for path in system.particles_view.prim_paths]
        board_links = {link.prim_path for link in board.links.values()}

        def column_particles():
            found = set()
            for system in systems:
                centers, _ = system.get_particles_position_orientation()
                for i, path in enumerate(system.particles_view.prim_paths):
                    end = centers[i].clone(); end[2] -= .3
                    hit = raytest(centers[i], end, ignore_bodies=paths, ignore_collisions=paths)
                    if hit['hit'] and hit['rigidBody'] in board_links and float(hit['normal'][2]) > 0:
                        found.add(path)
            return found

        expected = column_particles()
        # The initial board can be flush with its counter without OG's strict
        # contact predicate being true. Identify its actual collision support
        # and use that surface point, as the production RGB placement does.
        ignored = paths + list(board_links)
        board_center = board.get_position_orientation()[0]
        end = board_center.clone(); end[2] -= .3
        support_hit = raytest(board_center, end, ignore_bodies=ignored, ignore_collisions=ignored)
        if not support_hit['hit'] or float(support_hit['normal'][2]) < .9:
            raise AssertionError('Original board has no upward collision support')
        support = next(obj for obj in scene.objects
                       if support_hit['rigidBody'] in {link.prim_path for link in obj.links.values()})
        support_point = torch.as_tensor(support_hit['position']).clone()
        stage('original_support', target=support.name, point=support_point.tolist())
        payload = snapshot_particles(scene, [board], include_supported=True)
        captured = {path for record in payload for path in record.paths}
        validation['checks']['real_cut_produced_supported_particles'] = bool(expected)
        validation['checks']['whole_settled_pile_captured'] = captured == expected
        stage('cut_and_settled', cuts=cuts, total_particles=len(paths), expected=len(expected),
              captured=len(captured), missed=sorted(expected-captured), extra=sorted(captured-expected),
              board_aabb=[v.tolist() for v in board.aabb],
              systems=[{'name': s.name, 'radius': float(s.particle_contact_radius)} for s in systems])
        if not expected or captured != expected:
            write_json(args.output/'support_debug.json', {
                'particles': [{'system': s.name, 'paths': list(s.particles_view.prim_paths),
                               'centers': s.get_particles_position_orientation()[0].tolist()}
                              for s in systems]})
            raise AssertionError('Settled pile selection differs from independent board column check')
        state = backend.og.sim.dump_state(serialized=False)
        particle_state = snapshot_particle_state(backend.og.sim)
        # Reproduce the pre-fix omission with identical simulator state.
        with patch('manipulation_agent.executors.carry.snapshot_particles',
                   side_effect=lambda scene, containers, **kwargs: snapshot_particles(scene, containers)):
            old = backend._ideal_grasp(board, 6)
        validation['checks']['old_grasp_omits_surface_payload'] = old.get('supported_particles', 0) == 0
        validation['checks']['old_grasp_loses_board_particles'] = len(column_particles() & expected) < len(expected)
        stage('old_grasp', retained=len(column_particles() & expected), result=old)
        backend._carry_detach()
        backend.og.sim.load_state(state, serialized=False)
        restore_particle_state(particle_state)
        grasp = backend._ideal_grasp(board, 6)
        payload = list(backend._carry_particles)
        validation['checks']['fixed_grasp_preserves_whole_pile'] = (
            particle_count(payload, 'supported') == len(expected) and column_particles() == expected)
        stage('fixed_grasp', retained=len(column_particles()), result=grasp)
        backend._turn(45, 2000)
        support_errors = []
        for record in payload:
            centers, _ = record.system.get_particles_position_orientation()
            indices = {path: i for i, path in enumerate(record.system.particles_view.prim_paths)}
            position, orientation = record.link.get_position_orientation()
            rotation = T.quat2mat(orientation)
            raw = record.system.particles_view.get_transforms()
            for local_index, (path, (parent, offset, vector)) in enumerate(zip(record.paths, record.supports)):
                i = indices[path]
                start, direction = centers[i] + offset @ rotation.T, vector @ rotation.T
                actual = _support_at(path, start, direction)
                if actual != parent:
                    support_errors.append({'path': path, 'expected': parent, 'actual': actual,
                        'center': centers[i].tolist(), 'start': start.tolist(), 'vector': direction.tolist(),
                        'position_error': float(torch.linalg.norm((raw[i, :3]-position) @ rotation - record.positions[local_index]))})
        stage('turn_support_audit', retained=len(column_particles()), support_errors=support_errors,
              board_pose=[p.tolist() for p in board.get_position_orientation()])
        verify_particles(scene, payload)
        validation['checks']['turn_preserves_pile'] = column_particles() == expected
        stage('base_turn', retained=len(column_particles()))
        before = {s.name: s.particles_view.get_transforms().clone() for s in systems}
        try:
            with backend._placement_context():
                backend._carry_detach()
                pos, quat = board.get_position_orientation()
                board.set_position_orientation(pos + torch.tensor([.4, 0., .2]), quat)
                backend._relocate_contents(board, [])
                raise ValueError('injected placement rejection')
        except ValueError:
            pass
        validation['checks']['rollback_restores_poses_and_grasp'] = (
            backend._get_held() is board and all(torch.allclose(before[s.name], s.particles_view.get_transforms(), atol=2e-5)
                                               for s in systems))
        stage('placement_rollback', retained=len(column_particles()))
        # Successful placement on the board's original work surface, using
        # the same actual transactional placement implementation as the agent.
        backend._carry_detach()
        backend.og.sim.load_state(state, serialized=False)
        restore_particle_state(particle_state)
        backend._ideal_grasp(board, 6)
        placed = backend._checked_place_on_top(support, 6000, point=support_point)
        center = board.get_position_orientation()[0]
        end = center.clone(); end[2] -= .3
        hit = raytest(center, end, ignore_bodies=ignored, ignore_collisions=ignored)
        validation['checks']['placed_board_on_surface'] = bool(
            hit['hit'] and hit['rigidBody'] in {link.prim_path for link in support.links.values()} and
            float(hit['normal'][2]) > .9 and abs(float(board.aabb[0][2]-hit['position'][2])) < .02)
        validation['checks']['placement_retains_pile'] = column_particles() == expected
        validation['checks']['placement_detaches_particle_carry'] = not backend._carry_particles
        stage('placed_board', retained=len(column_particles()), target=support.name, result=placed,
              independent_check='nearest_upward_collision_support_and_bottom_gap_under_2cm',
              official_on_top=bool(board.states[OnTop].get_value(support)))
        harness.refresh()
        backend._ideal_grasp(board, 6)
        backend._ideal_release(1)
        before = {s.name: s.particles_view.get_transforms().clone() for s in systems}
        pos, quat = board.get_position_orientation()
        board.set_position_orientation(pos + torch.tensor([.4, 0., .2]), quat)
        backend._relocate_contents(board, [])
        validation['checks']['release_stops_particle_following'] = (
            backend._get_held() is None and not backend._carry_particles and
            all(torch.equal(before[s.name], s.particles_view.get_transforms()) for s in systems))
        stage('released', particle_following=False)
    except Exception as exc:
        validation['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        (args.output/'traceback.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    finally:
        try:
            if harness is not None:
                try:
                    result = harness.call('finish', {'outcome': 'aborted',
                        'reason': 'Private surface particle regression; no autonomous task-success claim.'}, 'probe-finish')
                    harness.finalize_recording()
                    validation['checks']['finish_closed'] = result.get('closed') is True
                    validation['checks']['video_finalized'] = recorder.run.get('video', {}).get('status') == 'passed'
                except Exception as exc:
                    validation['finish_error'] = {'type': type(exc).__name__, 'message': str(exc)}
            validation['status'] = ('passed' if validation['checks'] and all(validation['checks'].values())
                                    and not validation.get('error') and not validation.get('finish_error') else 'failed')
            write_json(args.output/'validation.json', validation)
        finally:
            if backend is not None: backend.close()
            else:
                from manipulation_agent.startup_cleanup import shutdown_partial_simulator
                shutdown_partial_simulator()
    return 0 if validation['status'] == 'passed' else 2


if __name__ == '__main__': raise SystemExit(main())
