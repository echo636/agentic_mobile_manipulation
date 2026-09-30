"""Pinned OmniGibson 3.9.2 adapter for symbolic research on public challenge instances.

All simulator access belongs to the creating thread. Importing this module is cheap;
the simulator and its official primitives are imported only by the constructor.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import math
import os
import random
import subprocess
from pathlib import Path

from .contracts import SkillError
from .records import write_json


class OmniGibsonBackend:
    mode = "oracle_task_state"

    def __init__(self, task: str, instance: int, output: Path, *, seed: int = 0, max_steps: int = 20000):
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
        gm.RENDER_VIEWER_CAMERA = False
        gm.ENABLE_HQ_RENDERING = False
        gm.USE_GPU_DYNAMICS = False
        gm.ENABLE_TRANSITION_RULES = True
        self.og, self.torch = og, torch
        self.task_name, self.instance, self.seed, self.output = task, instance, seed, output
        self.steps = 0
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

        self.evaluator = ResearchEvaluator(OmegaConf.create({
            "task": {"name": task}, "mode": "public_test", "env_wrapper": None, "write_video": False,
        }))
        self.evaluator.reset()
        self.evaluator.load_task_instance(instance)
        self.evaluator.reset()
        self.env, self.robot = self.evaluator.env, self.evaluator.robot
        self.task_metric = next(m for m in self.evaluator.metrics if isinstance(m, TaskMetric))
        from omnigibson.action_primitives.symbolic_semantic_action_primitives import SymbolicSemanticActionPrimitives
        self.primitives = SymbolicSemanticActionPrimitives(self.env, self.robot)
        self.primitives._enable_head_tracking = False
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
        robot_cfg["sensor_config"]["VisionSensor"]["sensor_kwargs"].update(image_height=256, image_width=256)
        # Symbolic settling converts joint positions to actions; every actuated group must be absolute position.
        for name in ("base", "trunk", "arm_left", "arm_right", "gripper_left", "gripper_right"):
            robot_cfg["controller_config"][name] = {
                "name": "HolonomicBaseJointController" if name == "base" else "JointController",
                "motor_type": "position", "command_input_limits": None, "command_output_limits": None,
                "use_impedances": False, "use_delta_commands": False,
            }
        scene = task_cfg["scene_model"]
        template = instances / "scene_test" / "public" / scene / "json" / f"{scene}_task_{task}_0_0_template-partial_rooms.json"
        data = json.loads(template.read_text())
        embedded = [o for o in data["objects_info"]["init_info"].values() if o["class_name"] == "Robot"]
        if len(embedded) != 1:
            raise ValueError("Expected one embedded R1Pro in the challenge template")
        args = embedded[0]["args"]
        for key, value in robot_cfg.items():
            if key not in {"type", "name", "position", "orientation"}:
                args[key] = value
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
        config["robots"] = []
        config["env"].update(device=f"cuda:{gpu}", automatic_reset=False)
        config["task"]["termination_config"]["max_steps"] = max_steps
        return config

    def _objects(self) -> dict:
        return {key: obj for key, obj in self.env.task.object_scope.items()
                if obj is not None and obj is not self.robot and hasattr(obj, "get_position_orientation")
                and hasattr(obj, "states") and hasattr(obj, "aabb")}

    def _goal_options(self) -> list[list[bool]]:
        return [[bool(pred.evaluate(self.env.task._evaluate_predicate)) for pred in option]
                for option in self.env.task.ground_goal_state_options]

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

    def execute(self, skill: str, target: str | None, max_steps: int) -> dict:
        from omnigibson.action_primitives.symbolic_semantic_action_primitives import SymbolicSemanticActionPrimitiveSet as Primitive
        from omnigibson.action_primitives.action_primitive_set_base import ActionPrimitiveErrorGroup
        obj = self._objects().get(target)
        before = self.steps
        if skill == "navigate_to":
            return self._navigate(obj, max_steps)
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
                "protocol": "symbolic_oracle_research", "official_submission_eligible": False,
                "task_instance": self.task_metadata}

    def provenance(self) -> dict:
        gpu = os.environ.get("OMNIGIBSON_GPU_ID", "0")
        query = subprocess.run(["nvidia-smi", "-i", gpu, "--query-gpu=uuid", "--format=csv,noheader"], capture_output=True, text=True)
        packages = {p: metadata.version(p) for p in ("omnigibson", "torch", "isaacsim", "bddl", "mcp")}
        versions = {}
        root = Path(os.environ["OMNIGIBSON_DATA_PATH"])
        for name in ("behavior-1k-assets", "omnigibson-robot-assets"):
            version = root / name / "VERSION"
            versions[name] = version.read_text().strip() if version.exists() else "unresolved"
        return {"name": "OmniGibson", "executor": "official_symbolic_plus_ideal_navigation",
                "observation_mode": self.mode, "packages": packages, "gpu_index": gpu,
                "gpu_uuid": query.stdout.strip(), "assets": versions, "input_hashes": self.input_hashes,
                "task_instance": self.task_metadata, "official_submission_eligible": False}

    def close(self) -> None:
        self.env.close()
        self.og.shutdown()
