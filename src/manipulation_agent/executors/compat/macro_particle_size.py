"""Preserve authored dicing geometry without changing bulk particle systems."""
from importlib import metadata
from functools import wraps


class MacroParticleSizePolicy:
    """Preserve authored dicing size and let thin sources occupy one layer.

    OG 3.9.2 enlarges macro templates to a 1 cm bounding-sphere radius. That
    makes a valid thin green-onion source too small for native DicingRule's
    2r grid. Preserve the authored template instead of changing task objects,
    transition outputs, or evaluation. Native sphere erosion also rejects thin
    sources even with the authored size: use a single center layer on those
    axes. Bulk systems retain the native minimum size: Filled's 500-grain
    cap and bulk generation cost rely on that default. Record the scope.
    """

    def __init__(self):
        self._sampler_class = None
        self._conversions = []
        self._templates = []
        self.record = dict(enabled=False, policy='native',
                           omnigibson=metadata.version('omnigibson'),
                           isaacsim=metadata.version('isaacsim'))
        if (self.record['omnigibson'], self.record['isaacsim']) != ('3.9.2', '5.1.0.0'):
            return
        from omnigibson.systems.macro_particle_system import m
        from omnigibson.systems.macro_particle_system import MacroPhysicalParticleSystem
        self._original_process = MacroPhysicalParticleSystem.process_particle_object

        @wraps(self._original_process)
        def process(system):
            # "diced" is the native system's transformation tag, including
            # cooked__diced__... successors. No task bindings are consulted.
            if 'diced' not in system.name.split('__'):
                return self._original_process(system)
            minimum = m.MIN_PARTICLE_RADIUS
            with m.unlocked():
                m.MIN_PARTICLE_RADIUS = 0.0
            try:
                result = self._original_process(system)
                self._templates.append(dict(system=system.name,
                    native_minimum_radius_m=float(minimum),
                    authored_radius_m=float(system.particle_radius)))
                return result
            finally:
                with m.unlocked():
                    m.MIN_PARTICLE_RADIUS = minimum

        MacroPhysicalParticleSystem.process_particle_object = process
        from ..macro_particle_sampling import thin_macro_sampler
        self._sampler_class = MacroPhysicalParticleSystem
        self._original_sampler = MacroPhysicalParticleSystem.generate_particles_from_link
        MacroPhysicalParticleSystem.generate_particles_from_link = thin_macro_sampler(
            self._original_sampler, self._conversions.append)
        self.record.update(enabled=True, policy='authored_diced_macro_size_with_thin_sampling',
                           native_bulk_minimum_radius_m=float(m.MIN_PARTICLE_RADIUS),
                           authored_size_scope='native diced transformation tag only',
                           native_particle_sampler_changed=True,
                           thin_source_sampling='single center layer on subdiameter axes, native spacing and mesh volume filter',
                           native_filled_sampling_unchanged=True,
                           task_predicates_changed=False, asset_files_changed=False,
                           validation_reference='long_horizon_full_v2_20261022/R313,R317')

    def describe(self):
        return dict(self.record, thin_source_conversions=list(self._conversions),
                    authored_diced_templates=list(self._templates))

    def close(self):
        if self._sampler_class is not None:
            self._sampler_class.generate_particles_from_link = self._original_sampler
            self._sampler_class.process_particle_object = self._original_process
            self._sampler_class = None
