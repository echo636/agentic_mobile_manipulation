"""Native asset / checked-executor probes, never complete long-task acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))
from manipulation_agent.records import write_json, release_source_digest
from manipulation_agent.reference_evidence import close_reference, complete_reference_exit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--order', required=True, choices=['apple_cut', 'furniture_attach', 'cloth_transfer'])
    parser.add_argument('--record-video', action='store_true')
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if not args.worker:
        if args.output.exists():
            parser.error('Choose a fresh immutable output')
        child = subprocess.run([sys.executable, '-B', '-u', str(Path(__file__).resolve()), *sys.argv[1:], '--worker'])
        return 0 if complete_reference_exit(args.output, child.returncode) else 1
    spec = json.loads(args.spec.read_text())
    assert spec['probe'] == args.order and spec['kind'] == 'extended_workflow_capability_probe'
    args.output.mkdir(parents=True, exist_ok=False)
    freeze = SOURCE / 'source_freeze.json'
    commit = (json.loads(freeze.read_text())['source_commit'] if freeze.exists() else
              subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip())
    record = dict(status='running', started_at_unix=time.time(), host=platform.node(),
                  interpreter=sys.executable, pid=os.getpid(), unit=os.environ.get('MAS_UNIT'),
                  gpu_uuid=os.environ.get('MAS_GPU_UUID'), source_commit=commit,
                  source_sha256=release_source_digest(SOURCE), spec_sha256=hashlib.sha256(args.spec.read_bytes()).hexdigest(),
                  output_path=str(args.output), probe=args.order, affected_tasks=spec['affected_tasks'],
                  validation_level='real_simulator_native_asset_checked_executor_capability_probe',
                  model_used=False, benchmark_ready=False, full_task_executed=False, evaluation=None)
    write_json(args.output / 'run_record.json', record)
    backend = reference = None
    try:
        from manipulation_agent.executors.omnigibson_rgb import RGBBackend
        from manipulation_agent.reference_tasks import ReferenceTask

        class ProbeBackend(RGBBackend):
            def _prepare_task_instance(self):
                from omnigibson.objects import DatasetObject, PrimitiveObject
                from omnigibson.macros import gm
                self.custom_spec = {'id': 'capability_probe_' + args.order, 'roles': {}}
                self.probe_objects = {}
                write_json(self.output / 'probe_setup.json', dict(status='running',
                    use_gpu_dynamics=gm.USE_GPU_DYNAMICS, native_models=spec['native_models'],
                    scene_authoring_only=True))
                self.og.sim.stop()
                protected = {getattr(o, 'name', None) for o in self.env.task.object_scope.values()}
                for name in spec['scene_exclusions']:
                    obj = next((o for o in self.env.scene.objects if o.name == name), None)
                    if obj is not None:
                        if name in protected:
                            raise ValueError('Cannot remove a base-task entity')
                        self.env.scene.remove_object(obj)
                for role, xy in [('source', [13.2, 8.4]), ('destination', [15.5, 8.4])]:
                    obj = PrimitiveObject(name='v2_probe_' + role, primitive_type='Cube', size=1.,
                                          scale=[1.8, 1.6, .45], fixed_base=True,
                                          rgba=[.3, .4, .6, 1.], category='work_surface')
                    self.env.scene.add_object(obj)
                    obj.set_position_orientation(self.torch.tensor([*xy, .225]), self.torch.tensor([0., 0., 0., 1.]))
                    self.probe_objects[role] = obj
                for row in spec['native_models']:
                    role = row['role']
                    obj = DatasetObject(name='v2_probe_' + role, category=row['category'], model=row['model'],
                                        prim_type=row.get('prim_type', 0), fixed_base=False)
                    self.env.scene.add_object(obj)
                    obj.set_position_orientation(self.torch.tensor([*row['xy'], 1.2]),
                                                 self.torch.tensor(row.get('orientation', [0., 0., 0., 1.])))
                    self.probe_objects[role] = obj
                self.og.sim.play()
                for row in spec['native_models']:
                    obj = self.probe_objects[row['role']]
                    low, high = obj.aabb
                    position, orientation = obj.get_position_orientation()
                    position[:2] += self.torch.tensor(row['xy']) - (low[:2] + high[:2]) / 2
                    position[2] += self.probe_objects[row['support']].aabb[1][2] - low[2] + .008
                    obj.set_position_orientation(position, orientation)
                    if row.get('prim_type', 0) == 0:
                        obj.keep_still()
                self.robot.set_position_orientation(self.torch.tensor([14.7, 9.7, .02]),
                                                    self.torch.tensor([0., 0., 0., 1.]))
                self.robot.keep_still()
                for _ in range(30):
                    self.og.sim.step()
                self.custom_spec['roles'] = {role: obj.name for role, obj in self.probe_objects.items()}
                self.initial_names = {obj.name for obj in self.env.scene.objects}
                write_json(self.output / 'probe_initial_geometry.json', {
                    role: dict(name=obj.name, category=obj.category,
                               aabb=[v.tolist() for v in obj.aabb],
                               pose=[v.tolist() for v in obj.get_position_orientation()],
                               states=[s.__name__ for s in obj.states]) for role, obj in self.probe_objects.items()})
                self.env.scene.save(json_path=str(self.output / 'probe_initial_scene.json'))
                self.steps = 0
                write_json(self.output / 'probe_setup.json', dict(status='passed',
                    use_gpu_dynamics=gm.USE_GPU_DYNAMICS, scene_authoring_only=True))

            def _predicate_values(self):
                from omnigibson.object_states import AttachedTo, OnTop
                live = {o.name: o for o in self.env.scene.objects}
                if args.order == 'furniture_attach':
                    pairs = [('bracket', 'top'), *[('leg' + str(i), 'bracket') for i in range(4)]]
                    return {child + '_attached': bool(self.probe_objects[child].states[AttachedTo].get_value(
                            self.probe_objects[parent])) for child, parent in pairs}
                if args.order == 'apple_cut':
                    return dict(parent_removed='v2_probe_apple' not in live,
                                real_halves=len([o for o in live.values() if o.category == 'half_apple'
                                                and o.name not in self.initial_names]) == 2)
                return {r + '_delivered': bool(self.probe_objects[r].states[OnTop].get_value(
                        self.probe_objects['destination'])) for r in ['blouse', 'boxers']}

        backend = ProbeBackend.__new__(ProbeBackend)
        backend.__init__(spec['base']['task'], 301, args.output, seed=0, max_steps=100000,
                         inside_placement='official_volume', record_video=args.record_video)
        record['backend'] = backend.provenance()
        backend.observe()
        write_json(args.output / 'custom_policy_ready_validation.json', dict(status='passed',
                   scope='capability probe initialized; not a full-task fixture'))
        reference = ReferenceTask(backend, args.output, max_attempts=40)
        if args.order == 'furniture_attach':
            reference.action('grasp', 'bracket')
            reference.action('attach', 'top')
            for i in range(4):
                reference.action('grasp', 'leg' + str(i))
                reference.action('attach', 'bracket')
            reference.action('wait', seconds=1.)
        elif args.order == 'apple_cut':
            reference.action('grasp', 'knife')
            reference.action('cut', 'apple')
            reference.action('place_on_top', 'destination', reference.top('destination', .2, .2))
            halves = [o for o in backend.env.scene.objects if o.category == 'half_apple'
                      and o.name not in backend.initial_names]
            if len(halves) != 2:
                raise RuntimeError('Cut did not produce exactly two native half-apple objects')
            for i, obj in enumerate(halves):
                role = 'half' + str(i)
                backend.custom_spec['roles'][role] = obj.name
                reference.action('grasp', role)
                reference.action('place_on_top', 'destination', reference.top('destination', .4 + i * .25, .7))
        else:
            for i, role in enumerate(['blouse', 'boxers']):
                reference.action('grasp', role)
                reference.action('place_on_top', 'destination', reference.top('destination', .25 + i * .5, .5))
        predicates = backend._predicate_values()
        record.update(status='passed' if all(predicates.values()) else 'failed',
                      evaluation=dict(capability_success=all(predicates.values()), predicates=predicates,
                                      full_task_success=None),
                      scope_limit='A probe pass does not establish assembled-table usability, washing/drying or the complete workflows')
    except BaseException as exc:
        record.update(status='failed', error=str(exc), error_type=type(exc).__name__, error_code=getattr(exc, 'code', None))
        (args.output / 'traceback.txt').write_text(traceback.format_exc())
        if backend is not None and hasattr(backend, 'initial_names'):
            try:
                record['evaluation'] = dict(capability_success=False, predicates=backend._predicate_values(), full_task_success=None)
                backend.observe()
                backend.env.scene.save(json_path=str(args.output / 'failure_scene.json'))
            except Exception:
                (args.output / 'failure_evidence_error.txt').write_text(traceback.format_exc())
    finally:
        record.update(action_attempts=len(reference.calls) if reference else 0, finished_at_unix=time.time())
        close_reference(backend, record, args.output)
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
