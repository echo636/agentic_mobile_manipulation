"""Load an imported custom scene without pretending it is an official task.

Only scene/robot initialization is supplied here. Actions, native transitions,
RGB observations and private custom evaluation use the normal backend.
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from .omnigibson_backend import configure_research_robot
from ..tasks.catalog_tasks import complete_scene_context
from ..records import write_json


def scene_config(backend):
    import yaml
    from omnigibson.utils.asset_utils import get_scene_path

    spec = backend.custom_spec
    frozen = (backend.spec_path.parent / spec['frozen_instance']['path']).resolve()
    data = json.loads(frozen.read_text())
    scene_model = spec['standalone_scene']['scene_model']
    template = Path(get_scene_path(scene_model)) / 'json' / (scene_model + '_best.json')
    template_bytes = template.read_bytes()
    restored = complete_scene_context(data, json.loads(template_bytes))
    encoded = json.dumps(data, sort_keys=True)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    cache = Path(os.environ['OMNIGIBSON_APPDATA_PATH']) / 'mas-custom-scenes'
    cache.mkdir(parents=True, exist_ok=True)
    scene_file = cache / (digest + '.json')
    scene_file.write_text(encoded)
    write_json(backend.output / 'scene_context.json', dict(
        policy='official_full_scene_with_authored_task_state',
        template_sha256=hashlib.sha256(template_bytes).hexdigest(),
        restored_objects=restored, authored_objects_and_task_state_preserved=True))
    if any(o['class_name'] in {'Robot', 'R1Pro'} for o in data['objects_info']['init_info'].values()):
        raise ValueError('Standalone import expects a scene without an embedded robot')
    robot = configure_research_robot(
        yaml.safe_load((Path(backend.og.__file__).parent / 'eval/r1pro.yaml').read_text()),
        fixed_surround=backend.fixed_surround_rgb, image_size=backend.image_size)
    start = spec['robot_start']
    robot.update(position=start['position'], orientation=start['orientation'])
    backend.input_hashes = dict(custom_task_spec=hashlib.sha256(backend.spec_path.read_bytes()).hexdigest(),
        scene_template=spec['frozen_instance']['sha256'], configured_scene=digest)
    write_json(backend.output / 'scene_compatibility.json', dict(changes=[],
        original_template_unmodified=True, scope='standalone_custom_scene',
        scene_object_count=len(data['objects_info']['init_info'])))
    return dict(
        env=dict(action_frequency=30, rendering_frequency=30, physics_frequency=120,
                 device='cpu', automatic_reset=False),
        scene=dict(type='InteractiveTraversableScene', scene_model=spec['standalone_scene']['scene_model'],
                   scene_file=str(scene_file), include_robots=False, load_room_types=None,
                   load_room_instances=None, load_object_categories=None,
                   not_load_object_categories=None, load_task_relevant_only=False,
                   trav_map_resolution=.05, default_erosion_radius=.57, waypoint_resolution=.1),
        robots=[robot], task=dict(type='DummyTask', include_obs=False))


def load_environment(backend, config):
    from .compat.broadphase import native_mbp_broadphase
    from .compat.articulation_sleep import ArticulationSleepPolicy
    from .compat.recipe_particle_output import RecipeParticleOutputPolicy

    policy = ArticulationSleepPolicy(gpu_dynamics=backend.use_gpu_dynamics)
    backend._standalone_sleep_policy = policy
    recipe_policy = backend._recipe_particle_output_policy = RecipeParticleOutputPolicy()
    try:
        with backend._startup_stage('load_standalone_custom_scene'), backend._native_startup_progress(), native_mbp_broadphase(backend.og) as broadphase:
            backend.env = backend.og.Environment(configs=config)
        write_json(backend.output / 'collision_broadphase.json', broadphase)
        with backend._startup_stage('reset_standalone_custom_scene'), backend._native_startup_progress('initial_reset'):
            backend.env.reset()
        backend.robot = backend.env.robots[0]
        backend.evaluator = SimpleNamespace(env=backend.env, robot=backend.robot, metrics=[])
        policy.validate_scene(backend.env.scene)
    except BaseException:
        policy.close()
        recipe_policy.close()
        raise
    finally:
        write_json(backend.output / 'articulation_sleep_policy.json', policy.describe())
        write_json(backend.output / 'recipe_particle_output_policy.json', recipe_policy.describe())
