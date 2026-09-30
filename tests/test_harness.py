import json
import tempfile
import threading
import unittest
from pathlib import Path

from manipulation_agent.contracts import Budget, tool_specs, validate
from manipulation_agent.fake_backend import FakeBackend
from manipulation_agent.harness import Harness
from manipulation_agent.policies import scripted_episode
from manipulation_agent.records import Recorder


class HarnessContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend()
        self.recorder = Recorder(Path(self.temp.name) / "episode", {"validation_level": "mock"})
        self.h = Harness(self.backend, self.recorder)
        self.i = 0

    def call(self, name, **args):
        self.i += 1
        return self.h.call(name, args, str(self.i))

    def act(self, skill, target=None, revision=None):
        return self.call("act", skill=skill, target=target, revision=self.h.revision if revision is None else revision)

    def test_task_success_is_not_finish_claim(self):
        result = self.call("finish", outcome="achieved", reason="I say so")
        self.assertTrue(result["closed"])
        self.assertNotIn("evaluation", result)
        self.assertFalse(self.recorder.run["task_success"])

    def test_complete_loop_writes_evaluation_and_html(self):
        scripted_episode(self.h, "turning_on_radio")
        self.assertTrue(self.recorder.run["task_success"])
        self.assertEqual(self.backend.steps, 2)
        self.assertTrue((self.recorder.output / "index.html").exists())
        events = [json.loads(x) for x in (self.recorder.output / "events.jsonl").read_text().splitlines()]
        self.assertEqual(len([x for x in events if x["kind"] == "independent_evaluation"]), 1)

    def test_stale_revision_rejected_without_world_change(self):
        self.act("navigate_to", "radio.n.01_1")
        result = self.act("toggle_on", "radio.n.01_1", revision=0)
        self.assertEqual(result["error"]["code"], "stale_observation")
        self.assertEqual(self.backend.steps, 1)

    def test_idempotent_replay_does_not_repeat_physics(self):
        args = {"skill": "navigate_to", "target": "radio.n.01_1", "revision": 0}
        one = self.h.call("act", args, "replay")
        two = self.h.call("act", args, "replay")
        self.assertEqual(one, two)
        self.assertEqual(self.backend.steps, 1)
        args["target"] = "apple.n.01_1"
        self.assertEqual(self.h.call("act", args, "replay")["error"]["code"], "request_id_conflict")

    def test_failed_execution_invalidates_old_observation(self):
        self.backend.fail_next = True
        result = self.act("navigate_to", "radio.n.01_1")
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"]["world_may_have_changed"])
        self.assertEqual(result["observation"]["revision"], 1)

    def test_unknown_object_cannot_reach_backend(self):
        self.assertEqual(self.act("grasp", "fabricated")["error"]["code"], "unknown_object")
        self.assertEqual(self.backend.steps, 0)

    def test_cannot_grasp_distant_object(self):
        self.assertEqual(self.act("grasp", "radio.n.01_1")["error"]["code"], "out_of_reach")

    def test_closed_container_requires_open_before_grasp(self):
        self.act("grasp", "apple.n.01_1")
        self.assertEqual(self.act("place_inside", "cabinet.n.01_1")["error"]["code"], "container_closed")
        self.assertEqual(self.act("open", "cabinet.n.01_1")["error"]["code"], "hand_occupied")

    def test_action_budget_is_hard_limit(self):
        self.h.budget = Budget(max_actions=1)
        self.act("navigate_to", "radio.n.01_1")
        self.assertEqual(self.act("toggle_on", "radio.n.01_1")["error"]["code"], "budget_exhausted")
        self.assertEqual(self.backend.steps, 1)

    def test_physics_budget_is_forwarded(self):
        self.h.budget = Budget(max_sim_steps=1)
        self.act("navigate_to", "radio.n.01_1")
        self.assertEqual(self.act("wait")["error"]["code"], "budget_exhausted")

    def test_no_actions_after_finish(self):
        self.call("finish", outcome="aborted", reason="test")
        self.assertEqual(self.act("wait")["error"]["code"], "episode_closed")

    def test_plan_cannot_invent_done_evidence(self):
        plan = [{"id": "one", "description": "done", "dependencies": [], "status": "done", "evidence": "fake"}]
        self.assertEqual(self.call("update_plan", reason="test", subgoals=plan)["error"]["code"], "unverified_subgoal")

    def test_plan_cycles_rejected(self):
        plan = [{"id": k, "description": k, "dependencies": [d], "status": "pending", "evidence": None} for k, d in [("a", "b"), ("b", "a")]]
        self.assertEqual(self.call("update_plan", reason="test", subgoals=plan)["error"]["code"], "invalid_plan")

    def test_memory_keeps_observation_provenance(self):
        self.call("remember", key="fact", text="radio is off", revision=0)
        self.act("navigate_to", "radio.n.01_1")
        self.assertEqual(self.call("recall")["memory"]["fact"]["revision"], 0)
        self.assertEqual(self.call("remember", key="fact", text="stale", revision=0)["error"]["code"], "stale_observation")

    def test_invalid_schema_and_bool_revision_rejected(self):
        self.assertEqual(self.act("wait", revision=True)["error"]["code"], "invalid_arguments")
        self.assertEqual(self.call("observe", extra="x")["error"]["code"], "invalid_arguments")

    def test_thread_owner_is_enforced(self):
        errors = []
        def work():
            try:
                self.call("observe")
            except RuntimeError as exc:
                errors.append(str(exc))
        t = threading.Thread(target=work)
        t.start(); t.join()
        self.assertEqual(len(errors), 1)

    def test_observe_does_not_advance_physics(self):
        one = self.call("observe")
        two = self.call("observe")
        self.assertEqual(one["observation"], two["observation"])
        self.assertEqual(self.backend.steps, 0)


if __name__ == "__main__":
    unittest.main()
