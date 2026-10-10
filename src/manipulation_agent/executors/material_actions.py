"""Checked ideal tool actions on the object selected from the latest RGB pixel.

The simulator's particle and transition states remain the source of truth.  No
task objects, goal predicates, or alternative visual targets are consulted.
"""
from ..contracts import SkillError
from ..asset_preflight import inspect_model_assets, inspect_particle_system_assets, object_part_models


def eligible_systems(systems, modifier, *, covered=None, ideal_projection=False):
    """Return only currently usable material systems, in deterministic order."""
    accepted = []
    for system in sorted(systems, key=lambda item: item.name):
        if not modifier.supports_system(system.name):
            continue
        if ideal_projection and getattr(modifier, 'requires_overlap', False):
            # Upstream ParticleApplier appends its physical projection-overlap
            # test last. The ideal actuator routes the model's selected target
            # directly, while all tool-state and capacity clauses still apply.
            ready = all(condition(modifier.obj) for condition in modifier.conditions[system.name][:-1])
        else:
            ready = modifier.check_conditions_for_system(system.name)
        if ready and (covered is None or covered.get_value(system)):
            accepted.append(system)
    return accepted


class CheckedMaterialActions:
    def _checked_surface_action(self, primitive, target, max_steps):
        from omnigibson.object_states import Covered, ParticleApplier, ParticleRemover, Saturated
        from omnigibson.utils.constants import ParticleModifyMethod
        from .placement import placement_transaction

        tool = self._get_held()
        if tool is None:
            raise SkillError('empty_hand', 'A compatible tool must be carried first')
        if tool is target or Covered not in target.states:
            raise SkillError('unsupported_relation', 'Selected object has no coverable surface')
        removing = primitive in {'wipe', 'sweep', 'vacuum'}
        spreading = primitive == 'spread'
        state_type = Saturated if spreading else ParticleRemover if removing else ParticleApplier
        if state_type not in tool.states:
            raise SkillError('pre_condition_error', 'Carried object is not a compatible material tool')
        modifier = tool.states[state_type]
        if not spreading:
            method = (ParticleModifyMethod.PROJECTION if primitive in {'vacuum', 'spray'}
                      else ParticleModifyMethod.ADJACENCY)
            if modifier.method != method:
                raise SkillError('pre_condition_error', 'Carried tool uses a different material interaction method')
        category = str(tool.category).lower()
        if primitive == 'wipe' and any(word in category for word in ('broom', 'sweeper', 'vacuum')):
            raise SkillError('pre_condition_error', 'Wiping requires a contact cleaning tool')
        if primitive == 'sweep' and 'broom' not in category:
            raise SkillError('pre_condition_error', 'Sweeping requires a broom')
        if primitive == 'vacuum' and 'vacuum' not in category:
            raise SkillError('pre_condition_error', 'Vacuuming requires a vacuum')
        if primitive == 'spray' and not any(word in category for word in ('atomizer', 'spray')):
            raise SkillError('pre_condition_error', 'Spraying requires a sprayer')
        covered = target.states[Covered]
        scene = target.scene
        if spreading:
            systems = list(scene.system_registry.objects)
            candidates = [system for system in sorted(systems, key=lambda item: item.name)
                          if modifier.get_value(system)]
        elif removing:
            systems = [system for system in scene.system_registry.objects if covered.get_value(system)]
            candidates = eligible_systems(systems, modifier, covered=covered)
        else:
            systems = [scene.get_system(name, force_init=False) for name in modifier.conditions]
            systems = [system for system in systems if system is not None]
            candidates = eligible_systems(systems, modifier, ideal_projection=primitive == 'spray')
        if not candidates:
            raise SkillError('pre_condition_error', 'No material on the selected surface matches the ready tool')

        # The selected object is fixed in place while the official Covered
        # setter changes only material belonging to that object.
        with self._anchored_operation(target), placement_transaction(self.og.sim, self._placement_record):
            for system in candidates:
                if not covered.set_value(system, not removing):
                    raise SkillError('execution_error', 'Material state setter rejected the action', changed=True)
            for _ in range(min(6, max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if any(bool(covered.get_value(system)) == removing for system in candidates):
                raise SkillError('postcondition_error', 'Material state did not persist after settling', changed=True)
        return {'primitive': primitive, 'implementation': 'checked_official_Covered_state',
                'material_systems': [system.name for system in candidates],
                'postcondition': 'Covered.get_value_after_settling'}

    def _checked_soak(self, target, max_steps):
        from omnigibson.object_states import Contains, ParticleRemover, ParticleSource, Saturated
        from .placement import placement_transaction

        tool = self._get_held()
        if tool is None:
            raise SkillError('empty_hand', 'A soakable tool must be carried first')
        if Saturated not in tool.states or ParticleRemover not in tool.states:
            raise SkillError('pre_condition_error', 'Carried object cannot absorb the selected material')
        scene = target.scene
        systems = []
        if ParticleSource in target.states:
            source = target.states[ParticleSource]
            systems.extend(system for system in scene.system_registry.objects
                           if source.check_conditions_for_system(system.name))
        if Contains in target.states:
            systems.extend(system for system in scene.system_registry.objects
                           if target.states[Contains].get_value(system))
        candidates = eligible_systems({system.name: system for system in systems}.values(),
                                      tool.states[ParticleRemover])
        if not candidates:
            raise SkillError('pre_condition_error', 'Selected source has no material this tool can absorb')
        saturated = tool.states[Saturated]
        with self._anchored_operation(target), placement_transaction(self.og.sim, self._placement_record):
            for system in candidates:
                if not saturated.set_value(system, True):
                    raise SkillError('execution_error', 'Saturation state setter rejected the action', changed=True)
            for _ in range(min(6, max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            if any(not saturated.get_value(system) for system in candidates):
                raise SkillError('postcondition_error', 'Carried tool did not remain saturated', changed=True)
        return {'primitive': 'soak', 'implementation': 'checked_official_Saturated_state',
                'material_systems': [system.name for system in candidates],
                'postcondition': 'Saturated.get_value_after_settling'}

    def _checked_cut(self, target, max_steps):
        from omnigibson.transition_rules import DicingRule, SlicingRule
        from omnigibson.utils.asset_utils import get_dataset_path

        tool = self._get_held()
        if tool is None:
            raise SkillError('empty_hand', 'A cutting tool must be carried first')
        if 'slicer' not in tool._abilities:
            raise SkillError('pre_condition_error', 'Carried object is not a cutting tool')
        ability = 'sliceable' if 'sliceable' in target._abilities else 'diceable'
        if ability not in target._abilities:
            raise SkillError('pre_condition_error', 'Selected object cannot be cut')
        rule_type = SlicingRule if ability == 'sliceable' else DicingRule
        rule = next((item for item in target.scene.transition_rule_api.active_rules
                     if isinstance(item, rule_type)), None)
        if rule is None:
            raise SkillError('pre_condition_error', 'Simulator has no active cutting transition')
        # execute_transition removes originals before loading additions. Dicing's
        # transition itself initializes a system and generates particles. Check
        # every selected output before either call; do not catch and continue
        # after a partially applied transition.
        try:
            asset_root = get_dataset_path('behavior-1k-assets')
            if ability == 'sliceable':
                parts = object_part_models(target.metadata)
                if not parts:
                    raise ValueError('Sliceable object has no object_parts metadata')
                # Future cuts of a child are not dependencies of this cut.
                assets = inspect_model_assets(parts, asset_root, follow_object_parts=False)
                missing = assets['missing_model_usds']
                invalid = bool(assets['metadata_errors'])
            else:
                from omnigibson.object_states import Cooked
                system_name = 'diced__' + target.category.removeprefix('half_')
                if Cooked in target.states and target.states[Cooked].get_value():
                    system_name = 'cooked__' + system_name
                assets = inspect_particle_system_assets(system_name, asset_root)
                missing = assets['missing_assets']
                invalid = False
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SkillError('cut_asset_unavailable',
                'Required cutting asset metadata is unavailable; the object has not been changed.') from exc
        if missing or invalid:
            raise SkillError('cut_asset_unavailable',
                'A required cutting output asset is unavailable; the object has not been changed.')
        output = rule.transition({ability: [target]})
        if target not in output.remove or not (output.add or ability == 'diceable'):
            raise SkillError('execution_error', 'Cutting transition produced no valid result')
        target.scene.transition_rule_api.execute_transition(added_obj_attrs=output.add,
                                                            removed_objs=output.remove)
        self.frames_revision = -1
        for _ in range(min(6, max_steps)):
            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        if target in target.scene.objects:
            raise SkillError('postcondition_error', 'Original object remained after cutting', changed=True)
        return {'primitive': 'cut', 'implementation': 'official_slicing_or_dicing_transition',
                'transition': rule_type.__name__, 'created_objects': len(output.add),
                'postcondition': 'original_object_replaced'}
