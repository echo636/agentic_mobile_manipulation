"""Load official substance melting mappings through native particle transitions."""


def install_particle_melting(scene):
    import operator

    from omnigibson.object_states import Temperature
    from omnigibson.transition_rules import CookingPhysicalParticleRule, StateCondition, m
    from omnigibson.utils.bddl_utils import get_knowledge_base, get_system_name_by_synset
    from omnigibson.utils.python_utils import classproperty

    class ParticleMeltingRule(CookingPhysicalParticleRule):
        @classproperty
        def _do_not_register_classes(cls):
            # This instance belongs only to this catalog scene, not the global registry.
            return super()._do_not_register_classes | {cls.__name__}

        def _generate_conditions(self):
            # Particles inherit their container's current temperature, as in native
            # particle cooking. A historically hot, now cold pot must not melt new inputs.
            return [StateCondition(filter_name='container', state=Temperature,
                                   val=m.MELTING_TEMPERATURE, op=operator.ge)]

    api = scene._transition_rule_api
    existing = {name for rule in api.all_rules for name in getattr(rule, '_recipes', {})}
    mappings = []
    for transition in get_knowledge_base().all_transition_rules():
        if not transition.name.endswith('-melting') or transition.name in existing:
            continue
        inputs, outputs = list(transition.input_synsets), list(transition.output_synsets)
        if len(inputs) != 1 or len(outputs) != 1:
            continue
        if not inputs[0].is_substance or not outputs[0].is_substance:
            continue
        source = get_system_name_by_synset(inputs[0].name)
        destination = get_system_name_by_synset(outputs[0].name)
        ParticleMeltingRule.add_recipe(name=transition.name, input_objects={}, output_objects={},
                                      input_systems=[source], output_systems=[destination])
        mappings.append(dict(name=transition.name, input_system=source, output_system=destination))
    if mappings:
        rule = ParticleMeltingRule(scene)
        api.all_rules.add(rule)
        api.refresh_rules((rule,))
    return dict(policy='official_particle_melting_in_heated_containers',
                melting_temperature_c=float(m.MELTING_TEMPERATURE), mappings=mappings,
                scope='native fillable/heatable containers and native ContainedParticles volume',
                conversion='native CookingPhysicalParticleRule particle replacement at input positions',
                task_predicates_changed=False, asset_files_changed=False,
                global_rule_registry_changed=False)
