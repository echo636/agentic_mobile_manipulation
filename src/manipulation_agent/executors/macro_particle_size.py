"""Preserve authored macro particle geometry in the pinned catalog runtime."""
from importlib import metadata


class MacroParticleSizePolicy:
    """Disable the minimum-radius enlargement, retaining the native sampler.

    OG 3.9.2 enlarges macro templates to a 1 cm bounding-sphere radius. That
    makes a valid thin green-onion source too small for native DicingRule's
    2r grid. Preserve the authored template instead of changing task objects,
    transition semantics, or evaluation. This changes a simulator heuristic;
    record it separately from the native-default configuration.
    """

    def __init__(self):
        self._settings = None
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
        self.record.update(enabled=True, policy='authored_macro_particle_size',
                           original_minimum_radius_m=self._original,
                           configured_minimum_radius_m=0.0,
                           native_particle_sampler_changed=False,
                           task_predicates_changed=False, asset_files_changed=False,
                           validation_reference='long_horizon_full_v2_20261022/R313,R317')

    def describe(self):
        return dict(self.record)

    def close(self):
        if self._settings is not None:
            with self._settings.unlocked():
                self._settings.MIN_PARTICLE_RADIUS = self._original
            self._settings = None
