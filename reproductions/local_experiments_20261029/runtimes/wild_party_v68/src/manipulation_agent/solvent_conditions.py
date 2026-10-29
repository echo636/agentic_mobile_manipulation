"""Interpret native washer solvent alternatives consistently in cleaning tools."""
from pathlib import Path
import json

PROTOCOL = 'native_washer_alternative_solvents_v2'


def install(record_path):
    from omnigibson.object_states.particle_modifier import ParticleRemover
    from omnigibson.utils.bddl_utils import get_knowledge_base, translate_bddl_washer_rule_to_og_washer_rule
    from omnigibson.utils.constants import ParticleModifyCondition

    original = ParticleRemover._parse_conditions
    # Objects are parsed before the scene initializes WasherRule's class
    # registry. Translate the same authoritative native KB rule directly.
    reference = translate_bddl_washer_rule_to_og_washer_rule(get_knowledge_base().washer_rule)
    path = Path(record_path)
    records = []
    def save():
        path.write_text(json.dumps({'protocol': PROTOCOL, 'repairs': records}, indent=2) + '\n')
    save()
    def parse(modifier, conditions):
        repaired = dict(conditions)
        for material, clauses in conditions.items():
            solvents = reference.get(material)
            if (not solvents or not clauses or len(clauses) < 2 or
                    any(kind != ParticleModifyCondition.SATURATED for kind, _ in clauses) or
                    {value for _, value in clauses} != set(solvents)):
                continue
            functions = tuple(modifier._generate_condition(kind, value) for kind, value in clauses)
            repaired[material] = [(ParticleModifyCondition.FUNCTION,
                                   lambda obj, functions=functions: any(fn(obj) for fn in functions))]
            records.append({'object': modifier.obj.name, 'material': material,
                            'alternatives': list(solvents), 'reference': 'native_KB_washer_rule_translation',
                            'previous_semantics': 'all_solvents', 'semantics': 'any_registered_solvent'})
        save()
        # Native limit clauses and all nonmatching readiness requirements remain.
        return original(modifier, repaired)
    ParticleRemover._parse_conditions = parse
    return records
