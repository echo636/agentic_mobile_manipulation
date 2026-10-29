"""Opt-in native physics reload before articulation edits after material edits."""
from copy import deepcopy
from functools import wraps
from pathlib import Path
import json

PROTOCOL = 'native_particle_edit_articulation_barrier_v2'


def install(record_path):
    import omnigibson as og
    if og.sim is not None:
        raise RuntimeError('Native edit barrier must be installed before simulator launch')
    path = Path(record_path)
    record = {'protocol': PROTOCOL, 'material_generation': 0, 'rebuilt_generation': 0,
              'rebuilds': [], 'explicit_physics_steps_added': 0,
              'scope': 'native object/system state restored; physics context and internal clock may restart'}
    def save():
        path.write_text(json.dumps(record, indent=2) + '\n')
    save()
    launch = og.launch
    @wraps(launch)
    def configured(*args, **kwargs):
        value = launch(*args, **kwargs)
        sim = og.sim
        def note_material_edit(**details):
            record['material_generation'] += 1
            record['last_edit'] = details
            save()
        def readiness():
            from omnigibson.object_states import ParticleRemover
            values = {}
            for index, scene in enumerate(sim.scenes):
                for obj in scene.objects:
                    modifier = obj.states.get(ParticleRemover)
                    if modifier is None:
                        continue
                    for name in scene.active_systems:
                        if modifier.supports_system(name):
                            values[f'{index}/{obj.name}/{name}'] = bool(modifier.check_conditions_for_system(name))
            return values
        def prepare_articulation(*, force=False, reason=None):
            if not force and record['rebuilt_generation'] == record['material_generation']:
                return
            # Deep copy is essential: NumPy-backed native tensors can alias
            # buffers that play/reset overwrites.
            readiness_before = readiness()
            state = deepcopy(sim.dump_state(serialized=False))
            row = {'generation': record['material_generation'], 'status': 'started', 'reason': reason,
                   'native_time_before': getattr(sim, 'current_time', None)}
            record['rebuilds'].append(row); save()
            sim.stop()
            sim.play()
            sim.load_state(state, serialized=False)
            after = sim.dump_state(serialized=False)
            from omnigibson.systems.macro_particle_system import MacroParticleSystem
            counters = {}
            for index, scene in enumerate(sim.scenes):
                for system in scene.system_registry.objects:
                    if isinstance(system, MacroParticleSystem):
                        counters[f'/{index}/registry/system_registry/{system.name}/particle_counter'] = (
                            max(system.particle_idns) + 1 if system.particle_idns else 0)
            differences, equivalents = compare_native_state(state, after, canonical_counters=counters)
            if readiness_before != readiness():
                differences.append('native material readiness changed')
            row.update(native_time_after=getattr(sim, 'current_time', None),
                       native_serialization_equivalences=equivalents[:30], state_differences=differences[:30], state_difference_count=len(differences),
                       status='passed' if not differences else 'failed')
            save()
            if differences:
                raise RuntimeError('Native physics edit barrier did not preserve object/system state: ' + str(differences[:3]))
            record['rebuilt_generation'] = record['material_generation']; save()
        sim.note_material_edit = note_material_edit
        sim.prepare_articulation_after_material_edit = prepare_articulation
        sim._native_particle_edit_barrier = True
        return value
    og.launch = configured
    return record


def compare_native_state(before, after, *, canonical_counters=None):
    """Compare physical values while recognizing documented native load formats."""
    import torch
    counters = canonical_counters or {}
    differences, equivalents = [], []
    physical_names = {'pos', 'ori', 'lin_vel', 'ang_vel', 'joint_pos', 'joint_vel',
                      'particle_positions', 'particle_orientations', 'particle_velocities'}
    def compare(left, right, path=''):
        if path.endswith('/non_kin/ModifiedParticles'):
            # Native ModifiedParticles._load_state retains only positive entries.
            left = {key: value for key, value in left.items() if key != 'n_systems' and value > 0}
            right = {key: value for key, value in right.items() if key != 'n_systems' and value > 0}
        if path in counters and left != right:
            if right == counters[path]:
                equivalents.append({'path': path, 'kind': 'native_next_available_particle_id'})
                return
        if isinstance(left, dict):
            if not isinstance(right, dict) or set(left) != set(right):
                differences.append(path + ': keys differ'); return
            for key in left:
                compare(left[key], right[key], path + '/' + str(key))
        elif isinstance(left, (tuple, list)):
            if len(left) != len(right):
                differences.append(path + ': length differs'); return
            for index, (a, b) in enumerate(zip(left, right)):
                compare(a, b, path + '/' + str(index))
        elif isinstance(left, torch.Tensor):
            if path.rsplit('/', 1)[-1] in physical_names and (
                    not torch.isfinite(left).all() or not torch.isfinite(right).all()):
                differences.append(path + ': nonfinite physical state'); return
            if left.numel() == right.numel() == 0:
                return
            if left.shape != right.shape:
                differences.append(path + ': tensor shape differs'); return
            if torch.allclose(left.cpu(), right.cpu(), rtol=1e-5, atol=1e-5, equal_nan=True):
                return
            if path.endswith('/particle_orientations') and left.ndim == 2 and left.shape[1] == 4:
                # Native instancers use Vt.QuathArray; load/dump also normalizes
                # scene-frame quaternions. Respect q == -q and two half ULPs.
                a, b = left.double(), right.double()
                an, bn = a.norm(dim=1, keepdim=True), b.norm(dim=1, keepdim=True)
                if bool((an > 0).all() and (bn > 0).all()):
                    a, b = a/an, b/bn
                    b = torch.where((a*b).sum(dim=1, keepdim=True) < 0, -b, b)
                    error = float((a-b).abs().max())
                    if error <= 2**-9:
                        equivalents.append({'path': path, 'kind': 'native_half_quaternion', 'max_component_error': error})
                        return
            differences.append(path + ': tensor differs')
        elif left != right:
            differences.append(path + ': value differs')
    compare(before, after)
    return differences, equivalents
