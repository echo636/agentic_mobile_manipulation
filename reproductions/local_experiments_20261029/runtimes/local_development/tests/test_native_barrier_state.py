from copy import deepcopy
import torch
from manipulation_agent.simulator_barriers import compare_native_state


def native_state():
    return {0: {'registry': {
        'object_registry': {'mop': {'non_kin': {'ModifiedParticles': {
            'n_systems': 2, 'water': 5, 'dust': 0}}}},
        'system_registry': {
            'dust': {'particle_counter': 12},
            'water': {'particle_states': {'waterInstancer0': {
                'particle_positions': torch.tensor([[1., 2., 3.]]),
                'particle_orientations': torch.tensor([[0., 0., 0., 1.]])}}}}}}}


def test_native_sparse_zero_counts_allocator_and_quaternion_sign_are_equivalent():
    before = native_state(); after = deepcopy(before)
    state = after[0]['registry']
    state['object_registry']['mop']['non_kin']['ModifiedParticles'] = {'n_systems': 1, 'water': 5}
    state['system_registry']['dust']['particle_counter'] = 0
    state['system_registry']['water']['particle_states']['waterInstancer0']['particle_orientations'] *= -1
    errors, equivalents = compare_native_state(before, after, canonical_counters={
        '/0/registry/system_registry/dust/particle_counter': 0})
    assert errors == []
    assert len(equivalents) == 2


def test_consumed_material_and_changed_particle_positions_are_rejected():
    before = native_state(); after = deepcopy(before)
    after[0]['registry']['object_registry']['mop']['non_kin']['ModifiedParticles']['water'] = 0
    after[0]['registry']['system_registry']['water']['particle_states']['waterInstancer0']['particle_positions'][0, 0] += .01
    errors, _ = compare_native_state(before, after)
    assert any('ModifiedParticles' in error for error in errors)
    assert any('particle_positions' in error for error in errors)


def test_particle_rotation_beyond_half_precision_and_wrong_allocator_are_rejected():
    before = native_state(); after = deepcopy(before)
    after[0]['registry']['system_registry']['water']['particle_states']['waterInstancer0']['particle_orientations'] = torch.tensor([[0., 0., 1., 0.]])
    after[0]['registry']['system_registry']['dust']['particle_counter'] = 13
    errors, _ = compare_native_state(before, after, canonical_counters={
        '/0/registry/system_registry/dust/particle_counter': 0})
    assert any('particle_orientations' in error for error in errors)
    assert any('particle_counter' in error for error in errors)


def test_nonfinite_physical_state_is_rejected_even_if_both_snapshots_match():
    before = native_state()
    before[0]['registry']['system_registry']['water']['particle_states']['waterInstancer0']['particle_positions'][0, 0] = float('nan')
    errors, _ = compare_native_state(before, deepcopy(before))
    assert any('nonfinite physical state' in error for error in errors)
