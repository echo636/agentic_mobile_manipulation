"""Avoid empty recipe scans and use native surface output for shallow trays."""
from functools import wraps

from ...contracts import SkillError


def particle_global_info(rule):
    """Particle cooking has no rigid-object inputs or outputs."""
    return {}


def particle_container_info(rule, object_candidates, container, global_info):
    import torch
    # CookingPhysicalParticleRule forbids input objects and ignores unrelated
    # objects. Its native validator reads this field but never indexes it;
    # particle containment and conversion use ContainedParticles separately.
    return {'in_volume': torch.empty(0, dtype=torch.bool)}


class RecipeParticleOutputPolicy:
    def __init__(self):
        from omnigibson.transition_rules import CookingPhysicalParticleRule, RecipeRule, TransitionResults
        from omnigibson.systems.micro_particle_system import MicroPhysicalParticleSystem
        self.conversions = []
        self.rule_class = RecipeRule
        self.original = RecipeRule._execute_recipe
        self.original_transition = RecipeRule.transition
        self.particle_rule = CookingPhysicalParticleRule
        self.original_particle_global = CookingPhysicalParticleRule._compute_global_rule_info
        self.original_particle_container = CookingPhysicalParticleRule._compute_container_info
        CookingPhysicalParticleRule._compute_global_rule_info = particle_global_info
        CookingPhysicalParticleRule._compute_container_info = particle_container_info

        @wraps(self.original_transition)
        def transition(rule, object_candidates):
            # Native RecipeRule otherwise computes every movable object's AABB
            # and container relations even when it cannot produce any output.
            # Keep garbage fallback and the native condition/counter updates.
            if not rule._active_recipes and not rule.use_garbage_fallback_recipe:
                return TransitionResults(add=[], remove=[])
            return self.original_transition(rule, object_candidates)

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
        RecipeRule.transition = transition

    def describe(self):
        return dict(policy='native_surface_output_for_subdiameter_recipe_volume',
            empty_recipe_scans_skipped=True,
            particle_cooking_rigid_object_scans_skipped=True,
            recipes_changed=False, particle_sizes_changed=False,
            task_predicates_changed=False, conversions=list(self.conversions))

    def close(self):
        self.rule_class._execute_recipe = self.original
        self.rule_class.transition = self.original_transition
        self.particle_rule._compute_global_rule_info = self.original_particle_global
        self.particle_rule._compute_container_info = self.original_particle_container
