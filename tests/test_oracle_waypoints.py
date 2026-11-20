"""CPU contract/control-flow checks; these are not robot or model experiment results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from manipulation_agent.compiled_oracle import CompiledOracleHarness
from manipulation_agent.contracts import Budget, SkillError, validate
from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend
from manipulation_agent.oracle_contracts import checked_pose, public_observation, tool_specs
from manipulation_agent.oracle_harness import OracleWaypointHarness
from manipulation_agent.records import Recorder
from manipulation_agent.tools import tool_specs as all_specs
from manipulation_agent.vision_policy import system_prompt


def waypoint(x=.4, grip="hold"):
    return {"position_m": [x, -.2, .8], "quaternion_xyzw": [0., 0., 0., 1.],
            "gripper": grip}


class FakeMotor:
    execute_motor = OracleWaypointBackend.execute_motor

    def __init__(self, fail_at=None):
        from manipulation_agent.deadline import EpisodeDeadline
        self.deadline = EpisodeDeadline()
        self.calls = []
        self.fail_at = fail_at
        self._trace_target = self._trace_arm = self._trace_waypoint = None

    def _move_eef(self, arm, target):
        self.calls.append(("pose", self._trace_waypoint, arm))
        return {"reached": self._trace_waypoint != self.fail_at}

    def _set_gripper(self, arm, command):
        self.calls.append(("gripper", command, arm))
        return {"reached": True}


class FakeBackend:
    mode = "oracle_3d_waypoints"
    steps = 0

    def __init__(self):
        self.moves = []
        self.fail = False

    def observe(self):
        return {"observation_mode": self.mode, "images": [],
                "capture": {"capture_id": "fake", "sim_step": self.steps},
                "oracle_3d": {"frame": "robot_base", "units": {}, "robot": {},
                              "objects": [], "object_scope": "test_fixture", "omitted_objects": 0}}

    def provenance(self):
        return {"name": "CPU test fixture", "observation_mode": self.mode}

    def control_contract(self):
        return {"protocol": "cpu_test_fixture"}

    def execute_motor(self, name, **args):
        self.moves.append((name, args))
        self.steps += 1
        if self.fail:
            raise SkillError("test_partial_failure", "Test failure", changed=True)
        return {"reached": True}

    def evaluate(self):
        return {"task_success": False, "fixture_only": True}


class OracleContractTests(unittest.TestCase):
    def test_new_catalog_does_not_expand_rgb_or_workflow(self):
        expected = {row["name"] for row in tool_specs()}
        self.assertIn("execute_waypoints", expected)
        self.assertEqual(expected, {row["name"] for row in all_specs("oracle_waypoints")})
        for profile in ("minimal", "skills", "workflow"):
            self.assertNotIn("execute_waypoints", {row["name"] for row in all_specs(profile)})
        self.assertIn("Oracle 3D", system_prompt("oracle_waypoints"))

    def test_full_pose_and_finite_unit_orientation_required(self):
        schema = next(row["inputSchema"] for row in tool_specs() if row["name"] == "execute_waypoints")
        good = {"arm": "right", "frame": "robot_base", "waypoints": [waypoint()], "revision": 0}
        validate(good, schema)
        bad = copy.deepcopy(good)
        del bad["waypoints"][0]["quaternion_xyzw"]
        with self.assertRaises(SkillError):
            validate(bad, schema)
        for quat in ([0, 0, 0, 0], [0, 0, 0, 2], [0, 0, float("nan"), 1]):
            pose = waypoint()
            pose["quaternion_xyzw"] = quat
            with self.assertRaises(SkillError):
                checked_pose(pose)
        self.assertEqual(checked_pose(waypoint())["quaternion_xyzw"], [0, 0, 0, 1])

    def test_whole_batch_validation_precedes_any_motion(self):
        motor = FakeMotor()
        invalid = waypoint()
        invalid["quaternion_xyzw"] = [0, 0, 0, 0]
        with self.assertRaises(SkillError):
            motor.execute_motor("execute_waypoints", arm="right", frame="robot_base",
                                waypoints=[waypoint(grip="close"), invalid])
        self.assertEqual(motor.calls, [])

    def test_failed_waypoint_does_not_close_or_execute_later_pose(self):
        motor = FakeMotor(fail_at=0)
        result = motor.execute_motor("execute_waypoints", arm="right", frame="robot_base",
                                     waypoints=[waypoint(grip="close"), waypoint(.5)])
        self.assertFalse(result["reached"])
        self.assertEqual(result["failed_waypoint"], 0)
        self.assertEqual(motor.calls, [("pose", 0, "right")])
        self.assertIsNone(motor._trace_target)

    def test_pose_then_gripper_then_lift_order(self):
        motor = FakeMotor()
        result = motor.execute_motor("execute_waypoints", arm="left", frame="robot_base",
                                     waypoints=[waypoint(grip="close"), waypoint(.5)])
        self.assertTrue(result["reached"])
        self.assertEqual(motor.calls, [("pose", 0, "left"), ("gripper", "close", "left"),
                                       ("pose", 1, "left")])

    def test_evaluator_fields_cannot_enter_oracle_geometry_packet(self):
        packet = FakeBackend().observe()
        packet["oracle_3d"]["robot"]["task_success"] = True
        with self.assertRaises(RuntimeError):
            public_observation(packet, 0)


class OracleHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "episode"
        self.backend = FakeBackend()
        self.harness = OracleWaypointHarness(self.backend, Recorder(self.output, {}),
                                              Budget(wall_seconds=None))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, ident="one", revision=0):
        return self.harness.call("execute_waypoints", {
            "arm": "right", "frame": "robot_base", "waypoints": [waypoint()],
            "revision": revision}, ident)

    def test_retry_is_idempotent_and_stale_revision_rejected(self):
        first = self.call()
        self.assertTrue(first["ok"])
        self.assertEqual(first, self.call())
        self.assertEqual(len(self.backend.moves), 1)
        stale = self.call("two")
        self.assertEqual(stale["error"]["code"], "stale_observation")
        self.assertEqual(len(self.backend.moves), 1)
        self.assertEqual(self.harness.agent_steps, 2)

    def test_partial_error_refreshes_revision_and_geometry(self):
        self.backend.fail = True
        result = self.call()
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"]["world_may_have_changed"])
        self.assertEqual(result["observation"]["revision"], 1)
        self.assertEqual(result["observation"]["capture"]["sim_step"], 1)

    def test_oracle_skill_reads_are_frozen_allowlisted_and_do_not_move_robot(self):
        before = (self.harness.revision, self.backend.steps, list(self.backend.moves))
        catalog = self.harness.call("list_skills", {}, "skill-list")
        self.assertEqual([s["name"] for s in catalog["skills"]], ["oracle-manipulation"])
        skill = self.harness.call("read_skill", {"name": "oracle-manipulation", "resource": "SKILL.md"}, "skill-read")
        self.assertTrue(skill["ok"])
        self.assertEqual(skill["bundle_sha256"], catalog["bundle_sha256"])
        frozen = self.output / "skill_snapshot/oracle-manipulation/SKILL.md"
        self.assertEqual(skill["text"], frozen.read_text())
        bad = self.harness.call("read_skill", {"name": "oracle-manipulation", "resource": "../../AGENTS.md"}, "skill-bad")
        self.assertEqual(bad["error"]["code"], "unknown_skill_resource")
        self.assertEqual(before, (self.harness.revision, self.backend.steps, self.backend.moves))

    def test_finish_claim_cannot_override_independent_failure(self):
        result = self.harness.call("finish", {"outcome": "achieved", "reason": "Fixture claim"}, "finish")
        self.assertTrue(result["closed"])
        run = json.loads((self.output / "run.json").read_text())
        self.assertFalse(run["task_success"])
        self.assertEqual(run["config"]["observation_mode"], "oracle_3d_waypoints")

    def test_native_response_loop_supports_unbounded_oracle_episode(self):
        from manipulation_agent.agent_loop import LoopConfig, VisualToolLoop
        responses = iter([
            [{"type": "function_call", "call_id": "init", "name": "initialize", "arguments": "{}"}],
            [{"type": "function_call", "call_id": "move", "name": "execute_waypoints",
              "arguments": json.dumps({"arm": "right", "frame": "robot_base",
                                      "waypoints": [waypoint()], "revision": 0})}],
            [{"type": "function_call", "call_id": "done", "name": "finish",
              "arguments": json.dumps({"outcome": "blocked", "reason": "CPU protocol test only"})}],
        ])
        requests = []
        def request(harness, body):
            requests.append(body)
            return {"output": next(responses), "usage": {"total_tokens": 1}}
        VisualToolLoop(model="fake-test-model", instructions=system_prompt("oracle_waypoints"),
                       request=request, config=LoopConfig(max_turns=4)).run(self.harness, "Fixture")
        self.assertTrue(self.harness.closed)
        self.assertEqual(len(self.backend.moves), 1)
        names = {item["name"] for item in requests[0]["tools"]}
        self.assertIn("execute_waypoints", names)
        self.assertNotIn("act", names)


class OracleTaskNotesTests(unittest.TestCase):
    profiles = (OracleWaypointHarness, CompiledOracleHarness)

    def harness(self, cls, root):
        backend = FakeBackend()
        backend.observe = Mock(wraps=backend.observe)
        backend.execute_motor = Mock(wraps=backend.execute_motor)
        options = {"experience_root": root / "empty-experience"}
        if cls is CompiledOracleHarness:
            options["program_root"] = root / "empty-programs"
        return cls(backend, Recorder(root / "episode", {}), Budget(wall_seconds=None), **options)

    def write(self, harness, content, version, request_id):
        return harness.call("task_notes", {"operation": "write", "content": content,
                                          "expected_version": version}, request_id)

    def test_both_profiles_reuse_notes_schema_and_nonphysical_metadata(self):
        shared = next(row for row in all_specs("skills") if row["name"] == "task_notes")
        for cls in self.profiles:
            with self.subTest(profile=cls.PROFILE):
                spec = next(row for row in all_specs(cls.PROFILE) if row["name"] == "task_notes")
                self.assertEqual(spec["inputSchema"], shared["inputSchema"])
                self.assertEqual(spec["annotations"], shared["annotations"])
                self.assertEqual(spec["_meta"]["mas"], {**shared["_meta"]["mas"], "profile": cls.PROFILE})
                self.assertNotIn("task_notes", cls.ACTION_TOOLS)
                spec["inputSchema"]["properties"]["content"]["maxLength"] = 1
                fresh = next(row for row in all_specs(cls.PROFILE) if row["name"] == "task_notes")
                self.assertEqual(fresh["inputSchema"], shared["inputSchema"])

    def test_initialize_and_notes_preserve_physics_revision_actions_and_capture(self):
        for cls in self.profiles:
            with self.subTest(profile=cls.PROFILE), tempfile.TemporaryDirectory() as tmp:
                h = self.harness(cls, Path(tmp))
                initial = h.call("initialize", {}, "init")
                self.assertEqual(initial["notes"], {"version": 0, "content": ""})
                self.assertEqual(h.backend.observe.call_count, 1)
                self.assertTrue(h.call("wait", {"steps": 1, "revision": 0}, "move")["ok"])
                before = (h.backend.steps, h.revision, h.actions, h.agent_steps,
                          h.backend.observe.call_count, h.backend.execute_motor.call_count)
                snapshot = copy.deepcopy(h.snapshot)
                written = self.write(h, "Reached staging pose; inspect contact before grasp.", 0, "plan")
                self.assertTrue(written["ok"], written)
                self.assertEqual(written["notes"]["observation_revision"], 1)
                self.assertEqual(written["notes"]["agent_step"], 1)
                read = h.call("task_notes", {"operation": "read", "content": None,
                                            "expected_version": None}, "read")
                self.assertEqual(read["notes"], written["notes"])
                updated = h.call("initialize", {}, "init-again")
                self.assertEqual(updated["notes"], written["notes"])
                self.assertEqual(updated["observation"], snapshot)
                self.assertEqual(updated["execution_budget"]["scope"], "unbounded")
                self.assertIsNone(updated["execution_budget"]["max_steps"])
                self.assertIsNone(updated["execution_budget"]["wall_time_limit_seconds"])
                self.assertEqual(before, (h.backend.steps, h.revision, h.actions, h.agent_steps,
                                         h.backend.observe.call_count, h.backend.execute_motor.call_count))
                self.assertEqual(h.snapshot, snapshot)

    def test_notes_version_conflicts_and_request_retries_preserve_current_work(self):
        for cls in self.profiles:
            with self.subTest(profile=cls.PROFILE), tempfile.TemporaryDirectory() as tmp:
                h = self.harness(cls, Path(tmp))
                first = self.write(h, "Inspect the can, then choose a grasp.", 0, "first")
                self.assertTrue(first["ok"], first)
                self.assertEqual(first, self.write(h, "Inspect the can, then choose a grasp.", 0, "first"))
                conflict = self.write(h, "Overwrite without reading", 0, "stale")
                self.assertEqual(conflict["error"]["code"], "notes_version_conflict")
                reused = self.write(h, "Different request content", 1, "first")
                self.assertEqual(reused["error"]["code"], "request_id_conflict")
                bad_read = h.call("task_notes", {"operation": "read", "content": "ignored?",
                                                "expected_version": None}, "bad-read")
                self.assertEqual(bad_read["error"]["code"], "invalid_arguments")
                self.assertEqual(h.task_notes.read(), first["notes"])
                second = self.write(h, "Verified grasp; next check is stable placement.", 1, "second")
                self.assertTrue(second["ok"], second)
                self.assertEqual(second["notes"]["version"], 2)
                self.assertEqual(first, self.write(h, "Inspect the can, then choose a grasp.", 0, "first"))
                self.assertEqual(h.task_notes.read(), second["notes"])
                self.assertEqual(json.loads((h.recorder.output / "task_notes.json").read_text()), second["notes"])
                self.assertEqual((h.backend.steps, h.revision, h.actions, h.agent_steps), (0, 0, 0, 0))
                self.assertEqual(h.backend.observe.call_count, 1)
                h.backend.execute_motor.assert_not_called()


class OracleTransportCLITests(unittest.TestCase):
    def test_parser_default_and_hybrid_reach_backend_and_run_config(self):
        from manipulation_agent import vision_cli
        from manipulation_agent.deadline import EpisodeDeadline
        for profile in ("oracle_waypoints", "oracle_compiled"):
            for policy in (None, "hybrid_legacy_navigation"):
                with self.subTest(profile=profile, policy=policy):
                    argv = ["manip-agent", "--output", "/tmp/oracle-cli-smoke-not-created",
                            "--agent-profile", profile, "--ik-backend", "omnigibson"]
                    if policy:
                        argv += ["--oracle-transport-policy", policy]
                    recorder = Mock(run={"status": "passed"})
                    backend, harness = Mock(), Mock()
                    with patch("sys.argv", argv), \
                         patch.object(vision_cli.EpisodeDeadline, "from_env", return_value=EpisodeDeadline()), \
                         patch.object(vision_cli.faulthandler, "enable"), \
                         patch.object(vision_cli.faulthandler, "register"), \
                         patch.object(vision_cli, "Recorder", return_value=recorder) as make_recorder, \
                         patch("manipulation_agent.executors.oracle_waypoints.OracleWaypointBackend", return_value=backend) as make_backend, \
                         patch("manipulation_agent.oracle_harness.OracleWaypointHarness", return_value=harness), \
                         patch("manipulation_agent.compiled_oracle.CompiledOracleHarness", return_value=harness), \
                         patch.object(vision_cli, "startup_stage"), \
                         patch.object(vision_cli.bridge, "serve"):
                        self.assertEqual(vision_cli.main(), 0)
                    expected = policy or "native_only"
                    self.assertEqual(make_backend.call_args.kwargs["oracle_transport_policy"], expected)
                    config = make_recorder.call_args.args[1]
                    self.assertEqual(config["oracle_transport_policy"], expected)
                    self.assertTrue(config["unbounded_episode"])
                    self.assertIsNone(config["wall_seconds"])




class OracleMCPImages(unittest.IsolatedAsyncioTestCase):
    async def test_oracle_tool_returns_verified_rgb_bytes_as_well_as_geometry(self):
        import base64
        import hashlib
        from unittest.mock import patch
        try:
            import mcp
        except ImportError:
            self.skipTest("Install the optional MCP extra")
        from manipulation_agent.mcp_server import create_server
        pixels = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aQAAAAABJRU5ErkJggg==")
        health = json.dumps({"ready": True, "tools": tool_specs()}).encode()
        observation = {"observation_mode": "oracle_3d_waypoints",
                       "oracle_3d": {"fixture": True},
                       "images": [{"image_ref": "test-front", "view": "front",
                                   "mime_type": "image/png",
                                   "sha256": hashlib.sha256(pixels).hexdigest()}]}
        def fetch(url):
            return pixels if "/image/" in url else health
        with patch("manipulation_agent.mcp_server.fetch", side_effect=fetch), \
             patch("manipulation_agent.mcp_server.rpc",
                   return_value={"ok": True, "observation": observation}):
            server = await create_server("http://127.0.0.1:1", oracle_payload="full")
            blocks = await server.call_tool("initialize", {})
        self.assertTrue(any(getattr(block, "type", None) == "image" for block in blocks))
        text = next(block.text for block in blocks if getattr(block, "type", None) == "text")
        self.assertEqual(json.loads(text)["observation"]["oracle_3d"], {"fixture": True})




class KinematicLayoutRegression(unittest.TestCase):
    def test_fixed_and_floating_articulations_have_different_link_row_offsets(self):
        from manipulation_agent.oracle_contracts import jacobian_row_index
        self.assertEqual(jacobian_row_index(12, 30, 29), 11)
        self.assertEqual(jacobian_row_index(12, 30, 30), 12)
        with self.assertRaises(SkillError):
            jacobian_row_index(12, 30, 28)

    def test_orientation_error_handles_pi_and_equivalent_quaternion_signs(self):
        import math
        from manipulation_agent.oracle_contracts import rotation_error_vector
        self.assertEqual(rotation_error_vector([0,0,0,-1], [0,0,0,1]), [0,0,0])
        error = rotation_error_vector([0,0,1,0], [0,0,0,1])
        self.assertAlmostEqual(error[2], math.pi)
        error = rotation_error_vector([0,0,math.sqrt(.5),math.sqrt(.5)], [0,0,0,1])
        self.assertAlmostEqual(error[2], math.pi/2)


class ExistingNavigationHandoffTests(unittest.TestCase):
    def backend(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        backend = object.__new__(OracleWaypointBackend)
        backend.oracle_transport_policy = "native_only"
        backend._navigation_active = False
        backend._base_target = None
        backend._command_q = "old servo targets"
        backend.robot = SimpleNamespace(get_joint_positions=Mock(
            return_value=SimpleNamespace(clone=lambda: "measured post-navigation joints")))
        return backend

    def test_native_transport_refuses_either_held_arm_before_navigation(self):
        from types import SimpleNamespace
        for arm in ("left", "right"):
            with self.subTest(arm=arm):
                backend = self.backend()
                backend.robot._ag_obj_in_hand = {arm: SimpleNamespace(name="can")}
                with patch("manipulation_agent.executors.oracle_waypoints.RGBBackend.execute_visual") as legacy:
                    with self.assertRaises(SkillError) as error:
                        backend._navigate_to({"image_ref": "front", "point": [.4, .7]})
                    self.assertEqual(error.exception.code, "held_navigation_disabled")
                    legacy.assert_not_called()
                self.assertFalse(backend._navigation_active)
                self.assertEqual(backend._command_q, "old servo targets")

    def test_hybrid_transport_explicitly_allows_and_labels_ideal_held_projection(self):
        from types import SimpleNamespace
        backend = self.backend()
        backend.oracle_transport_policy = "hybrid_legacy_navigation"
        backend.robot._ag_obj_in_hand = {"right": SimpleNamespace(name="can")}
        with patch("manipulation_agent.executors.oracle_waypoints.RGBBackend.execute_visual",
                   return_value={"nav_status": "approached"}) as legacy:
            result = backend._navigate_to({"image_ref": "front", "point": [.4, .7]})
        legacy.assert_called_once()
        self.assertEqual(result["transport_policy"], "hybrid_legacy_navigation")
        self.assertEqual(result["transport_execution"], "ideal_held_object_projection")
        self.assertEqual(result["execution_phase"], "legacy_navigation")
        self.assertFalse(backend._navigation_active)

    def test_control_contract_reports_transport_policy_and_projection(self):
        backend = self.backend()
        backend._wrist_camera_contract = Mock(return_value={})
        backend.oracle_grasping_mode = "assisted"
        backend.waypoint_max_steps = 180
        backend.ik_backend = "omnigibson"
        for policy in ("native_only", "hybrid_legacy_navigation"):
            with self.subTest(policy=policy):
                backend.oracle_transport_policy = policy
                contract = backend.control_contract()
                self.assertEqual(contract["transport_policy"], policy)
                self.assertEqual(contract["navigation"]["held_navigation_allowed"], policy != "native_only")
                self.assertEqual(contract["navigation"]["held_object_pose_projection"], policy != "native_only")

    def test_unknown_transport_policy_is_rejected_before_simulator_initialization(self):
        with patch("manipulation_agent.executors.oracle_waypoints.RGBBackend.__init__") as initialize:
            with self.assertRaises(ValueError):
                OracleWaypointBackend(oracle_transport_policy="implicit_teleport")
            initialize.assert_not_called()

    def test_profile_reuses_navigation_instead_of_base_velocity_tool(self):
        names = {row["name"] for row in tool_specs()}
        self.assertIn("navigate_to", names)
        self.assertNotIn("move_base", names)
        schema = next(row["inputSchema"] for row in tool_specs() if row["name"] == "navigate_to")
        validate({"target": {"image_ref": "front-current", "point": [.4, .7]}, "revision": 0}, schema)
        with self.assertRaises(SkillError):
            validate({"target": {"image_ref": "front-current", "point": [1.2, .7]}, "revision": 0}, schema)

    def test_navigation_calls_existing_executor_and_resumes_measured_joint_targets(self):
        from unittest.mock import patch
        backend = self.backend()
        target = {"image_ref": "front-current", "point": [.4, .7]}
        def legacy(instance, primitive, actual_target, max_steps, **kwargs):
            self.assertIs(instance, backend)
            self.assertTrue(instance._navigation_active)
            self.assertEqual((primitive, actual_target, max_steps), ("navigate_to", target, 700))
            return {"nav_status": "approached", "steps": 12}
        with patch("manipulation_agent.executors.oracle_waypoints.RGBBackend.execute_visual", new=legacy):
            result = backend._navigate_to(target)
        self.assertTrue(result["reached"])
        self.assertFalse(result["manipulation_performed"])
        self.assertEqual(backend._command_q, "measured post-navigation joints")
        self.assertFalse(backend._navigation_active)
        self.assertIsNone(backend._base_target)

    def test_navigation_error_clears_projection_phase_and_old_servo_targets(self):
        from unittest.mock import patch
        backend = self.backend()
        backend._base_target = {"partial": True}
        with patch("manipulation_agent.executors.oracle_waypoints.RGBBackend.execute_visual",
                   side_effect=SkillError("navigation_unreachable", "partial move", changed=True)):
            with self.assertRaises(SkillError):
                backend._navigate_to({"image_ref": "front-current", "point": [.4, .7]})
        self.assertFalse(backend._navigation_active)
        self.assertIsNone(backend._base_target)
        self.assertEqual(backend._command_q, "measured post-navigation joints")

    def test_original_semantic_manipulation_remains_inaccessible(self):
        from unittest.mock import patch
        backend = self.backend()
        with patch("manipulation_agent.executors.oracle_waypoints.RGBBackend.execute_visual") as legacy:
            for active, primitive in [(False, "navigate_to"), (False, "grasp"),
                                      (True, "grasp"), (True, "place_inside")]:
                backend._navigation_active = active
                with self.assertRaises(SkillError):
                    backend.execute_visual(primitive, {}, 700)
            legacy.assert_not_called()

    def test_native_step_does_not_project_and_navigation_preserves_native_hold(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        backend = self.backend()
        backend.deadline = SimpleNamespace(check=Mock())
        backend._motor_ready = False
        backend._base_target = {"legacy": "target"}
        backend._restore_base_target = Mock()
        hold = object()
        backend._hold_action = Mock(return_value=hold)
        backend._video_frame = Mock()
        with patch("manipulation_agent.executors.oracle_waypoints.OmniGibsonBackend._step") as step:
            native = object()
            backend._step(native)
            backend._restore_base_target.assert_not_called()
            backend._hold_action.assert_not_called()
            step.assert_called_once_with(backend, native)
            step.reset_mock()
            backend._navigation_active = True
            backend._step(object())
            self.assertEqual(backend._restore_base_target.call_count, 2)
            backend._hold_action.assert_called_once()
            step.assert_called_once_with(backend, hold)

    def test_navigation_request_retry_and_failure_refresh_use_existing_harness_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend()
            harness = OracleWaypointHarness(backend, Recorder(Path(directory)/"episode", {}),
                                             Budget(wall_seconds=None))
            arguments = {"target": {"image_ref": "front-current", "point": [.4, .7]}, "revision": 0}
            result = harness.call("navigate_to", arguments, "navigation-1")
            self.assertTrue(result["ok"])
            self.assertEqual(harness.call("navigate_to", arguments, "navigation-1"), result)
            self.assertEqual(len(backend.moves), 1)
            self.assertEqual(result["observation"]["revision"], 1)
            self.assertEqual(harness.call("navigate_to", arguments, "stale")["error"]["code"],
                             "stale_observation")
            backend.fail = True
            arguments["revision"] = 1
            failed = harness.call("navigate_to", arguments, "partial-navigation")
            self.assertFalse(failed["ok"])
            self.assertTrue(failed["error"]["world_may_have_changed"])
            self.assertEqual(failed["observation"]["revision"], 2)


if __name__ == "__main__":
    unittest.main()
