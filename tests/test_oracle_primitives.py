"""CPU operation orchestration tests, never simulator/model success evidence."""
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from manipulation_agent.contracts import SkillError, validate, Budget
from manipulation_agent.oracle_operations import operation_specs, checked_paths, joint_goal
from manipulation_agent.executors.oracle_primitives import OracleManipulationPrimitives
from manipulation_agent.oracle_harness import OracleWaypointHarness
from manipulation_agent.records import Recorder
from test_oracle_waypoints import FakeBackend


class Vec(list):
    def __sub__(self, other):
        return Vec(a-b for a,b in zip(self, other))
    def norm(self):
        return math.sqrt(sum(x*x for x in self))


def pose(z):
    return {"position_m": [.4, -.2, z], "quaternion_xyzw": [0.,0.,0.,1.], "gripper": "hold"}


def grasp_args():
    return {"arm": "right", "frame": "robot_base", "allow_trunk": True,
            "target_object_id": "can", "target_link": "base_link", "grasp_kind": "free_object",
            "pregrasp_waypoints": [pose(.3)], "approach_waypoints": [pose(.1)],
            "verification_waypoints": [pose(.18)], "settle_steps": 4, "min_lift_m": .05}


class FakePrimitives(OracleManipulationPrimitives):
    def __init__(self):
        self.events = []
        self.positions = {"can": Vec([.4,-.2,.1]), "destination": Vec([.4,-.2,0])}
        self.objects = {}
        for name in self.positions:
            obj = SimpleNamespace(name=name, fixed_base=False, joints={})
            obj.get_position_orientation = lambda name=name: (self.positions[name], None)
            link = SimpleNamespace(name="base_link", get_position_orientation=obj.get_position_orientation)
            obj.links = {"base_link": link}
            self.objects[name] = obj
        self.eef_position = Vec([.4,-.2,.4])
        self.robot = SimpleNamespace(eef_links={"right": SimpleNamespace(
            get_position_orientation=lambda: (self.eef_position, None))},
            finger_links={"right": [SimpleNamespace(name="robot_r1:finger1",prim_path="/robot/finger1"),SimpleNamespace(name="robot_r1:finger2",prim_path="/robot/finger2")]})
        self.held = None
        self._gripper_commands = {"right": 1.}
        self.oracle_grasping_mode = "assisted"
        self.fail_phase = None
        self.miss = False
        self.relation = True
        self.stable = True
        self.joint_actual = 0.
        self.joint_end = .1
        self.toggle = SimpleNamespace(value=False, link=SimpleNamespace(name="toggle"))
        self.toggle.get_value = lambda: self.toggle.value
        self.press_changes = True

    def _resolve_object(self, name):
        return self.objects[name]
    def _resolve_link(self, obj, name):
        return obj.links[name]
    def _link_key(self, obj, link):
        return next(name for name,item in obj.links.items() if item is link)
    def _phase(self, name):
        self._primitive_context["phase"] = name
        self.events.append(name)
    def _is_articulated_handle(self, obj, link):
        return getattr(self, "is_handle", False)
    def _grasp_state(self, arm):
        return {"object_id": self.held, "target_link": "base_link", "mode": "assisted"}
    def _retention(self, arm, obj, link):
        return {"retained": self.held == obj.name}
    def _release_evidence(self, arm, obj):
        return {"released": self.held != obj.name}
    def _list(self, value):
        return list(value)
    def _relative(self, pose, reference):
        return (pose[0]-reference[0], None)
    def _set_gripper(self, arm, command):
        self.events.append("gripper:"+command)
        self._gripper_commands[arm] = 1. if command=="open" else -1.
        self.held = None if command=="open" or self.miss else "can"
        return {"reached": True}
    def _step(self, action):
        pass
    def _hold_action(self):
        return None
    def _stable_object(self, obj, steps):
        return {"stable": self.stable, "position_drift_m": 0. if self.stable else .1}
    def _relation(self, obj, dest, relation):
        return self.relation
    def _native_toggle(self, obj):
        return self.toggle, False
    def _joint_description(self, obj, name, joint):
        return {"type": joint.kind, "position": self.joint_actual, "lower_limit": 0.,
                "upper_limit": .5, "parent_link": "base_link"}
    def execute_motor(self, name, arm, frame, waypoints):
        phase = self._primitive_context["phase"]
        if phase == self.fail_phase:
            return {"reached": False, "waypoints": []}
        old = self.eef_position
        self.eef_position = Vec(waypoints[-1]["position_m"])
        if self.held and not self.objects[self.held].joints:
            self.positions[self.held] = Vec(a+b-c for a,b,c in zip(
                self.positions[self.held], self.eef_position, old))
        if phase == "joint_motion":
            self.joint_actual = self.joint_end
        if phase == "press_approach" and self.press_changes:
            self.toggle.value = True
            self._primitive_context["contact_links_seen"].add("finger1")
            self._primitive_context["contact_seen"] = True
        return {"reached": True, "waypoints": []}


class PrimitiveContracts(unittest.TestCase):
    def test_six_schemas_and_model_authored_paths(self):
        specs = operation_specs()
        self.assertEqual({r["name"] for r in specs},
                         {"grasp","place","translate","rotate","press","release"})
        args = grasp_args()
        schema = next(r["inputSchema"] for r in specs if r["name"]=="grasp")
        validate({**args, "revision": 0}, schema)
        for boolean in [True, False]:
            validate(boolean, {"type": "boolean"})
        for invalid in [0, 1, "true", None]:
            with self.assertRaises(SkillError):
                validate(invalid, {"type": "boolean"})
        with self.assertRaises(SkillError):
            validate({"arm":"right","target_object_id":"can","revision":0}, schema)

    def test_joint_limits_and_too_small_progress_rejected_before_motion(self):
        self.assertAlmostEqual(joint_goal(.1,.2,0.,.5,"prismatic"), .3)
        for values in [(.4,.2,0.,.5),(.1,0.,0.,.5),(.1,.1,None,.5),(.1,float("nan"),0.,.5)]:
            with self.assertRaises(SkillError):
                joint_goal(*values,"prismatic")

    def test_whole_operation_validation_precedes_gripper_open(self):
        fake = FakePrimitives()
        args = grasp_args()
        args["verification_waypoints"][0]["quaternion_xyzw"]=[0.,0.,0.,0.]
        with self.assertRaises(SkillError):
            fake.execute_primitive("grasp", **args)
        self.assertEqual(fake.events, [])

    def test_paths_cannot_hide_early_release(self):
        args = grasp_args()
        args["approach_waypoints"][0]["gripper"]="open"
        with self.assertRaises(SkillError):
            checked_paths(args)


class PrimitiveExecution(unittest.TestCase):
    def test_grasp_requires_actual_object_lift_and_retention(self):
        fake = FakePrimitives()
        result = fake.execute_primitive("grasp", **grasp_args())
        self.assertTrue(result["verified"])
        self.assertGreaterEqual(result["lifted_m"], .05)
        self.assertIsNone(fake._primitive_context)

    def test_reached_hand_without_target_retention_does_not_lift(self):
        fake = FakePrimitives()
        fake.miss = True
        result = fake.execute_primitive("grasp", **grasp_args())
        self.assertFalse(result["verified"])
        self.assertNotIn("verification_motion", fake.events)

    def test_unreached_approach_never_closes_gripper(self):
        fake = FakePrimitives()
        fake.fail_phase = "approach"
        result = fake.execute_primitive("grasp", **grasp_args())
        self.assertFalse(result["reached"])
        self.assertNotIn("gripper:close", fake.events)

    def test_handle_verification_does_not_require_upward_lift(self):
        fake = FakePrimitives()
        fake.is_handle = True
        args = grasp_args()
        args["grasp_kind"]="articulated_handle"
        args["verification_waypoints"]=[pose(.1)]
        self.assertTrue(fake.execute_primitive("grasp", **args)["verified"])

    def place(self, fake):
        fake.held="can"
        return fake.execute_primitive("place", arm="right", frame="robot_base", allow_trunk=False,
            target_object_id="can", destination_object_id="destination", relation="inside",
            placement_waypoints=[pose(.1)], retreat_waypoints=[pose(.3)], settle_steps=4)

    def test_place_verifies_relation_and_stability_after_release(self):
        fake=FakePrimitives()
        self.assertTrue(self.place(fake)["verified"])
        fake=FakePrimitives();fake.relation=False
        self.assertFalse(self.place(fake)["verified"])
        fake=FakePrimitives();fake.stable=False
        self.assertFalse(self.place(fake)["verified"])

    def test_failed_placement_does_not_release(self):
        fake=FakePrimitives();fake.fail_phase="placement"
        self.assertFalse(self.place(fake)["reached"])
        self.assertNotIn("gripper:open",fake.events)

    def test_missing_relation_evidence_is_unknown(self):
        fake=FakePrimitives();fake.relation=None
        result=self.place(fake)
        self.assertIsNone(result["verified"])
        self.assertEqual(result["verification_status"],"unknown")

    def test_joint_motion_success_requires_actual_joint_not_hand_motion(self):
        for operation,kind,delta_name in [("translate","prismatic","displacement_m"),
                                          ("rotate","revolute","angle_rad")]:
            fake=FakePrimitives();fake.held="can"
            fake.objects["can"].joints["joint"]=SimpleNamespace(kind=kind)
            args=dict(arm="right",frame="robot_base",allow_trunk=False,target_object_id="can",
                      target_link="base_link",joint_id="joint",motion_waypoints=[pose(.2)],
                      release_after=False,settle_steps=3)
            args[delta_name]=.1
            self.assertTrue(fake.execute_primitive(operation,**args)["verified"])
            fake.joint_actual=0.;fake.joint_end=0.
            self.assertFalse(fake.execute_primitive(operation,**args)["verified"])

    def test_button_needs_native_state_transition_and_declared_finger(self):
        fake=FakePrimitives()
        args=dict(arm="right",frame="robot_base",allow_trunk=False,target_object_id="can",
                  functional_region="toggle",contact_link="finger1",press_waypoints=[pose(.1)],
                  dwell_steps=6,retreat_waypoints=[pose(.3)],desired_local_state=True)
        self.assertTrue(fake.execute_primitive("press",**args)["verified"])
        fake=FakePrimitives();fake.press_changes=False
        self.assertFalse(fake.execute_primitive("press",**args)["verified"])
        fake=FakePrimitives();args["contact_link"]="finger2"
        self.assertFalse(fake.execute_primitive("press",**args)["verified"])

    def test_already_correct_button_state_does_not_toggle_again(self):
        fake=FakePrimitives();fake.toggle.value=True
        result=fake.execute_primitive("press",arm="right",frame="robot_base",allow_trunk=False,
            target_object_id="can",functional_region="toggle",contact_link="finger1",
            press_waypoints=[pose(.1)],dwell_steps=6,retreat_waypoints=[pose(.3)],
            desired_local_state=True)
        self.assertTrue(result["verified"])
        self.assertEqual(fake.events,[])


class VerificationBoundary(unittest.TestCase):
    def test_motion_reached_does_not_hide_failed_or_unknown_verification(self):
        for value,code in [(False,"primitive_verification_failed"),(None,"primitive_verification_unavailable")]:
            with tempfile.TemporaryDirectory() as directory:
                backend=FakeBackend()
                backend.execute_motor=lambda name,**kwargs: {"reached":True,"verified":value}
                harness=OracleWaypointHarness(backend,Recorder(Path(directory)/"run",{}),Budget(wall_seconds=None))
                result=harness.call("release",{"arm":"right","frame":"robot_base","allow_trunk":False,
                    "target_object_id":"can","settle_steps":3,"revision":0},"test")
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"],code)
                self.assertEqual(result["observation"]["revision"],1)

    def test_video_phase_marker_preserves_tool_and_native_caption(self):
        from manipulation_agent.video import EpisodeVideo
        with tempfile.TemporaryDirectory() as directory:
            video=EpisodeVideo(Path(directory),30,execution_caption="native manipulation")
            video.mark("grasp",{},"request")
            video.mark_phase("approach")
            self.assertEqual(video.context["tool"],"grasp")
            self.assertEqual(video.markers[-1]["phase"],"approach")
            self.assertEqual(video.manifest["execution_caption"],"native manipulation")


class CanonicalLinkTests(unittest.TestCase):
    def test_assisted_hold_matches_object_dictionary_key_not_prefixed_display_name(self):
        from manipulation_agent.executors.oracle_geometry import OracleGeometry
        class Geometry(OracleGeometry):
            oracle_grasping_mode="assisted"
            def _grasp_state(self, arm):
                return {"object_id":"can","target_link":"base_link"}
            def _contacts(self, *args):
                return {"available":True,"pairs":[],"finger_count":0}
        link=SimpleNamespace(name="can:base_link",prim_path="/can/base_link")
        obj=SimpleNamespace(name="can",links={"base_link":link})
        self.assertTrue(Geometry()._retention("right",obj,link)["retained"])


class NativeNavigationSerialization(unittest.TestCase):
    def test_real_legacy_follower_accepts_binary_gripper_action_adapter(self):
        from unittest.mock import Mock
        from test_forward_backend import ForwardBackendTests
        from manipulation_agent.executors.oracle_waypoints import OracleWaypointBackend
        fixture=ForwardBackendTests(methodName="test_adapter_turns_to_path_and_restores_endpoint_view")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        backend=fixture.b
        backend.__class__=OracleWaypointBackend
        backend._hold_action=Mock(return_value=fixture.torch.zeros(5))
        backend.robot.q_to_action=Mock(side_effect=AssertionError("binary gripper cannot serialize q"))
        result=backend._execute_gt_plan(fixture.grid,fixture.plan(),500)
        self.assertLess(result["final_position_error_m"],.002)
        self.assertGreater(backend._hold_action.call_count,0)
        backend.robot.q_to_action.assert_not_called()


class ContactActivationBoundary(unittest.TestCase):
    def test_native_button_activation_is_reported_even_when_pose_target_is_unreached(self):
        fake=FakePrimitives()
        def partial_press(name, **kwargs):
            fake.toggle.value=True
            fake._primitive_context["contact_links_seen"].add("finger1")
            return {"reached":False,"waypoints":[]}
        fake.execute_motor=partial_press
        result=fake.execute_primitive("press",arm="right",frame="robot_base",allow_trunk=False,
            target_object_id="can",functional_region="toggle",contact_link="finger1",
            press_waypoints=[pose(.1)],dwell_steps=6,retreat_waypoints=[pose(.3)],desired_local_state=True)
        self.assertFalse(result["reached"])
        self.assertTrue(result["verified"])
        self.assertNotIn("retreat",fake.events)


class GraspKindBoundary(unittest.TestCase):
    def test_free_object_cannot_bypass_lift_verification_by_claiming_to_be_a_handle(self):
        fake=FakePrimitives()
        args=grasp_args()
        args["grasp_kind"]="articulated_handle"
        with self.assertRaises(SkillError) as error:
            fake.execute_primitive("grasp",**args)
        self.assertEqual(error.exception.code,"invalid_grasp_kind")
        self.assertEqual(fake.events,[])


class PhaseToleranceContract(unittest.TestCase):
    def test_transit_does_not_require_contact_precision_but_final_contact_does(self):
        from manipulation_agent.oracle_operations import waypoint_tolerances
        self.assertEqual(waypoint_tolerances("pregrasp",0,1),(.008,.06))
        self.assertEqual(waypoint_tolerances("press_approach",0,3),(.008,.06))
        self.assertEqual(waypoint_tolerances("press_approach",2,3),(.004,.04))
        self.assertEqual(waypoint_tolerances("approach",1,2),(.004,.04))
        self.assertEqual(waypoint_tolerances("retreat",0,1),(.008,.06))
