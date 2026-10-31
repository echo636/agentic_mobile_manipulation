"""Normalize native default saturation conditions at the ability boundary."""
from copy import deepcopy
from pathlib import Path
import json

PROTOCOL = 'native_default_saturation_system_names_v1'


def install(record_path):
    from omnigibson.object_states.particle_modifier import ParticleModifier
    from omnigibson.utils.bddl_utils import get_system_name_by_synset
    from omnigibson.utils.constants import ParticleModifyCondition

    original = ParticleModifier.postprocess_ability_params.__func__
    if getattr(original, '_manipulation_default_saturation_names', False):
        raise RuntimeError('Default saturation condition normalization already installed')
    path = Path(record_path)
    records = []
    def save():
        path.write_text(json.dumps({'protocol': PROTOCOL, 'normalized': records}, indent=2) + '\n')
    save()
    def normalized(cls, params, scene):
        result = original(cls, deepcopy(params), scene)
        for field in ('default_fluid_conditions', 'default_non_fluid_conditions', 'default_visual_conditions'):
            conditions = result.get(field)
            if conditions is None:
                continue
            for index, condition in enumerate(conditions):
                kind, value = condition
                if kind != ParticleModifyCondition.SATURATED or value in scene.available_systems:
                    continue
                canonical = get_system_name_by_synset(value)
                if canonical not in scene.available_systems:
                    raise ValueError('Default saturation system unavailable: ' + canonical)
                conditions[index] = [kind, canonical]
                records.append({'state': cls.__name__, 'field': field,
                                'original_system': value, 'canonical_system': canonical,
                                'logical_condition_unchanged': True})
        save()
        return result
    normalized._manipulation_default_saturation_names = True
    ParticleModifier.postprocess_ability_params = classmethod(normalized)
    return records
