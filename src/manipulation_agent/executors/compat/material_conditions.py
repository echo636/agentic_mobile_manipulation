"""Normalize native default saturation names (yujie PR #6, bbe9b5f)."""
from copy import deepcopy
from functools import wraps


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
