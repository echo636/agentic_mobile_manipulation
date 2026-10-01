"""Pinned OmniGibson 3.9.2 adapter for symbolic research on public challenge instances.

All simulator access belongs to the creating thread. Importing this module is cheap;
the simulator and its official primitives are imported only by the constructor.
"""
from __future__ import annotations

import hashlib
import copy
import importlib.metadata as metadata
import json
import math
import os
import random
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .contracts import SkillError
from .records import write_json
from .goal_grounding import efficient_grounding, evaluate_once_per_literal


def normalize_embedded_robot(data):
    """Migrate the known legacy R1Pro serialization in a derived scene copy only."""
    changes = []
    for key, entry in data['objects_info']['init_info'].items():
        if (entry.get('class_module'),entry.get('class_name')) == ('omnigibson.robots.r1pro','R1Pro'):
            if entry['args'].get('model','r1pro') != 'r1pro':
                raise ValueError('Conflicting legacy robot model')
            entry.update(class_module='omnigibson.robots.robot',class_name='Robot')
            entry['args']['model']='r1pro'
            changes.append({'object':key,'from':'omnigibson.robots.r1pro.R1Pro',
                            'to':'omnigibson.robots.robot.Robot','model':'r1pro'})
        if entry.get('class_name') == 'Robot' and entry.get('args', {}).get('model') == 'r1pro':
            state = data.get('state', {}).get('registry', {}).get('object_registry', {}).get(key)
            if state is not None and 'controller_groups' not in state:
                if 'controllers' not in state:
                    raise ValueError(f'Unrecognized serialized controller schema for {key}')
                # Old IK/velocity goals are incompatible with the newly configured
                # absolute-position controllers. Retain physical state and use the
                # current controllers' defaults, followed by the evaluator reset.
                state['controller_groups'] = {}
                changes.append({'object': key, 'migration': 'legacy_controller_state',
                    'old_controller_names': sorted(state['controllers']),
                    'policy': 'current_controller_defaults_then_evaluator_reset',
                    'physical_state_preserved': True})
    return changes


def select_compatible_scene(template: Path, instance_path: Path):
    """Choose a supplied template whose object bindings cover the actual instance.

    Some challenge archives ship an obsolete partial-room template alongside a
    current full template. Never rename task objects or substitute another task.
    """
    required = set(json.loads(instance_path.read_text())) - {'robot_poses'}
    candidates = [template, template.with_name(template.name.replace('-partial_rooms', ''))]
    rejected = []
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        data = json.loads(candidate.read_text())
        bindings = data.get('metadata', {}).get('task', {}).get('inst_to_name', {})
        objects = data['objects_info']['init_info']
        missing = sorted(k for k in required if k not in bindings or
                         (not k.startswith('agent.') and bindings[k] not in objects))
        if not missing:
            return candidate, data, rejected
        rejected.append({'path': str(candidate), 'missing_instance_bindings': missing,
                         'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()})
    raise ValueError('Task instance and scene templates are incompatible: ' + json.dumps(rejected))


def restore_static_floor_geometry(data, full):
    """Restore omitted fixed floors from the same supplied scene template.

    Partial-room assets can omit the corridor between two required rooms while
    the full traversability map still routes through it. Never add task objects,
    change bindings or modify a present object; only absent fixed floor meshes.
    """
    added=[];objects=data['objects_info']['init_info']
    states=data['state']['registry']['object_registry']
    for name,entry in full['objects_info']['init_info'].items():
        args=entry.get('args',{})
        if name in objects or args.get('category')!='floors' or args.get('fixed_base') is not True:continue
        state=full['state']['registry']['object_registry'].get(name)
        if state is None:raise ValueError('Missing pinned floor state: '+name)
        objects[name]=copy.deepcopy(entry);states[name]=copy.deepcopy(state);added.append(name)
    return added


class OmniGibsonBackend:
    mode = "oracle_task_state"

    @contextmanager
    def _startup_stage(self, name):
        start = time.monotonic()
        def record(status, **extra):
            with (self.output / 'startup_stages.jsonl').open('a') as stream:
                stream.write(json.dumps({'stage': name, 'status': status,
                    'elapsed_seconds': time.monotonic() - start, **extra}) + '\n')
        record('running')
        try:
            yield
        except BaseException as exc:
            record('failed', error_type=type(exc).__name__, error=str(exc))
            raise
        else:
            record('passed')

    def __init__(self, task: str, instance: int, output: Path, *, seed: int = 0, max_steps: int = 20000,
                 inside_placement: str = "symbolic_raycast"):
        # Fail explicitly on the vector-env API change until its adapter is validated.
        version = metadata.version("omnigibson")
        if version != "3.9.2":
            raise RuntimeError(f"This research adapter requires OmniGibson 3.9.2, got {version}")
        import numpy as np
        import torch
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        gpu = int(os.environ.get("OMNIGIBSON_GPU_ID", "0"))
        torch.cuda.set_device(gpu)
        import omnigibson as og
        from omnigibson.macros import gm
        from omnigibson.eval.evaluator import Evaluator, resolve_instance_ids
        from omnigibson.eval.utils.eval_utils import NUM_PUBLIC_TEST_INSTANCES
        from omnigibson.eval.utils.score_utils import load_human_stats
        from omnigibson.metrics import TaskMetric
        from omegaconf import OmegaConf
        from gello.utils.og_teleop_cfg import DISABLED_TRANSITION_RULES

        if instance not in resolve_instance_ids(task, list(range(NUM_PUBLIC_TEST_INSTANCES)), "public_test"):
            raise ValueError("Instance must belong to this pinned source's public test split")
        gm.HEADLESS = True
        gm.RENDER_VIEWER_CAMERA = bool(getattr(self,'private_viewer_grounding',False))
        gm.ENABLE_HQ_RENDERING = False
        gm.USE_GPU_DYNAMICS = False
        gm.ENABLE_TRANSITION_RULES = True
        self.og, self.torch = og, torch
        self.task_name, self.instance, self.seed, self.output = task, instance, seed, output
        self.steps = 0
        self.inside_placement = inside_placement
        self.sampling_physics_steps = 0
        self.navigation_distance = 0.0
        self.frames_revision = -1
        self.frames = []
        config = self._config(task, gpu, max_steps)
        write_json(output / "environment_config.json", config)

        class PassivePolicy:
            def reset(self):
                pass

        class ResearchEvaluator(Evaluator):
            def load_env(inner, env_wrapper):
                for rule in DISABLED_TRANSITION_RULES:
                    rule.ENABLED = False
                inner.human_stats = load_human_stats(task)
                return og.Environment(configs=config)

            def load_policy(inner):
                return PassivePolicy()

            def _preprocess_obs(inner, obs):
                return obs

            def load_metrics(inner):
                metrics = super().load_metrics()
                # Preserve the pinned official metric implementation and formula.
                # Only memoize identical predicate reads within a scoring pass.
                for metric in metrics:
                    if isinstance(metric, TaskMetric):
                        reset = metric.reset
                        compute = metric._compute_episode_metrics
                        def cached_reset(env, original=reset):
                            with evaluate_once_per_literal(env.task.ground_goal_state_options):
                                return original(env)
                        def cached_compute(env, info, original=compute):
                            with evaluate_once_per_literal(env.task.ground_goal_state_options):
                                return original(env, info)
                        metric.reset = cached_reset
                        metric._compute_episode_metrics = cached_compute
                return metrics

        with self._startup_stage('construct_evaluator_and_environment'), efficient_grounding():
            self.evaluator = ResearchEvaluator(OmegaConf.create({
                "task": {"name": task}, "mode": "public_test", "env_wrapper": None, "write_video": False,
            }))
        with self._startup_stage('initial_reset'):
            self.evaluator.reset()
        with self._startup_stage('load_task_instance'):
            self.evaluator.load_task_instance(instance)
        with self._startup_stage('instance_reset'):
            self.evaluator.reset()
        self.env, self.robot = self.evaluator.env, self.evaluator.robot
        from omnigibson.utils.asset_utils import get_task_instance_path
        import bddl
        scene = self.env.task.scene_name
        filename = self.env.task.get_cached_activity_scene_filename(
            scene_model=scene, activity_name=task, activity_definition_id=0, activity_instance_id=instance)
        instance_path = Path(get_task_instance_path(scene, f"{scene}_task_{task}_instances/{filename}-tro_state", mode="public_test"))
        self.input_hashes["task_instance_state"] = hashlib.sha256(instance_path.read_bytes()).hexdigest()
        definition_path = Path(bddl.__file__).parent / "activity_definitions" / task / "problem0.bddl"
        self.input_hashes["bddl_definition"] = hashlib.sha256(definition_path.read_bytes()).hexdigest()
        write_json(output / "dependency_versions.json", {d.metadata["Name"]: d.version for d in metadata.distributions() if d.metadata["Name"]})
        self.task_metric = next(m for m in self.evaluator.metrics if isinstance(m, TaskMetric))
        from omnigibson.action_primitives.symbolic_semantic_action_primitives import SymbolicSemanticActionPrimitives
        self.primitives = SymbolicSemanticActionPrimitives(self.env, self.robot)
        self.primitives._enable_head_tracking = False
        with self._startup_stage('initial_goal_evaluation'):
            self.initial_goals = self._goal_options()
        # Keep original task-instance files intact. Evaluator performs its official restoration.
        self.task_metadata = {"task": task, "instance": instance, "split": "public_test",
                              "scene": self.env.task.scene_name, "seed": seed}

    def _config(self, task: str, gpu: int, max_steps: int) -> dict:
        import yaml
        from omnigibson.eval.utils.eval_utils import generate_basic_environment_config
        root = Path(os.environ["OMNIGIBSON_DATA_PATH"])
        instances = root / "2026-challenge-task-instances"
        manifest = instances / "metadata" / "available_tasks.yaml"
        task_cfg = yaml.safe_load(manifest.read_text())[task][0]
        config = generate_basic_environment_config(task, task_cfg)
        robot_cfg = yaml.safe_load((Path(self.og.__file__).parent / "eval" / "r1pro.yaml").read_text())
        robot_cfg.pop("eval", None)
        robot_cfg.update(grasping_mode="sticky", disable_grasp_handling=True,
                         obs_modalities=["rgb", "depth_linear", "proprio"])
        if getattr(self, 'fixed_surround_rgb', False):
            # A separate calibrated four-camera rig replaces the stock head/wrist sensors.
            robot_cfg['include_sensor_names'] = []
            robot_cfg.pop('exclude_sensor_names', None)
        image_size = getattr(self, "image_size", 256)
        robot_cfg["sensor_config"]["VisionSensor"]["sensor_kwargs"].update(image_height=image_size, image_width=image_size)
        # Symbolic settling converts joint positions to actions; every actuated group must be absolute position.
        for name in ("base", "trunk", "arm_left", "arm_right", "gripper_left", "gripper_right"):
            robot_cfg["controller_config"][name] = {
                "name": "HolonomicBaseJointController" if name == "base" else "JointController",
                "motor_type": "position", "command_input_limits": None, "command_output_limits": None,
                "use_impedances": False, "use_delta_commands": False,
            }
        # HolonomicBaseJointController fixes this internally and has no such constructor argument.
        robot_cfg["controller_config"]["base"].pop("use_delta_commands")
        scene = task_cfg["scene_model"]
        template = instances / "scene_test" / "public" / scene / "json" / f"{scene}_task_{task}_0_0_template-partial_rooms.json"
        instance_path = template.parent / f'{scene}_task_{task}_instances' / f'{scene}_task_{task}_0_{self.instance}_template-tro_state.json'
        template, data, rejected = select_compatible_scene(template, instance_path)
        if rejected:
            write_json(self.output / 'scene_template_selection.json', {
                'selected': str(template), 'rejected': rejected,
                'policy': 'use_supplied_full_template_with_matching_instance_bindings',
                'original_assets_unmodified': True, 'task_instance_unchanged': True})
        full_template=template.with_name(template.name.replace('-partial_rooms',''))
        if full_template!=template and full_template.is_file():
            full_data=json.loads(full_template.read_text())
            restored_floors=restore_static_floor_geometry(data,full_data)
            if restored_floors:
                write_json(self.output/'scene_geometry_repair.json',{
                    'policy':'restore_absent_fixed_floors_from_same_supplied_full_template',
                    'added_floors':restored_floors,'full_template_sha256':hashlib.sha256(full_template.read_bytes()).hexdigest(),
                    'task_object_bindings_unchanged':True,'original_assets_unmodified':True,
                    'scope':'research_scene_geometry_repair_not_official_submission'})
        migrations = normalize_embedded_robot(data)
        if migrations:
            write_json(self.output/'scene_compatibility.json',{'changes':migrations,
                       'original_template_unmodified':True,'asset_hash_check_preserved':True})
        embedded = [o for o in data["objects_info"]["init_info"].values() if o["class_name"] == "Robot"]
        if len(embedded) > 1:
            raise ValueError("Multiple embedded robots are unsupported")
        if embedded:
            args = embedded[0]["args"]
            for key, value in robot_cfg.items():
                if key not in {"type", "name", "position", "orientation"}:
                    args[key] = value
            config["robots"] = []
        else:
            robot_cfg.update(position=task_cfg["robot_start_position"], orientation=task_cfg["robot_start_orientation"])
            config["robots"] = [robot_cfg]
        cache = Path(os.environ["OMNIGIBSON_APPDATA_PATH"]) / "mas-scenes"
        cache.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(data, sort_keys=True)
        patched = cache / (hashlib.sha256(encoded.encode()).hexdigest() + ".json")
        patched.write_text(encoded)
        self.input_hashes = {"available_tasks": hashlib.sha256(manifest.read_bytes()).hexdigest(),
                             "scene_template": hashlib.sha256(template.read_bytes()).hexdigest(),
                             "configured_scene": hashlib.sha256(encoded.encode()).hexdigest()}
        config["scene"].update(scene_file=str(patched), trav_map_resolution=0.05,
                              default_erosion_radius=0.57, waypoint_resolution=0.1)
        config["env"].update(device=f"cuda:{gpu}", automatic_reset=False)
        if getattr(self,'private_viewer_grounding',False):
            # Set the initial product size; never resize it after rig creation.
            config.setdefault('render',{}).update(viewer_width=image_size,viewer_height=image_size)
        config["task"]["termination_config"]["max_steps"] = max_steps
        return config

    def _objects(self) -> dict:
        return {key: obj for key, obj in self.env.task.object_scope.items()
                if obj is not None and obj is not self.robot and hasattr(obj, "get_position_orientation")
                and hasattr(obj, "states") and hasattr(obj, "aabb")}

    def _goal_options(self) -> list[list[bool]]:
        options = self.env.task.ground_goal_state_options
        with evaluate_once_per_literal(options):
            return [[bool(pred.evaluate(self.env.task._evaluate_predicate)) for pred in option]
                    for option in options]

    def _capture(self) -> list[dict]:
        if self.frames_revision == self.steps:
            return self.frames
        from PIL import Image
        import numpy as np
        directory = self.output / "frames"
        directory.mkdir(exist_ok=True)
        self.og.sim.render()
        images = []
        for i, (name, sensor) in enumerate(self.robot.sensors.items()):
            data, _ = sensor.get_obs()
            if "rgb" not in data:
                continue
            rgb = data["rgb"].detach().cpu().numpy()[..., :3]
            image_path = directory / f"step_{self.steps:06d}_camera_{i}.jpg"
            Image.fromarray(rgb.astype(np.uint8)).save(image_path)
            depth = data.get("depth_linear")
            row = {"camera": name, "path": str(image_path.relative_to(self.output))}
            if depth is not None:
                values = depth.detach().cpu().numpy()
                row["finite_depth_fraction"] = float(np.isfinite(values).mean())
                np.save(directory / f"step_{self.steps:06d}_camera_{i}_depth.npy", values)
            images.append(row)
        self.frames_revision, self.frames = self.steps, images
        return images

    def observe(self) -> dict:
        from omnigibson import object_states as states
        objects = self._objects()
        base = self.robot.get_position_orientation()[0]
        held = self.primitives._get_obj_in_hand()
        result = []
        for key, obj in objects.items():
            lower, upper = obj.aabb
            nearest = self.torch.maximum(lower[:2], self.torch.minimum(base[:2], upper[:2]))
            values = {}
            for label, cls in (("open", states.Open), ("toggled_on", states.ToggledOn)):
                if cls in obj.states:
                    values[label] = bool(obj.states[cls].get_value())
            relations = {}
            for label, cls in (("inside", states.Inside), ("on_top", states.OnTop)):
                if cls in obj.states:
                    relations[label] = [other_key for other_key, other in objects.items()
                                        if other is not obj and obj.states[cls].get_value(other)]
            result.append({"id": key, "category": obj.category, "name": obj.name,
                           "graspable": not obj.fixed_base,
                           "distance_m": round(float(self.torch.linalg.norm(base[:2] - nearest)), 3),
                           "states": values, "relations": relations})
        return {"objects": result, "held_object": next((k for k, v in objects.items() if v is held), None),
                "images": self._capture(), "available_skills": list(__import__("manipulation_agent.contracts", fromlist=["SKILLS"]).SKILLS)}

    def _step(self, action) -> None:
        obs, reward, terminated, truncated, info = self.env.step(action)
        self.steps += 1
        for metric in self.evaluator.metrics:
            metric.step(self.env, action, obs, reward, terminated, truncated, info)

    def _navigate(self, target, max_steps: int) -> dict:
        """Ideal navigation: reachable eroded-map endpoint teleport, not physical path execution.

        Bypasses the upstream symbolic NAVIGATE_TO path that dereferences its absent
        CuRobo planner. The map is privileged executor state and explicitly recorded.
        """
        import cv2
        import numpy as np
        import omnigibson.utils.transform_utils as T
        torch = self.torch
        trav = self.env.scene.trav_map
        start, orientation = self.robot.get_position_orientation()
        goal = target.get_position_orientation()[0]
        floor = min(range(len(trav.floor_heights)), key=lambda i: abs(float(start[2]) - trav.floor_heights[i]))
        occupancy = trav._erode_trav_map(trav.floor_map[floor].clone()).cpu().numpy()
        _, labels = cv2.connectedComponents(occupancy, connectivity=4)
        source = trav.world_to_map(start[:2]).cpu().numpy()
        if not (0 <= source[0] < labels.shape[0] and 0 <= source[1] < labels.shape[1]):
            raise SkillError("navigation_unreachable", "Robot is outside traversability map")
        component = labels[tuple(source)]
        if component == 0:
            raise SkillError("navigation_unreachable", "Robot start is outside the eroded traversable region")
        cells = torch.tensor(np.column_stack(np.where(labels == component)), dtype=torch.float32)
        points = trav.map_to_world(cells)
        lower, upper = target.aabb
        nearest = torch.maximum(lower[:2].cpu(), torch.minimum(points, upper[:2].cpu()))
        distance = torch.linalg.norm(points - nearest, dim=1)
        valid = (distance >= 0.60) & (distance <= 1.15)
        candidates = points[valid]
        if len(candidates) == 0:
            raise SkillError("navigation_unreachable", "No reachable standoff near the target")
        cost = torch.linalg.norm(candidates - start[:2].cpu(), dim=1)
        endpoint = candidates[int(torch.argmin(cost))]
        path, length = trav.get_shortest_path(floor, start[:2].cpu(), endpoint, entire_path=True)
        if path is None:
            raise SkillError("navigation_unreachable", "No path to sampled target standoff")
        if max_steps < 50:
            raise SkillError("budget_exhausted", "Navigation needs at least 50 settling steps")
        new_pos = start.clone()
        new_pos[:2] = endpoint.to(start.device)
        yaw = math.atan2(float(goal[1] - new_pos[1]), float(goal[0] - new_pos[0]))
        new_quat = T.euler2quat(torch.tensor([0.0, 0.0, yaw], device=start.device))
        if hasattr(self, '_execute_base_path'):
            route = [start[:2].cpu().tolist(), *path.cpu().tolist(), endpoint.cpu().tolist()]
            result = self._execute_base_path(route, yaw, max_steps)
            self.navigation_distance += float(length)
            return {**result,'path_distance_m':float(length),'dynamic_collision_check':False}
        held = self.primitives._get_obj_in_hand()
        relative = T.relative_pose_transform(*held.get_position_orientation(), start, orientation) if held else None
        self.robot.set_position_orientation(new_pos, new_quat)
        if held is not None:
            held.set_position_orientation(*T.pose_transform(new_pos, new_quat, *relative))
            held.keep_still()
        self.robot.keep_still()
        for _ in range(50):
            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        self.navigation_distance += float(length)
        return {"navigation": "ideal_reachable_endpoint_teleport", "path_distance_m": float(length),
                "dynamic_collision_check": False, "settling_steps": 50}

    def _place_inside_volume(self, target, max_steps: int) -> dict:
        """Ideal placement through official collision-checked volume sampling.

        No goal/evaluator access: release the currently held object, sample a real
        pose using Inside.set_value, settle and independently recheck the relation.
        Its internal physics ticks are bounded and counted separately from env.step.
        Failed placement can leave the item released and must be replanned.
        """
        from omnigibson.object_states import Inside
        held = self.primitives._get_obj_in_hand()
        if held is None:
            raise SkillError("empty_hand", "No object is held")
        fillable = [link for link in target.links.values() if link.is_meta_link and
                    link.meta_link_type in {"fillable", "openfillable"}]
        if not fillable or Inside not in held.states:
            raise SkillError("unsupported_relation", "Target has no supported fillable volume")
        # Release before sampling; the grasp constraint would drag a sampled pose back.
        for arm in self.robot.arm_names:
            self.robot.release_grasp_immediately(arm=arm)
        original_step = self.og.sim.step_physics
        started = time.monotonic()
        before = self.sampling_physics_steps
        physics_limit = min(6000, max_steps * 4)

        def bounded_step(*args, **kwargs):
            if self.sampling_physics_steps - before >= physics_limit or time.monotonic() - started > 120:
                raise SkillError("sampling_budget_exhausted", "Volume sampler exceeded physics/time limit", changed=True)
            self.sampling_physics_steps += 1
            return original_step(*args, **kwargs)

        self.og.sim.step_physics = bounded_step
        try:
            sampled = held.states[Inside].set_value(target, True)
        finally:
            self.og.sim.step_physics = original_step
            self.frames_revision = -1
        if not sampled:
            raise SkillError("sampling_error", "Official volume sampler could not place the released object", changed=True)
        for _ in range(min(50, max_steps)):
            self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
        if not held.states[Inside].get_value(target):
            raise SkillError("postcondition_error", "Object left the container after settling", changed=True)
        return {"primitive": "place_inside", "implementation": "official_Inside_set_value_volume_sampler",
                "postcondition": "Inside.get_value_after_settling", "sampling_physics_steps": self.sampling_physics_steps - before}

    def execute(self, skill: str, target: str | None, max_steps: int) -> dict:
        from omnigibson.action_primitives.symbolic_semantic_action_primitives import SymbolicSemanticActionPrimitiveSet as Primitive
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveErrorGroup
        obj = self._objects().get(target)
        before = self.steps
        if skill == "navigate_to":
            return self._navigate(obj, max_steps)
        if skill == "place_inside" and self.inside_placement == "official_volume":
            return self._place_inside_volume(obj, max_steps)
        if skill == "wait":
            for _ in range(min(30, max_steps)):
                self._step(self.robot.q_to_action(self.robot.get_joint_positions()))
            return {"steps": self.steps - before}
        generator = self.primitives.apply_ref(getattr(Primitive, skill.upper()), *([] if obj is None else [obj]), attempts=1)
        try:
            for action in generator:
                if self.steps - before >= max_steps:
                    raise SkillError("action_timeout", "Primitive exceeded its physics-step budget", changed=True)
                self._step(action)
        except ActionPrimitiveErrorGroup as exc:
            reason = exc.exceptions[-1].reason.name.lower() if exc.exceptions else "primitive_failure"
            raise SkillError(reason, str(exc), changed=True) from exc
        finally:
            generator.close()
        return {"primitive": skill, "steps": self.steps - before, "postcondition": "official_primitive_checked"}

    def evaluate(self) -> dict:
        goals = self._goal_options()
        task_success = any(bool(option) and all(option) for option in goals)
        # This calls the pinned official TaskMetric, including its initial-state partial-credit convention.
        official = self.task_metric._compute_episode_metrics(self.env, self.task_metric.state[self.env.scene])
        if self.steps == 0:
            official["time"]["normalized_time"] = None
        return {"task_success": task_success, "official_task_success": bool(self.env.task.success),
                "goal_options": goals, "initial_goal_options": self.initial_goals,
                "goal_satisfaction_fraction": max((sum(o) / len(o) for o in goals if o), default=0),
                "official_metrics": official, "ideal_navigation_distance_m": self.navigation_distance,
                "inside_placement": self.inside_placement,
                "volume_sampling_physics_steps": self.sampling_physics_steps,
                "time_metric_scope": "env.step only; excludes separate volume sampling physics ticks",
                "protocol": "symbolic_oracle_research", "official_submission_eligible": False,
                "task_instance": self.task_metadata}

    def provenance(self) -> dict:
        gpu = os.environ.get("OMNIGIBSON_GPU_ID", "0")
        query = subprocess.run(["nvidia-smi", "-i", gpu, "--query-gpu=uuid", "--format=csv,noheader"], capture_output=True, text=True)
        packages = {p: metadata.version(p) for p in ("omnigibson", "torch", "isaacsim", "bddl", "mcp")}
        source = subprocess.run(["git", "-C", str(Path(self.og.__file__).parent), "rev-parse", "HEAD"], capture_output=True, text=True)
        versions = {}
        root = Path(os.environ["OMNIGIBSON_DATA_PATH"])
        for name in ("behavior-1k-assets", "omnigibson-robot-assets"):
            version = root / name / "VERSION"
            versions[name] = version.read_text().strip() if version.exists() else "unresolved"
        return {"name": "OmniGibson", "executor": "official_symbolic_plus_ideal_navigation",
                "observation_mode": self.mode, "packages": packages, "gpu_index": gpu,
                "inside_placement": self.inside_placement,
                "omnigibson_source_commit": source.stdout.strip(),
                "gpu_uuid": query.stdout.strip(), "assets": versions, "input_hashes": self.input_hashes,
                "task_instance": self.task_metadata, "official_submission_eligible": False}

    def close(self) -> None:
        self.env.close()
        self.og.shutdown()
