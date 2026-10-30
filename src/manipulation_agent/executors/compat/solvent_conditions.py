"""Use native washer solvent alternatives in tools (yujie PR #6, bbe9b5f)."""
from functools import wraps


def install_native_solvent_alternatives():
    from omnigibson.object_states.particle_modifier import ParticleRemover
    from omnigibson.utils.bddl_utils import get_knowledge_base, translate_bddl_washer_rule_to_og_washer_rule
    from omnigibson.utils.constants import ParticleModifyCondition

    original = ParticleRemover._parse_conditions
    if not getattr(original, '_mas_solvent_alternatives', False):
        # Object abilities are parsed before WasherRule's scene registration.
        reference = translate_bddl_washer_rule_to_og_washer_rule(get_knowledge_base().washer_rule)

        @wraps(original)
        def parse(modifier, conditions):
            repaired = dict(conditions)
            for material, clauses in conditions.items():
                solvents = reference.get(material)
                if (not solvents or not clauses or len(clauses) < 2
                        or any(kind != ParticleModifyCondition.SATURATED for kind, _ in clauses)
                        or {value for _, value in clauses} != set(solvents)):
                    continue
                functions = tuple(modifier._generate_condition(kind, value) for kind, value in clauses)
                repaired[material] = [(ParticleModifyCondition.FUNCTION,
                                       lambda obj, functions=functions: any(fn(obj) for fn in functions))]
            # Retain native capacity limits and every nonmatching condition.
            return original(modifier, repaired)

        parse._mas_solvent_alternatives = True
        ParticleRemover._parse_conditions = parse
    return {'status': 'configured', 'scope': 'saturation_only_clauses_matching_native_washer_alternatives',
            'semantics': 'any_registered_solvent', 'capacity_and_other_requirements': 'unchanged'}
