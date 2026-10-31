"""Dicing compatibility must not alter bulk fill geometry or leak settings."""
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from manipulation_agent.executors.compat.macro_particle_size import MacroParticleSizePolicy


class MacroParticleSizePolicyTests(unittest.TestCase):
    def test_dicing_scope_and_native_policy_restoration(self):
        settings = SimpleNamespace(MIN_PARTICLE_RADIUS=.01, unlocked=nullcontext)

        class Physical:
            def __init__(self, name, radius, fail=False):
                self.name, self.authored_radius, self.fail = name, radius, fail

            def process_particle_object(self):
                if self.fail:
                    raise RuntimeError('native template failed')
                self.particle_radius = max(self.authored_radius, settings.MIN_PARTICLE_RADIUS)

            def generate_particles_from_link(self, *args, **kwargs):
                pass

        modules = {name: ModuleType(name) for name in (
            'omnigibson', 'omnigibson.systems', 'omnigibson.systems.macro_particle_system')}
        modules['omnigibson.systems.macro_particle_system'].__dict__.update(
            m=settings, MacroPhysicalParticleSystem=Physical)
        original_process, original_sampler = Physical.process_particle_object, Physical.generate_particles_from_link
        versions = {'omnigibson': '3.9.2', 'isaacsim': '5.1.0.0'}
        with patch.dict('sys.modules', modules), patch(
                'manipulation_agent.executors.compat.macro_particle_size.metadata.version',
                side_effect=versions.__getitem__):
            policy = MacroParticleSizePolicy()
            try:
                for name, radius, expected in [('grated_cheese', .0015, .01),
                        ('diced__green_onion', .00615, .00615),
                        ('cooked__diced__green_onion', .00615, .00615)]:
                    system = Physical(name, radius)
                    system.process_particle_object()
                    self.assertEqual(system.particle_radius, expected)
                    self.assertEqual(settings.MIN_PARTICLE_RADIUS, .01)
                with self.assertRaisesRegex(RuntimeError, 'native template failed'):
                    Physical('diced__green_onion', .00615, fail=True).process_particle_object()
                self.assertEqual(settings.MIN_PARTICLE_RADIUS, .01)
            finally:
                policy.close()
            self.assertIs(Physical.process_particle_object, original_process)
            self.assertIs(Physical.generate_particles_from_link, original_sampler)
