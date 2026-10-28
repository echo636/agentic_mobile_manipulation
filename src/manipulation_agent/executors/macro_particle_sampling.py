"""Convert thin rigid sources without requiring an inscribed particle sphere."""
from functools import wraps

from ..contracts import SkillError


def thin_macro_sampler(original, record):
    @wraps(original)
    def generate(system, obj, link, use_visual_meshes=True, mesh_name_prefixes=None,
                 check_contact=True, sampling_distance=None, max_samples=None, **kwargs):
        import torch

        try:
            low, high = link.visual_aabb
        except ValueError:
            low, high = obj.aabb
        radius = float(system.particle_radius)
        thin = high - low <= 2 * radius
        if not bool(thin.any()):
            return original(system, obj, link, use_visual_meshes=use_visual_meshes,
                mesh_name_prefixes=mesh_name_prefixes, check_contact=check_contact,
                sampling_distance=sampling_distance, max_samples=max_samples, **kwargs)

        # A macro particle is an authored rigid mesh, not its enclosing sphere.
        # A thin source occupies one layer of the native conversion grid; its
        # thickness need not contain that sphere. Retain native spacing on the
        # other axes and keep only centers inside the actual source geometry.
        spacing = system.particle_particle_rest_distance if sampling_distance is None else sampling_distance
        axes = [(lo + hi).reshape(1) / 2 if narrow else
                torch.arange(lo + radius, hi - radius, spacing, device=low.device)
                for lo, hi, narrow in zip(low, high, thin)]
        positions = torch.stack(torch.meshgrid(*axes, indexing='ij'), dim=-1).reshape(-1, 3)
        positions = positions[link.check_points_in_volume(
            particle_positions_world=positions, use_visual_meshes=use_visual_meshes)]
        if check_contact:
            positions = positions[~system.check_in_contact(positions)]
        if max_samples is not None and len(positions) > max_samples:
            positions = positions[torch.randperm(len(positions))[:int(max_samples)]]
        if len(positions) == 0:
            # The caller removes the source after this returns. Do not consume
            # real material when the native conversion grid has no interior.
            raise SkillError('sampling_error', 'No interior particle samples for the selected material conversion')
        record({'source': obj.name, 'link': link.name, 'system': system.name,
                'source_extent_m': (high-low).tolist(), 'particle_radius_m': radius,
                'thin_axes': thin.tolist(), 'generated': len(positions),
                'authored_particle_geometry_unchanged': True})
        return system.generate_particles(positions=positions, **kwargs)

    return generate
