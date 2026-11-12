"""Resolve native material names from the declared ability metadata."""
from copy import deepcopy
from functools import wraps


def dicing_system_name(obj):
    from omnigibson.object_states import Cooked
    from omnigibson.utils.bddl_utils import get_system_name_by_synset

    cooked = Cooked in obj.states and obj.states[Cooked].get_value()
    derivative = obj._abilities['diceable'][
        ('cooked' if cooked else 'uncooked') + '_diceable_derivative_synset']
    return get_system_name_by_synset(derivative)


def dicing_transition(rule, object_candidates):
    from omnigibson.transition_rules import TransitionResults

    removed = []
    for obj in object_candidates['diceable']:
        system = rule.scene.get_system(dicing_system_name(obj))
        system.generate_particles_from_link(
            obj, obj.root_link, check_contact=False, use_visual_meshes=False)
        removed.append(obj)
    return TransitionResults(add=[], remove=removed)


def install_dicing_derivative_names():
    from omnigibson.transition_rules import DicingRule

    # OG 3.9.2 concatenates the object category, but e.g. half_garlic_clove
    # declares diced__clove. Correct both explicit cuts and contact-triggered
    # transitions; retain native conditions, particle generation and removal.
    DicingRule.transition = dicing_transition
    return {'status': 'configured', 'scope': 'native_dicing_output_material_name',
            'source': 'diceable_derivative_synset', 'transition_conditions': 'unchanged'}


def install_default_saturation_names():
    from omnigibson.object_states.particle_modifier import ParticleModifier
    from omnigibson.utils.bddl_utils import get_system_name_by_synset
    from omnigibson.utils.constants import ParticleModifyCondition

    original = ParticleModifier.postprocess_ability_params.__func__
    if not getattr(original, '_mas_default_saturation_names', False):
        @wraps(original)
        def normalized(cls, params, scene):
            # Native processing mutates nested explicit conditions. Defaults
            # need the same name conversion without altering shared abilities.
            result = original(cls, deepcopy(params), scene)
            for field in ('default_fluid_conditions', 'default_non_fluid_conditions',
                          'default_visual_conditions'):
                conditions = result.get(field)
                if conditions is None:
                    continue
                result[field] = [
                    [kind, get_system_name_by_synset(value)]
                    if kind == ParticleModifyCondition.SATURATED and value not in scene.available_systems
                    else [kind, value] for kind, value in conditions]
            return result

        normalized._mas_default_saturation_names = True
        ParticleModifier.postprocess_ability_params = classmethod(normalized)
    return {'status': 'configured', 'scope': 'native_particle_modifier_default_saturation_names',
            'logical_conditions': 'unchanged'}
