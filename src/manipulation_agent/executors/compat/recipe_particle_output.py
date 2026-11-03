"""Use native surface generation for recipe fluids in subdiameter trays."""
from functools import wraps

from ...contracts import SkillError


class RecipeParticleOutputPolicy:
    def __init__(self):
        from omnigibson.transition_rules import RecipeRule
        from omnigibson.systems.micro_particle_system import MicroPhysicalParticleSystem
        self.conversions = []
        self.rule_class = RecipeRule
        self.original = RecipeRule._execute_recipe

        @wraps(self.original)
        def execute(rule, container, recipe, container_info):
            outputs = recipe['output_systems']
            if len(outputs) != 1:
                return self.original(rule, container, recipe, container_info)
            system = rule.scene.get_system(outputs[0])
            if not isinstance(system, MicroPhysicalParticleSystem):
                return self.original(rule, container, recipe, container_info)
            original = system.generate_particles_from_link
            prior = vars(system).get('generate_particles_from_link')

            def generate(obj, link, **kwargs):
                count = kwargs.get('max_samples')
                if count == 0:
                    return None
                extent = link.visual_aabb_extent
                if bool((extent >= 2 * system.particle_radius).all()):
                    return original(obj=obj, link=link, **kwargs)
                before = system.n_particles
                # Keep the native material and max_samples budget. As with
                # volume sampling, the surface may yield fewer particles.
                # A shallow receiver cannot contain an inscribed fluid sphere;
                # release onto its physical surface and let native physics act.
                placed = system.generate_particles_on_object(obj,
                    max_samples=count, min_samples_for_success=1)
                self.conversions.append(dict(container=obj.name, system=system.name,
                    extent_m=extent.tolist(), radius_m=float(system.particle_radius),
                    requested=count, generated=system.n_particles-before,
                    method='native_surface_release', native_return=bool(placed)))
                if not placed:
                    raise SkillError('sampling_error', 'No physical surface for the native recipe output', changed=True)
                return None

            system.generate_particles_from_link = generate
            try:
                return self.original(rule, container, recipe, container_info)
            finally:
                if prior is None:
                    del system.generate_particles_from_link
                else:
                    system.generate_particles_from_link = prior

        RecipeRule._execute_recipe = execute

    def describe(self):
        return dict(policy='native_surface_output_for_subdiameter_recipe_volume',
            recipes_changed=False, particle_sizes_changed=False,
            task_predicates_changed=False, conversions=list(self.conversions))

    def close(self):
        self.rule_class._execute_recipe = self.original
