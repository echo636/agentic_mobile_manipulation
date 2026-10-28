"""Preserve authored macro particle geometry in the pinned catalog runtime."""
from importlib import metadata


class MacroParticleSizePolicy:
    """Preserve authored size and let thin sources occupy one conversion layer.

    OG 3.9.2 enlarges macro templates to a 1 cm bounding-sphere radius. That
    makes a valid thin green-onion source too small for native DicingRule's
    2r grid. Preserve the authored template instead of changing task objects,
    transition outputs, or evaluation. Native sphere erosion also rejects thin
    sources even with the authored size: use a single center layer on those
    axes. Record both simulator sampling changes separately from defaults.
    """

    def __init__(self):
        self._settings = None
        self._sampler_class = None
        self._conversions = []
        self.record = dict(enabled=False, policy='native',
                           omnigibson=metadata.version('omnigibson'),
                           isaacsim=metadata.version('isaacsim'))
        if (self.record['omnigibson'], self.record['isaacsim']) != ('3.9.2', '5.1.0.0'):
            return
        from omnigibson.systems.macro_particle_system import m
        self._original = float(m.MIN_PARTICLE_RADIUS)
        with m.unlocked():
            m.MIN_PARTICLE_RADIUS = 0.0
        self._settings = m
        from omnigibson.systems.macro_particle_system import MacroPhysicalParticleSystem
        from ..macro_particle_sampling import thin_macro_sampler
        self._sampler_class = MacroPhysicalParticleSystem
        self._original_sampler = MacroPhysicalParticleSystem.generate_particles_from_link
        MacroPhysicalParticleSystem.generate_particles_from_link = thin_macro_sampler(
            self._original_sampler, self._conversions.append)
        self.record.update(enabled=True, policy='authored_macro_particle_size_with_thin_sampling',
                           original_minimum_radius_m=self._original,
                           configured_minimum_radius_m=0.0,
                           native_particle_sampler_changed=True,
                           thin_source_sampling='single center layer on subdiameter axes, native spacing and mesh volume filter',
                           task_predicates_changed=False, asset_files_changed=False,
                           validation_reference='long_horizon_full_v2_20261022/R313,R317')

    def describe(self):
        return dict(self.record, thin_source_conversions=list(self._conversions))

    def close(self):
        if self._sampler_class is not None:
            self._sampler_class.generate_particles_from_link = self._original_sampler
            self._sampler_class = None
        if self._settings is not None:
            with self._settings.unlocked():
                self._settings.MIN_PARTICLE_RADIUS = self._original
            self._settings = None
