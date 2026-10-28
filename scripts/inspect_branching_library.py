"""Capture a frozen library scene, then attempt its checked full reference.

Inspection views are human evidence only, never visual-policy observations.
The reference uses privileged object identities and the normal checked executor.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))
from manipulation_agent.tasks.custom_tasks import load_spec, sha256
from manipulation_agent.records import write_json, release_source_digest
from manipulation_agent.references.reference_evidence import close_reference, complete_reference_exit

SHOTS = [
    ('library', '公共书柜、6 个笔袋及工具与收纳台', [-6.0, -3.2, 2.2], [-4.5, -1.25, .5]),
    ('shelf', '原生矮书柜顶面的笔袋与初始灰尘', [-4.6, -1.9, 2.1], [-4.6, -1.05, .85]),
    ('a_overview', 'A 卧室地面物品及暂存台', [-4.45, -.15, 2.1], [-4.45, 1.8, .4]),
    ('a_stage', 'A 房间积尘暂存台', [-4.6, 1.0, 1.6], [-5.61, 1.05, .45]),
    ('b_overview', 'B 卧室地面物品及暂存台', [4.45, -.15, 2.1], [4.45, 1.8, .4]),
    ('b_stage', 'B 房间积尘暂存台', [4.6, .5, 1.6], [3.32, .5, .45]),
]


def capture(backend, output):
    import numpy as np
    from PIL import Image
    from manipulation_agent.observations.rig import look_at_orientation
    sensor = backend.rig['front']
    camera_pose = [v.clone() for v in sensor.get_position_orientation()]
    initial = {o.name: [v.clone() for v in o.get_position_orientation()] for o in backend.env.scene.objects}
    directory = output / 'scene_views'; directory.mkdir()
    shots = []
    try:
        for name, label, position, target in SHOTS:
            orientation = look_at_orientation(position, target)
            sensor.set_position_orientation(backend.torch.tensor(position, dtype=backend.torch.float32),
                                            backend.torch.tensor(orientation, dtype=backend.torch.float32))
            for _ in range(8):
                backend.og.sim.render()
            backend._wait_for_rgb_annotations()
            data, _ = sensor.get_obs()
            rgb = data['rgb'].detach().cpu().numpy()[..., :3].astype('uint8')
            path = directory / (name + '.jpg'); Image.fromarray(rgb).save(path, quality=95)
            shots.append(dict(id=name, label=label, file='scene_views/' + path.name, sha256=sha256(path),
                              position=position, target=target, width=rgb.shape[1], height=rgb.shape[0]))
        for obj in backend.env.scene.objects:
            for before, after in zip(initial[obj.name], obj.get_position_orientation()):
                if not np.allclose(before.cpu().numpy(), after.cpu().numpy(), atol=1e-6):
                    raise RuntimeError('Render capture changed object pose: ' + obj.name)
    finally:
        sensor.set_position_orientation(*camera_pose)
        for _ in range(8):
            backend.og.sim.render()
        backend.frames_revision = -1
    write_json(output / 'scene_views.json', dict(status='passed', views=shots, task_actions=0,
               object_poses_unchanged=True, source='OmniGibson renderer; frozen initial scene'))


def inspect(backend, reference, output):
    from omnigibson.object_states import Covered, ParticleRemover
    tool = reference.obj('wipe_tool')
    remover = tool.states[ParticleRemover]
    tool_check = dict(category=tool.category, method=str(remover.method),
                     supports_dust=bool(remover.supports_system('dust')),
                     ready_for_dust=bool(remover.check_conditions_for_system('dust')))
    surfaces = {r: dict(covered_supported=Covered in reference.obj(r).states,
                        dusty=bool(reference.obj(r).states[Covered].get_value(backend.env.scene.get_system('dust'))))
                for r in ['shared_shelf', 'a_stage', 'b_stage']}
    lo, hi = [v.tolist() for v in reference.obj('shared_shelf').aabb]
    slots = []
    for room in backend.custom_spec['reference_plan']['rooms']:
        for w in room['work']:
            if w['destination'] != 'shared_shelf':
                continue
            low, high = [v.tolist() for v in reference.obj(w['item']).aabb]
            size = [high[i] - low[i] for i in (0, 1)]
            xy = w['destination_xy']
            slots.append(dict(item=w['item'], xy=xy, size=size,
                inside_shelf_envelope=all(lo[i] <= xy[i] - size[i] / 2 and xy[i] + size[i] / 2 <= hi[i] for i in (0, 1))))
    overlaps = [[a['item'], b['item']] for i, a in enumerate(slots) for b in slots[i+1:]
                if all(abs(a['xy'][k] - b['xy'][k]) < (a['size'][k] + b['size'][k]) / 2 for k in (0, 1))]
    record = dict(status='passed' if tool_check['ready_for_dust'] and all(s['inside_shelf_envelope'] for s in slots)
                  and not overlaps else 'failed', tool=tool_check, surfaces=surfaces,
                  shelf_aabb=[lo, hi], book_slots=slots, slot_overlaps=overlaps,
                  door_joint_positions={r: reference.obj(r).get_joint_positions().tolist()
                                        for r in ['a_door', 'b_door']},
                  robot_position=backend.robot.get_position_orientation()[0].tolist(),
                  validation_level='native state compatibility and AABB slot screen; actual placement and routing separate')
    write_json(output / 'library_geometry_validation.json', record)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--order', default='library_then_a_then_b')
    p.add_argument('--worker', action='store_true')
    a = p.parse_args()
    if not a.worker:
        if a.output.exists():
            p.error('Output already exists; preserve prior evidence')
        child = subprocess.run([sys.executable, '-B', '-u', __file__, *sys.argv[1:], '--worker'])
        return 0 if complete_reference_exit(a.output, child.returncode) else 1
    spec, _ = load_spec(a.spec, require_frozen=True)
    a.output.mkdir(parents=True, exist_ok=False)
    freeze = json.loads((SOURCE / 'source_freeze.json').read_text())
    record = dict(status='running', host=platform.node(), interpreter=sys.executable, pid=os.getpid(),
        unit=os.environ.get('MAS_UNIT'), gpu_uuid=os.environ.get('MAS_GPU_UUID'), output_path=str(a.output),
        source_commit=freeze['source_commit'], source_sha256=release_source_digest(SOURCE),
        spec_sha256=sha256(a.spec), scene_sha256=spec['frozen_instance']['sha256'],
        validation_level='frozen scene inspection followed by real simulator checked full reference',
        order=a.order, model_used=False, benchmark_ready=False, evaluation=None)
    write_json(a.output / 'run_record.json', record)
    backend = reference = None
    try:
        from manipulation_agent.executors.custom_task import CustomTaskBackend
        from manipulation_agent.references.reference_tasks import ReferenceTask
        backend = CustomTaskBackend(a.spec, a.output, record_video=False,
                                    inside_placement='official_volume', max_steps=1000000)
        record['backend'] = backend.provenance()
        reference = ReferenceTask(backend, a.output, max_attempts=1000)
        capture(backend, a.output)
        record['inspection_status'] = 'passed'
        geometry = inspect(backend, reference, a.output)
        record['geometry_status'] = geometry['status']
        if geometry['status'] != 'passed':
            raise RuntimeError('Library geometry or tool compatibility screen failed')
        backend.observe()
        reference.run(a.order)
        record['evaluation'] = backend.evaluate()
        record['status'] = 'passed' if record['evaluation']['task_success'] else 'failed'
    except BaseException as exc:
        record.update(status='failed', error=str(exc), error_code=getattr(exc, 'code', None))
        (a.output / 'traceback.txt').write_text(traceback.format_exc())
        if backend is not None:
            record['evaluation'] = backend.evaluate()
            backend.env.scene.save(json_path=str(a.output / 'failure_scene.json'))
    finally:
        record['action_attempts'] = len(reference.calls) if reference else 0
        record['autonomous_success_claimed'] = False
        close_reference(backend, record, a.output)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
