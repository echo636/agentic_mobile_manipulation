"""Local articulation snapshot restoration; no task-success claim."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Pinned simulator Python required')
class ArticulationTests(unittest.TestCase):
    def test_preview_restores_carried_assembly_and_only_its_particle_system(self):
        import torch
        from manipulation_agent.executors.articulation_snapshot import preview_restore
        values={name:torch.tensor([float(index)]) for index,name in enumerate(('door','content','held','carried'))}
        originals={name:value.clone() for name,value in values.items()}
        objects={name:SimpleNamespace(
            dump_state=lambda name=name,**kw:values[name],
            load_state=lambda state,name=name,**kw:values[name].copy_(state)) for name in values}
        unrelated=SimpleNamespace(load_state=lambda *a,**kw:self.fail('Unrelated object was restored'))
        scene=SimpleNamespace(objects=[*objects.values(),unrelated])
        sim=SimpleNamespace(scenes=[scene])
        system=object();other=object()
        particle_states=[(scene,system,(),None,None),(scene,other,(),None,None)]
        backend=SimpleNamespace(og=SimpleNamespace(sim=sim),_get_held=lambda:objects['held'],
            _carry_contents=[(objects['carried'],None)],_carry_particles=[SimpleNamespace(system=system)])
        with patch('manipulation_agent.executors.particle_payload.snapshot_particle_state',return_value=particle_states),\
             patch('manipulation_agent.executors.particle_payload.particle_state_unchanged',return_value=True),\
             patch('manipulation_agent.executors.particle_payload.restore_particle_state') as restore_particles:
            restore=preview_restore(backend,objects['door'],[(objects['content'],None,None)],follows_carry=True)
            for value in values.values():value.add_(10)
            restore()
        for name,value in values.items():torch.testing.assert_close(value,originals[name])
        restore_particles.assert_called_once_with([particle_states[0]])


if __name__ == '__main__':
    unittest.main()
