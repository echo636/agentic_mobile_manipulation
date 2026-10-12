"""Private popcorn container/particle regression; not an autonomous task trial."""
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
    recorder = Recorder(args.output, dict(task='make_microwave_popcorn', instance=301, seed=0,
        policy='private_particle_payload_probe', model_used=False, not_a_benchmark_attempt=True))
    validation = {'status': 'running', 'validation_level': 'real_simulator_private_particle_component',
                  'model_used': False, 'whole_task_success_claimed': False, 'checks': {}, 'stages': []}
    write_json(args.output/'validation.json', validation)
    backend = harness = None
    def stage(name, **data):
        validation['stages'].append(dict(stage=name, **data))
        write_json(args.output/'validation.json', validation)
        print(json.dumps(validation['stages'][-1]), flush=True)
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.executors.visual_mesh_grounding import query_visual_surface
        from manipulation_agent.executors.particle_payload import verify_particles, particle_count, snapshot_particle_state, restore_particle_state
        from manipulation_agent.vision_harness import VisionHarness
        from manipulation_agent.contracts import Budget, SkillError
        from omnigibson.object_states import ContainedParticles, Inside
        import omnigibson.utils.transform_utils as T
        backend = RGBBackend('make_microwave_popcorn', 301, args.output, seed=0,
                             max_steps=40000, inside_placement='official_volume', record_video=True)
        backend.deadline.arm_local(1800)
        harness = VisionHarness(backend, recorder, Budget(wall_seconds=1800), profile='skills')
        torch = backend.torch
        objects = {obj.name: obj for obj in backend.env.scene.objects}
        bag, microwave = objects['popcorn_bag_73'], objects['microwave_hjjxmi_0']
        system = backend.env.scene.get_system('popcorn', force_init=False)
        link = bag.states[ContainedParticles].link
        def count(): return int(link.check_points_in_volume(system.get_particles_position_orientation()[0]).sum())
        backend._ideal_state_action('open', microwave, 30)
        harness.refresh()
        initial_count = count()
        state = backend.og.sim.dump_state(serialized=False)
        particle_state = snapshot_particle_state(backend.og.sim)
        stage('initialized', contained=initial_count, total=system.n_particles)
        # Reproduce the old carry omission in the same loaded scene.
        with patch('manipulation_agent.executors.carry.snapshot_particles', return_value=[]):
            backend._ideal_grasp(bag, 6)
        baseline_count = count()
        validation['checks']['old_carry_loses_contents'] = baseline_count < initial_count
        stage('old_carry', contained=baseline_count)
        # Query an actual particle triangle using its current physical pose.
        view = system.particles_view
        path = view.prim_paths[0]
        mesh = next(p for p in system.particles.values() if p.prim_path == path)
        raw = view.get_transforms()[0]
        point = T.quat2mat(raw[3:]) @ (mesh.points[mesh.faces.reshape(-1, 3)[0]].mean(0)*mesh.scale) + raw[:3]
        try:
            query_visual_surface(backend, point, reject_particles=True)
            validation['checks']['particle_surface_not_countertop'] = False
        except SkillError as exc:
            validation['checks']['particle_surface_not_countertop'] = exc.code == 'particle_target'
            stage('particle_grounding', code=exc.code, detail=getattr(exc, 'diagnostics', {}))
        backend._carry_detach()
        backend.og.sim.load_state(state, serialized=False)
        restore_particle_state(particle_state)
        validation['checks']['baseline_restore_preserves_initial_particles'] = count() == initial_count
        fixed = backend._ideal_grasp(bag, 6)
        payload = list(backend._carry_particles)
        validation['checks']['fixed_grasp_preserves_contents'] = count() == initial_count == particle_count(payload)
        stage('fixed_grasp', contained=count(), result=fixed)
        backend._turn(45, 2000)
        verify_particles(backend.env.scene, payload)
        validation['checks']['base_turn_preserves_contents'] = count() == initial_count
        stage('base_turn', contained=count())
        # Failed placement must restore particles and the carry relationship.
        before = system.particles_view.get_transforms().clone()
        try:
            with backend._placement_context(microwave):
                backend._carry_detach()
                pos, quat = bag.get_position_orientation()
                bag.set_position_orientation(pos + torch.tensor([.4, 0., .2]), quat)
                backend._relocate_contents(bag, [])
                raise ValueError('injected placement rejection')
        except ValueError:
            pass
        validation['checks']['rollback_restores_particle_poses_and_grasp'] = (
            backend._get_held() is bag and torch.allclose(before, system.particles_view.get_transforms(), atol=2e-5))
        stage('placement_rollback', contained=count())
        placement = backend._checked_place_inside(microwave, 6000)
        validation['checks']['placed_bag_inside_microwave'] = bool(bag.states[Inside].get_value(microwave))
        validation['checks']['placed_bag_retains_particles'] = count() == initial_count
        validation['checks']['placement_detaches_particle_carry'] = not backend._carry_particles
        stage('placed_inside', contained=count(), result=placement)
        harness.refresh()
        # Pickup and release stop enforcing particle poses; ordinary physics resumes.
        backend._ideal_grasp(bag, 6)
        backend._ideal_release(1)
        validation['checks']['release_detaches_particles'] = backend._get_held() is None and not backend._carry_particles
        stage('release', contained=count())
    except Exception as exc:
        validation['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        (args.output/'traceback.txt').write_text(traceback.format_exc())
        print(traceback.format_exc(), flush=True)
    finally:
        try:
            if harness is not None:
                try:
                    result = harness.call('finish', {'outcome': 'aborted', 'reason': 'Private particle regression; no autonomous task-success claim.'}, 'probe-finish')
                    harness.finalize_recording()
                    validation['checks']['finish_closed'] = result.get('closed') is True
                    validation['checks']['video_finalized'] = recorder.run.get('video', {}).get('status') == 'passed'
                except Exception as exc:
                    validation['finish_error'] = {'type': type(exc).__name__, 'message': str(exc)}
            validation['status'] = 'passed' if validation['checks'] and all(validation['checks'].values()) and not validation.get('error') and not validation.get('finish_error') else 'failed'
            write_json(args.output/'validation.json', validation)
        finally:
            if backend is not None: backend.close()
            else:
                from manipulation_agent.startup_cleanup import shutdown_partial_simulator
                shutdown_partial_simulator()
    return 0 if validation['status'] == 'passed' else 2


if __name__ == '__main__': raise SystemExit(main())
