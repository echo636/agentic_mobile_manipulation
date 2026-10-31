"""Contract checks for RGB-selected tool actions without simulator imports."""
import sys
import types
import unittest
import json
import tempfile
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.material_actions import CheckedMaterialActions, eligible_systems
from manipulation_agent.executors.primitives import PRIMITIVES


class MaterialActionTests(unittest.TestCase):
    def setUp(self):
        self.Covered = type('Covered', (), {})
        self.ParticleRemover = type('ParticleRemover', (), {})
        self.ParticleApplier = type('ParticleApplier', (), {})
        self.Saturated = type('Saturated', (), {})
        self.ParticleSource = type('ParticleSource', (), {})
        self.Contains = type('Contains', (), {})
        self.Cooked = type('Cooked', (), {})
        states = types.ModuleType('omnigibson.object_states')
        for name in ('Covered', 'ParticleRemover', 'ParticleApplier', 'Saturated',
                     'ParticleSource', 'Contains', 'Cooked'):
            setattr(states, name, getattr(self, name))
        self.asset_folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.asset_folder.cleanup)
        self.asset_root = Path(self.asset_folder.name)
        asset_utils = types.ModuleType('omnigibson.utils.asset_utils')
        asset_utils.get_dataset_path = lambda name: str(self.asset_root)
        methods = types.SimpleNamespace(ADJACENCY=1, PROJECTION=2)
        constants = types.ModuleType('omnigibson.utils.constants')
        constants.ParticleModifyMethod = methods
        self.modules = patch.dict(sys.modules, {
            'omnigibson': types.ModuleType('omnigibson'),
            'omnigibson.object_states': states,
            'omnigibson.utils': types.ModuleType('omnigibson.utils'),
            'omnigibson.utils.constants': constants,
            'omnigibson.utils.asset_utils': asset_utils,
        })
        self.methods = methods
        self.system = types.SimpleNamespace(name='dust', states={})
        self.scene = types.SimpleNamespace(system_registry=types.SimpleNamespace(objects=[self.system]),
                                           get_system=lambda name, force_init=False: self.system)
        self.covered_value = [True]
        self.covered = types.SimpleNamespace(
            get_value=lambda system: self.covered_value[0],
            set_value=lambda system, value: self.covered_value.__setitem__(0, value) or True)
        self.target = types.SimpleNamespace(states={self.Covered: self.covered}, scene=self.scene)
        self.modifier = types.SimpleNamespace(method=self.methods.ADJACENCY,
            supports_system=lambda name: True, check_conditions_for_system=lambda name: True,
            conditions={'dust': []})
        self.tool = types.SimpleNamespace(category='broom', states={self.ParticleRemover: self.modifier})
        sim = types.SimpleNamespace(dump_state=lambda **kw: {}, load_state=lambda *a, **kw: None)
        self.backend = CheckedMaterialActions()
        self.backend.og = types.SimpleNamespace(sim=sim)
        self.backend.primitives = types.SimpleNamespace(_empty_action=lambda: [])
        self.backend._get_held = lambda: self.tool
        self.backend._anchored_operation = lambda target: nullcontext()
        self.backend._placement_record = lambda result: None
        self.backend._step = lambda action: None

    def test_all_requested_actions_are_in_public_catalog(self):
        self.assertTrue({'wipe', 'cut', 'soak', 'sweep', 'spray', 'spread', 'hang', 'vacuum'} <= set(PRIMITIVES))

    def test_mixed_pour_counts_objects_after_material_settling(self):
        from manipulation_agent.executors.material_transfer import CheckedMaterialTransfer

        inside = type('Inside', (), {})
        on_top = type('OnTop', (), {})
        volume = type('ContainedParticles', (), {})
        retained = [True, True]
        objects = [types.SimpleNamespace(name=str(i), states={inside: types.SimpleNamespace(
            get_value=lambda destination, i=i: retained[i])}) for i in range(2)]
        source = types.SimpleNamespace(name='source')
        destination = types.SimpleNamespace(name='receiver', states={volume: object()})
        remaining = []
        backend = CheckedMaterialTransfer()
        backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(
            object_registry=lambda key, name: next((obj for obj in objects if obj.name == name), None)))
        backend._transfer_preconditions = lambda *args: (source, destination)
        backend._pour_objects = lambda source: remaining or objects
        backend._pour_rigid_objects = lambda *args: objects
        backend._placement_record = lambda record: None

        def transfer_material(*args, **kwargs):
            self.assertTrue(kwargs['empty_ok'])
            # Later free physics dislodges one rigid object back to the source.
            retained[1] = False
            remaining.append(objects[1])
            return {'materials': [{'moved_particles': 3, 'retained_particles': 3}]}

        backend._transfer_material_portion = transfer_material
        with self.modules, patch.multiple(sys.modules['omnigibson.object_states'],
                Inside=inside, OnTop=on_top, ContainedParticles=volume, create=True):
            result = backend._checked_transfer('pour', destination, 6)
        self.assertEqual(result['rigid_transfer'], {
            'moved_objects': 2, 'retained_objects': 1, 'source_remaining_objects': 1,
            'identities_changed_during_settling': False})

    def test_pour_receipt_does_not_query_objects_replaced_by_native_transition(self):
        from unittest.mock import Mock
        from manipulation_agent.executors.material_transfer import CheckedMaterialTransfer

        states = {name: type(name, (), {}) for name in ('Inside', 'OnTop', 'ContainedParticles')}
        stale_contact = Mock(side_effect=KeyError('removed_object/base_link'))
        original = types.SimpleNamespace(name='food', states={states['OnTop']:
            types.SimpleNamespace(get_value=stale_contact)})
        replacement = types.SimpleNamespace(name='food')
        scene_objects = {'food': original}
        source = types.SimpleNamespace(name='source')
        receiver = types.SimpleNamespace(name='receiver', states={})
        backend = CheckedMaterialTransfer()
        backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(
            object_registry=lambda key, name: scene_objects.get(name)))
        backend._transfer_preconditions = lambda *args: (source, receiver)
        backend._pour_objects = lambda source: [original] if scene_objects['food'] is original else []
        backend._pour_rigid_objects = lambda *args: [original]
        backend._placement_record = lambda record: None

        def settle(*args, **kwargs):
            # A transition removes the original during free physics. Even a
            # new object with the same name must not revive its stale handle.
            scene_objects['food'] = replacement
            return {'materials': []}

        backend._transfer_material_portion = settle
        with self.modules, patch.multiple(sys.modules['omnigibson.object_states'], **states, create=True):
            result = backend._checked_transfer('pour', receiver, 6)
        stale_contact.assert_not_called()
        self.assertEqual(result['rigid_transfer'], {
            'moved_objects': 1, 'retained_objects': 0, 'source_remaining_objects': 0,
            'identities_changed_during_settling': True})

    def test_pour_accepts_surfaces_while_scoop_requires_a_vessel(self):
        from manipulation_agent.executors.material_transfer import CheckedMaterialTransfer

        states = {name: type(name, (), {}) for name in ('Inside', 'Open', 'ContainedParticles')}
        source = types.SimpleNamespace(states={})
        board = types.SimpleNamespace(states={})
        backend = CheckedMaterialTransfer()
        backend._get_held = lambda: source
        backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[]))
        backend._pour_objects = lambda obj: [object()]
        with self.modules, patch.multiple(sys.modules['omnigibson.object_states'], **states, create=True):
            self.assertEqual(backend._transfer_preconditions('pour', board), (source, board))
            with self.assertRaises(SkillError):
                backend._transfer_preconditions('scoop', board)
            backend._pour_objects = lambda obj: []
            self.assertEqual(backend._transfer_preconditions('pour', board), (source, board))

    def test_pour_counts_stacked_loose_items_without_emptying_nested_vessels(self):
        from manipulation_agent.executors.material_transfer import CheckedMaterialTransfer

        states = {name: type(name, (), {}) for name in
                  ('Inside', 'OnTop', 'AttachedTo', 'ContainedParticles')}
        source = types.SimpleNamespace(name='tray', states={})
        bottom = types.SimpleNamespace(name='bottom_piece', states={})
        top = types.SimpleNamespace(name='top_piece', states={states['OnTop']:
            types.SimpleNamespace(get_value=lambda obj: obj is bottom)})
        vessel = types.SimpleNamespace(name='nested_vessel', states={states['ContainedParticles']: object()})
        nested = types.SimpleNamespace(name='nested_contents', states={states['Inside']:
            types.SimpleNamespace(get_value=lambda obj: obj is vessel)})
        attached = types.SimpleNamespace(name='attached_part', states={states['AttachedTo']:
            types.SimpleNamespace(get_value=lambda obj: obj is vessel)})
        backend = CheckedMaterialTransfer()
        backend._grasp_dependencies = lambda obj: [(bottom, source, 'OnTop'),
            (top, bottom, 'OnTop'), (vessel, source, 'OnTop'),
            (nested, vessel, 'Inside'), (attached, vessel, 'AttachedTo')]
        with self.modules, patch.multiple(sys.modules['omnigibson.object_states'], **states, create=True):
            selected = backend._pour_objects(source)
        self.assertEqual([obj.name for obj in selected], ['bottom_piece', 'top_piece', 'nested_vessel'])

    def test_rigid_pour_commits_once_and_keeps_failed_physical_result(self):
        import numpy as np
        from unittest.mock import Mock
        from manipulation_agent.executors.material_transfer import CheckedMaterialTransfer

        states = {name: type(name, (), {}) for name in ('Inside', 'OnTop', 'ContainedParticles')}
        import itertools
        for relation, retained in itertools.product(('OnTop', 'Inside'), (False, True)):
            with self.subTest(relation=relation, retained=retained):
                receiver = types.SimpleNamespace(name='receiver', states=(
                    {states['ContainedParticles']: object()} if relation == 'Inside' else {}),
                    aabb=(np.zeros(3), np.ones(3)))
                objects = [types.SimpleNamespace(name=str(i), states={states[relation]:
                    types.SimpleNamespace(set_value=Mock(side_effect=AssertionError('cuboid sampler')),
                                          get_value=lambda target: retained)},
                    set_position_orientation=Mock(), keep_still=Mock()) for i in range(2)]
                backend = CheckedMaterialTransfer()
                backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(
                    object_registry=lambda key, name: next(obj for obj in objects if obj.name == name)))
                backend.og = types.SimpleNamespace(sim=types.SimpleNamespace(
                    get_physics_dt=lambda: 1/120, get_sim_step_dt=lambda: 1/30,
                    load_state=Mock(side_effect=AssertionError('scene rewind'))))
                backend.deadline = types.SimpleNamespace(check=lambda **kw: None)
                backend._grasp_dependencies = lambda obj: []
                selected_point = np.array([.8, .2, .3])
                # A clear patch on a lower shelf; the box center is not a
                # usable receiving surface (for example a countertop sink).
                backend._surface_candidates = Mock(side_effect=lambda obj, target, point:
                    [dict(index=2, pose=(np.zeros(3), np.ones(4)))]
                    if np.array_equal(point, selected_point) else [])
                backend._volume_pour_pose = lambda *args: (np.zeros(3), np.ones(4))
                backend._relocate_contents = Mock()
                backend._placement_physics_step = Mock()
                backend._placement_record = Mock()
                backend.primitives = types.SimpleNamespace(_empty_action=lambda: [])
                backend._step = Mock()
                with self.modules, patch.multiple(sys.modules['omnigibson.object_states'], **states, create=True), \
                        patch.object(sys.modules['omnigibson.utils'], 'transform_utils', object(), create=True), \
                        patch('manipulation_agent.executors.material_transfer.snapshot_particles', return_value=[]), \
                        patch('manipulation_agent.executors.particle_payload.relocate_particles'):
                    moved = backend._pour_rigid_objects(object(), receiver, objects, 6, selected_point)
                self.assertEqual(moved, objects if retained else objects[:1])
                for obj in moved:
                    obj.set_position_orientation.assert_called_once()
                if not retained:
                    objects[1].set_position_orientation.assert_not_called()
                backend._placement_physics_step.assert_not_called()
                backend._step.assert_called()
                np.testing.assert_array_equal(selected_point, [.8, .2, .3])
                if relation == 'OnTop':
                    self.assertEqual(backend._surface_candidates.call_count, len(moved))
                else:
                    backend._surface_candidates.assert_not_called()
                key = 'official_on_top' if relation == 'OnTop' else 'official_inside'
                self.assertEqual(backend._placement_record.call_args.args[0][key], retained)

    def test_surface_pour_moves_existing_portion_and_measures_contact_after_settling(self):
        import numpy as np
        from unittest.mock import Mock
        from manipulation_agent.executors.material_transfer import CheckedMaterialTransfer

        volume, contact, modified = (type(name, (), {}) for name in
                                     ('ContainedParticles', 'ContactParticles', 'ModifiedParticles'))
        saturated = types.ModuleType('omnigibson.object_states.saturated')
        saturated.ModifiedParticles = modified
        positions = np.zeros((3, 3))
        system = types.SimpleNamespace(name='loose_food', n_particles=3,
            generate_particles=Mock(), get_particles_position_orientation=lambda: (positions, None))
        view = types.SimpleNamespace(prim_paths=['a', 'b', 'c'])
        source = types.SimpleNamespace(name='source', states={})
        contacts = set()
        destination = types.SimpleNamespace(name='surface', states={contact:
            types.SimpleNamespace(get_value=lambda system: contacts)})
        payload = types.SimpleNamespace(container=source, system=system, paths=('a', 'b', 'c'))
        backend = CheckedMaterialTransfer()
        backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(active_systems={system.name: system}))
        backend._grasp_dependencies = lambda obj: []
        backend._get_held = lambda: source
        backend.primitives = types.SimpleNamespace(_empty_action=lambda: [])
        backend._step = lambda action: contacts.add(1)
        backend._placement_record = lambda record: None

        with self.modules, patch.multiple(sys.modules['omnigibson.object_states'],
                ContainedParticles=volume, ContactParticles=contact, create=True), \
                patch.dict(sys.modules, {'omnigibson.object_states.saturated': saturated}), \
                patch('manipulation_agent.executors.material_transfer.snapshot_particles', return_value=[payload]), \
                patch('manipulation_agent.executors.material_transfer._indices', return_value=[0, 1, 2]), \
                patch('manipulation_agent.executors.material_transfer.physical_particle_view', return_value=view), \
                patch('manipulation_agent.executors.material_transfer.free_surface_positions',
                      return_value=np.ones((2, 3))), \
                patch('manipulation_agent.executors.material_transfer.move_particle_centers') as move:
            result = backend._transfer_material_portion('pour', source, destination, 6)
        self.assertEqual(move.call_args.args[1], [0, 1])
        system.generate_particles.assert_not_called()
        self.assertEqual(view.prim_paths, ['a', 'b', 'c'])
        receipt = result['materials'][0]
        self.assertEqual((receipt['moved_particles'], receipt['retained_particles'],
                          receipt['source_remaining_particles']), (2, 1, 1))
        self.assertEqual(receipt['retained_relation'], 'surface_contact')
        self.assertTrue(result['source_has_more_material'])

    def test_sweep_removes_only_compatible_selected_material(self):
        from manipulation_agent.observations.boundary import public_execution_error
        with self.modules:
            result = self.backend._checked_surface_action('sweep', self.target, 6)
        self.assertFalse(self.covered_value[0])
        self.assertEqual(result['material_systems'], ['dust'])
        self.assertEqual(result['postcondition'], 'Covered.get_value_after_settling')
        with self.modules, self.assertRaises(SkillError) as failure:
            self.backend._checked_surface_action('sweep', self.target, 6)
        public = public_execution_error(failure.exception)
        self.assertEqual(public['code'], 'no_removable_material')
        self.assertIn('may already be clean', public['message'])

    def test_sweep_rejects_wrong_tool_before_state_change(self):
        self.tool.category = 'cloth'
        with self.modules, self.assertRaises(SkillError) as failure:
            self.backend._checked_surface_action('sweep', self.target, 6)
        self.assertEqual(failure.exception.code, 'pre_condition_error')
        self.assertTrue(self.covered_value[0])

    def test_spray_requires_projection_applicator_and_applies_material(self):
        self.tool.category = 'atomizer'
        self.tool.states = {self.ParticleApplier: self.modifier}
        with self.modules, self.assertRaises(SkillError):
            self.backend._checked_surface_action('spray', self.target, 6)
        self.modifier.method = self.methods.PROJECTION
        self.covered_value[0] = False
        with self.modules:
            result = self.backend._checked_surface_action('spray', self.target, 6)
        self.assertTrue(self.covered_value[0])
        self.assertEqual(result['material_systems'], ['dust'])

    def test_ideal_spray_skips_only_physical_overlap_clause(self):
        modifier = types.SimpleNamespace(obj=object(), requires_overlap=True,
            supports_system=lambda name: True,
            check_conditions_for_system=lambda name: False,
            conditions={'dust': [lambda obj: True, lambda obj: True, lambda obj: False]})
        self.assertEqual(eligible_systems([self.system], modifier, ideal_projection=True), [self.system])
        modifier.conditions['dust'][0] = lambda obj: False
        self.assertEqual(eligible_systems([self.system], modifier, ideal_projection=True), [])

    def test_soak_saturates_only_from_active_compatible_source(self):
        self.tool.states[self.Saturated] = types.SimpleNamespace(
            set_value=lambda system, value: setattr(self, 'saturated', value) or True,
            get_value=lambda system: getattr(self, 'saturated', False))
        self.target.states[self.ParticleSource] = types.SimpleNamespace(
            check_conditions_for_system=lambda name: True)
        with self.modules:
            result = self.backend._checked_soak(self.target, 6)
        self.assertTrue(self.saturated)
        self.assertEqual(result['material_systems'], ['dust'])

    def test_spread_requires_material_already_held_by_tool(self):
        self.covered_value[0] = False
        self.tool.states = {self.Saturated: types.SimpleNamespace(get_value=lambda system: False)}
        with self.modules, self.assertRaises(SkillError) as failure:
            self.backend._checked_surface_action('spread', self.target, 6)
        self.assertEqual(failure.exception.code, 'pre_condition_error')
        self.tool.states[self.Saturated] = types.SimpleNamespace(get_value=lambda system: True)
        with self.modules:
            result = self.backend._checked_surface_action('spread', self.target, 6)
        self.assertTrue(self.covered_value[0])
        self.assertEqual(result['material_systems'], ['dust'])

    def test_cut_uses_official_transition_and_checks_replacement(self):
        class SlicingRule:
            def transition(self, candidates):
                self.assertion(candidates)
                return types.SimpleNamespace(add=[object()], remove=[self.target])
        rule = SlicingRule()
        rule.target = self.target
        rule.assertion = lambda candidates: self.assertEqual(candidates, {'sliceable': [self.target]})
        transitions = types.ModuleType('omnigibson.transition_rules')
        transitions.SlicingRule = SlicingRule
        transitions.DicingRule = type('DicingRule', (), {})
        self.tool._abilities = {'slicer'}
        self.target._abilities = {'sliceable'}
        self.target.metadata = {'object_parts': {'0': {'category': 'half_vegetable', 'model': 'part'}}}
        usd = self.asset_root/'objects/half_vegetable/part/usd/part.encrypted.usd'
        usd.parent.mkdir(parents=True); usd.write_bytes(b'fixture')
        self.scene.objects = [self.target]
        self.scene.transition_rule_api = types.SimpleNamespace(
            active_rules=[rule],
            execute_transition=lambda added_obj_attrs, removed_objs:
                self.scene.objects.remove(self.target))
        self.backend.frames_revision = 0
        with self.modules, patch.dict(sys.modules, {'omnigibson.transition_rules': transitions}):
            result = self.backend._checked_cut(self.target, 6)
        self.assertEqual(result['transition'], 'SlicingRule')
        self.assertEqual(self.backend.frames_revision, -1)
        self.assertNotIn(self.target, self.scene.objects)

    def test_missing_slice_part_rejects_before_transition_and_preserves_original(self):
        class SlicingRule:
            def transition(rule, candidates):
                self.fail('Missing asset must be detected before transition')
        transitions = types.ModuleType('omnigibson.transition_rules')
        transitions.SlicingRule = SlicingRule
        transitions.DicingRule = type('DicingRule', (), {})
        self.tool._abilities = {'slicer'}
        self.target._abilities = {'sliceable'}
        self.target.metadata = {'object_parts': {'0': {'category': 'half_onion', 'model': 'first'},
                                                '1': {'category': 'half_onion', 'model': 'missing_second'}}}
        usd = self.asset_root/'objects/half_onion/first/usd/first.encrypted.usd'
        usd.parent.mkdir(parents=True); usd.write_bytes(b'fixture')
        self.scene.objects = [self.target]
        self.scene.transition_rule_api = types.SimpleNamespace(active_rules=[SlicingRule()],
            execute_transition=lambda **kw: self.fail('Must not remove original object'))
        with self.modules, patch.dict(sys.modules, {'omnigibson.transition_rules': transitions}), \
                self.assertRaises(SkillError) as failure:
            self.backend._checked_cut(self.target, 6)
        self.assertEqual(failure.exception.code, 'cut_asset_unavailable')
        self.assertFalse(failure.exception.changed)
        self.assertIn(self.target, self.scene.objects)

    def test_dicing_checks_only_current_cooked_output_before_particle_generation(self):
        class DicingRule:
            def transition(rule, candidates):
                raise AssertionError('Do not guess the particle system from the object category')
        transitions = types.ModuleType('omnigibson.transition_rules')
        transitions.DicingRule = DicingRule
        transitions.SlicingRule = type('SlicingRule', (), {})
        transitions.TransitionResults = types.SimpleNamespace
        bddl_utils = types.ModuleType('omnigibson.utils.bddl_utils')
        bddl_utils.get_system_name_by_synset = lambda synset: {'cooked__diced__clove.n.01': 'cooked__diced__clove'}[synset]
        self.tool._abilities = {'slicer'}
        self.target._abilities = {'diceable': {'cooked_diceable_derivative_synset': 'cooked__diced__clove.n.01'}}
        self.target.category = 'half_garlic_clove'
        self.target.root_link = object()
        self.target.states[self.Cooked] = types.SimpleNamespace(get_value=lambda: True)
        self.scene.objects = [self.target]
        self.generated = False
        def generate(target, link, **options):
            self.assertIs(target, self.target)
            self.assertIs(link, self.target.root_link)
            self.generated = True
        self.system.generate_particles_from_link = generate
        def system_by_name(name):
            self.assertEqual(name, 'cooked__diced__clove')
            return self.system
        self.scene.get_system = system_by_name
        self.scene.transition_rule_api = types.SimpleNamespace(active_rules=[DicingRule()],
            execute_transition=lambda **kw: self.scene.objects.remove(self.target))
        system = self.asset_root/'systems/cooked__diced__clove'
        with self.modules, patch.dict(sys.modules, {'omnigibson.transition_rules': transitions,
                                                   'omnigibson.utils.bddl_utils': bddl_utils}):
            with self.assertRaises(SkillError) as missing_system:
                self.backend._checked_cut(self.target, 6)
            self.assertFalse(missing_system.exception.changed)
            self.assertFalse(self.generated)
            self.assertIn(self.target, self.scene.objects)
            system.mkdir(parents=True)
            (system/'metadata.json').write_text(json.dumps({'type':'granular'}))
            (system/'part/usd').mkdir(parents=True)
            with self.assertRaises(SkillError) as failure:
                self.backend._checked_cut(self.target, 6)
            self.assertFalse(failure.exception.changed)
            self.assertFalse(self.generated)
            self.assertIn(self.target, self.scene.objects)
            (system/'part/usd/part.encrypted.usd').write_bytes(b'fixture')
            result = self.backend._checked_cut(self.target, 6)
        self.assertTrue(self.generated)
        self.assertEqual(result['transition'], 'DicingRule')
        self.assertNotIn(self.target, self.scene.objects)


if __name__ == '__main__':
    unittest.main()
