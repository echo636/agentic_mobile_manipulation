"""CPU-only contract checks; synthetic fixtures provide no physical evidence."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from manipulation_agent.exploration_workspace import freeze_project

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "program_skills/rax/grasp-countertop-bottle"
spec = importlib.util.spec_from_file_location("countertop_recipe", SKILL / "controller.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


class Context:
    def __init__(self, distance=.7, fail=None):
        self.calls = []
        self.step = 0
        self.fail = fail
        self.notes = {"content": None, "version": 0}
        self.objects = [
            {"id": "bottle", "task_names": ["wine_bottle.n.01_1"], "category": "wine_bottle",
             "pose": {"position_m": [distance, 0, 1.3], "quaternion_xyzw": [0, 0, 0, 1]},
             "aabb": {"center_m": [distance, 0, 1.3], "max_m": [distance+.04, .04, 1.45]}},
            {"id": "counter", "task_names": ["countertop.n.01_1"], "category": "countertop",
             "aabb": {"max_m": [1, 1, 1]}},
        ]
        self.robot = {
            "base_pose_world": {"position_m": [0, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]},
            "grasping_mode": "assisted",
            "trunk": {"joint_positions_rad": [0]*4,
                      "joint_limits_rad": {"lower": [-4]*4, "upper": [4]*4},
                      "body_posture": {"upper_body_tilt_deg": 0, "upper_body_height_above_base_m": 1.14}},
            "body_links": [{"link": side+"_arm_link1", "aabb": {"center_m": [0, y, 1.44]}}
                           for side, y in (("left", .236), ("right", -.236))],
            "arms": {side: {"held_object": None, "joint_positions_rad": [0]*7,
                            "joint_limits_rad": {"lower": [-4]*7, "upper": [4]*7},
                            "contacts": {"finger_count": 0},
                            "finger_geometry": [{"aabb_in_eef": {"min_m": [0, 0, -.1], "max_m": [0, 0, .1]}}]*2}
                     for side in ("left", "right")},
        }

    def observation(self):
        return copy.deepcopy({"revision": 7, "capture": {"capture_id": "fixture", "sim_step": self.step},
                              "images": [], "oracle_3d": {"objects": self.objects, "robot": self.robot}})

    def call(self, name, **kw):
        self.calls.append((name, copy.deepcopy(kw)))
        if name == self.fail:
            return {"ok": False, "error": "injected failure"}
        result = {"ok": True}
        if name == "get_oracle_geometry":
            result.update(robot=copy.deepcopy(self.robot), capture=self.observation()["capture"],
                          objects=copy.deepcopy([o for ident in kw["object_ids"] for o in self.objects if o["id"] == ident]))
        elif name == "task_notes":
            if kw["operation"] == "write":
                self.notes = {"content": kw["content"], "version": self.notes["version"]+1}
            result["notes"] = copy.deepcopy(self.notes)
        elif name == "move_joints":
            group = kw["group"]
            target = self.robot["trunk"] if group == "trunk" else self.robot["arms"][group[4:]]
            target["joint_positions_rad"] = kw["positions_rad"]
            result["execution"] = {"reached": True, "max_joint_error_rad": 0}
        elif name == "wait":
            self.step += kw["steps"]
        elif name == "grasp":
            arm = self.robot["arms"][kw["arm"]]
            arm.update(held_object="bottle", native_grasp={"object_id": "bottle", "target_link": "base_link", "constraint_type": "fixture"},
                       eef_pose=kw["verification_waypoints"][0])
            lift = kw["verification_waypoints"][0]["position_m"][2]-kw["approach_waypoints"][-1]["position_m"][2]
            self.objects[0]["pose"]["position_m"][2] += lift
            result["execution"] = {"verified": True, "retention": {"retained": True}}
        elif name not in ("observe_scene", "set_oracle_focus"):
            raise AssertionError("Unexpected fixture tool: "+name)
        result["observation"] = self.observation()
        return result

    def motors(self):
        return [name for name, _ in self.calls if name in ("move_joints", "navigate_to", "grasp", "wait", "release")]


class ManipTests(unittest.TestCase):
    def invoke(self, ctx, **kw):
        with patch("builtins.print"):
            return recipe.Manip(ctx, recipe="countertop_pick",
                                targets={"object": recipe.EntityRef(task_name="wine_bottle.n.01_1")}, **kw)

    def test_invalid_arguments_never_call_context(self):
        for kw in ({"parameters": {"lift_m": float("nan")}}, {"parameters": {"lift_m": True}},
                   {"parameters": {"hold_steps": 59}}, {"parameters": {"hold_steps": 60.0}},
                   {"parameters": {"arm": "both"}}, {"parameters": {"unknown": 1}},
                   {"constraints": {"allow_base_motion": 1}}, {"revision": True},
                   {"parameters": {"ready_posture": "other"}}):
            with self.subTest(kw=kw):
                ctx = Context()
                self.assertEqual(self.invoke(ctx, **kw)["phase"], "validate_parameters")
                self.assertEqual(ctx.calls, [])
        ctx = Context()
        for args in ({"recipe": "missing", "targets": {}}, {"recipe": "countertop_pick", "targets": {"object": "raw-id"}}):
            self.assertFalse(recipe.Manip(ctx, **args)["ok"])
        self.assertEqual(ctx.calls, [])

    def test_stale_revision_stops_before_motion(self):
        ctx = Context()
        result = self.invoke(ctx, revision=6)
        self.assertIn("stale revision", result["error"])
        self.assertEqual([name for name, _ in ctx.calls], ["observe_scene"])

    def test_local_only_far_station_stops_before_joint_preparation(self):
        ctx = Context(distance=2)
        result = self.invoke(ctx)
        self.assertIn("approach_required", result["error"])
        self.assertEqual(result["transport"], "local_only")
        self.assertEqual(ctx.motors(), [])

    def test_disabled_trunk_stops_before_arm_preparation(self):
        ctx = Context()
        result = self.invoke(ctx, constraints={"allow_trunk_motion": False})
        self.assertIn("trunk preparation required", result["error"])
        self.assertEqual(ctx.motors(), [])

    def test_explicit_arm_lift_and_batched_hold(self):
        ctx = Context()
        result = self.invoke(ctx, revision=7, parameters={"arm": "right", "lift_m": .12, "hold_steps": 301})
        self.assertTrue(result["ok"], result)
        grasp = [kw for name, kw in ctx.calls if name == "grasp"]
        self.assertEqual(len(grasp), 1)
        self.assertEqual(grasp[0]["arm"], "right")
        self.assertAlmostEqual(grasp[0]["verification_waypoints"][0]["position_m"][2]-grasp[0]["approach_waypoints"][-1]["position_m"][2], .12)
        self.assertEqual([kw["steps"] for name, kw in ctx.calls if name == "wait"], [10, 150, 150, 1])
        self.assertEqual(result["metrics"]["hold_steps_observed"], 301)
        self.assertFalse(result["metrics"]["continuous_hold_validated"])
        self.assertNotIn("navigate_to", ctx.motors())

    def test_failure_does_not_retry_or_release(self):
        ctx = Context(fail="grasp")
        result = self.invoke(ctx)
        self.assertFalse(result["ok"])
        self.assertEqual(ctx.motors().count("grasp"), 1)
        self.assertEqual(ctx.calls[-1][0], "grasp")
        self.assertNotIn("release", ctx.motors())

    def test_navigation_forbidden_after_grasp_even_with_permission(self):
        ctx = Context()
        r = recipe.Robot(ctx, {}, recipe.DEFAULT_PARAMETERS, {"allow_base_motion": True}, None)
        r.grasps = 1
        with self.assertRaises(recipe.Stop):
            r.call("navigate_to", target={})
        self.assertEqual(ctx.calls, [])

    def test_main_selects_one_local_manip_after_separate_empty_approach(self):
        ctx = Context()
        original = ctx.call
        def call(name, **kw):
            if name == 'manip':
                ctx.calls.append((name, copy.deepcopy(kw)))
                return {'ok': True}
            return original(name, **kw)
        ctx.call = call
        with patch.object(recipe, 'Manip', side_effect=AssertionError('Bypassed the manip tool')):
            with patch('builtins.print'):
                result = recipe.main(ctx)
        self.assertTrue(result['ok'])
        args = next(kw for name, kw in ctx.calls if name == 'manip')
        self.assertEqual(args['recipe'], 'countertop_pick')
        self.assertFalse(args['constraints']['allow_base_motion'])
        self.assertEqual(args['targets']['object'], {'task_name': 'wine_bottle.n.01_1'})
        self.assertEqual(result['navigation']['transport'], 'ideal_checked_empty_navigation')

    def test_source_freezes_and_spec_hash_matches(self):
        expected = json.loads((SKILL / "spec.json").read_text())["controller_sha256"]
        self.assertEqual(hashlib.sha256((SKILL / "controller.py").read_bytes()).hexdigest(), expected)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(SKILL, root / "workspace/code/bottle")
            frozen = freeze_project(root / "workspace", "bottle", root / "bundles")
            self.assertEqual(frozen["files"]["controller.py"], expected)


if __name__ == "__main__":
    unittest.main()
