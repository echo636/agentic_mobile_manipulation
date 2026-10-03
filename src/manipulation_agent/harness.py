from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import asdict

from .contracts import Backend, Budget, SkillError, tool_specs, validate
from .deadline import EpisodeDeadline
from .records import Recorder, write_json


class Harness:
    """Single-owner episode; all front ends use the same validation and state transitions."""

    def __init__(self, backend: Backend, recorder: Recorder, budget: Budget = Budget()):
        self.backend, self.recorder, self.budget = backend, recorder, budget
        self.owner = threading.get_ident()
        self.started = time.monotonic()
        self.deadline = getattr(backend, 'deadline', None) or EpisodeDeadline.from_env()
        backend.deadline = self.deadline
        self.revision = self.actions = self.calls = 0
        self.closed = False
        self.plan, self.memory, self.cache, self.evidence = [], {}, {}, {}
        self.schemas = {s["name"]: s["inputSchema"] for s in tool_specs()}
        self.snapshot = self._observe()
        recorder.run.update(backend=backend.provenance(), budget=asdict(budget))
        write_json(recorder.output / "run.json", recorder.run)
        recorder.event("episode_started", {"observation": self.snapshot})

    def _observe(self) -> dict:
        return {**self.backend.observe(), "revision": self.revision,
                "observation_mode": self.backend.mode, "sim_steps": self.backend.steps}

    def sync_execution_clock(self):
        deadline=self.deadline.unix
        if deadline is None or getattr(self,'_clock_recorded',False):return
        self._clock_recorded=True
        timing={'episode_deadline_unix':deadline}
        clock=self.deadline.clock
        if clock:
            # Existing observation-job deadlines and wall-time diagnostics share
            # the execution origin too; simulator construction is excluded.
            self.started=time.monotonic()-max(0,time.time()-clock['execution_started_at_unix'])
            timing.update(execution_started_at_unix=clock['execution_started_at_unix'],
                          execution_budget_seconds=clock['execution_budget_seconds'],
                          execution_clock_kind=clock.get('clock_kind','policy_execution'))
        self.recorder.run.update(timing)
        self.recorder.event('execution_clock_armed',timing)
        write_json(self.recorder.output/'run.json',self.recorder.run)

    def start_standalone_clock(self):
        if not self.deadline.managed:
            self.started=time.monotonic()
            self.deadline.arm_local(self.budget.wall_seconds)
        self.sync_execution_clock()

    def call(self, name: str, arguments: dict, request_id: str) -> dict:
        if threading.get_ident() != self.owner:
            raise RuntimeError("Simulator access must stay on its owning thread")
        fingerprint = json.dumps([name, arguments], sort_keys=True, allow_nan=False)
        if request_id in self.cache:
            previous, result = self.cache[request_id]
            if previous != fingerprint:
                return {"ok": False, "error": {"code": "request_id_conflict", "message": "Use a new ID for a different request"}}
            return copy.deepcopy(result)
        if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
            return {"ok": False, "error": {"code": "invalid_request_id", "message": "A bounded nonempty request ID is required"}}
        self.calls += 1
        self.sync_execution_clock()
        self.recorder.event("tool_call", {"name": name, "arguments": arguments, "request_id": request_id})
        try:
            if self.closed:
                raise SkillError("episode_closed", "This episode has already ended")
            if name not in self.schemas:
                raise SkillError("unknown_tool", "Tool is not in this episode's catalog")
            validate(arguments, self.schemas[name])
            if name != 'finish': self.deadline.check()
            if name != "finish" and (self.calls > self.budget.max_calls or
                    (not self.deadline.managed and time.monotonic() - self.started > self.budget.wall_seconds)):
                raise SkillError("budget_exhausted", "Call or wall-clock budget exhausted; finish the episode")
            result = {"ok": True, **getattr(self, f"_tool_{name}")(**arguments)}
        except SkillError as exc:
            result = {"ok": False, "error": {"code": exc.code, "message": str(exc), "world_may_have_changed": exc.changed},
                      "observation": self.snapshot}
        event_id = self.recorder.event("tool_result", {"name": name, "request_id": request_id, "result": result})
        result["evidence_id"] = event_id
        self.evidence[event_id] = {"name": name, "ok": result["ok"], "revision": self.revision}
        self.cache[request_id] = (fingerprint, copy.deepcopy(result))
        if self.closed:
            self.recorder.render()
        return result

    def _tool_observe(self) -> dict:
        self.snapshot = self._observe()
        return {"observation": self.snapshot}

    def _tool_act(self, skill: str, target: str | None, revision: int) -> dict:
        self.deadline.check()
        if revision != self.revision:
            raise SkillError("stale_observation", "Use the revision returned by the latest observation")
        if self.actions >= self.budget.max_actions or self.backend.steps >= self.budget.max_sim_steps:
            raise SkillError("budget_exhausted", "Action or physics-step budget exhausted")
        no_target = skill in {"release", "wait"}
        if no_target != (target is None):
            raise SkillError("invalid_target", "release/wait require null target; other skills require an object ID")
        objects = {o["id"]: o for o in self.snapshot["objects"]}
        if target is not None and target not in objects:
            raise SkillError("unknown_object", "Target must be an object in the current observation")
        held = self.snapshot.get("held_object")
        if skill == "grasp" and held is not None and held != target:
            raise SkillError("hand_occupied", "Place or release the held object first")
        if skill == "grasp":
            if objects[target].get("graspable") is False:
                raise SkillError("fixed_object", "This object is fixed in the scene and cannot be grasped")
            for parent in objects[target].get("relations", {}).get("inside", []):
                if objects.get(parent, {}).get("states", {}).get("open") is False:
                    raise SkillError("container_closed", "Open the containing object before grasping its contents")
        if skill in {"place_inside", "place_on_top", "release"} and held is None:
            raise SkillError("empty_hand", "No object is being held")
        if skill in {"open", "close", "toggle_on", "toggle_off"} and held is not None:
            raise SkillError("hand_occupied", "This executor requires an empty default hand")
        if skill == "place_inside" and objects[target].get("states", {}).get("open") is False:
            raise SkillError("container_closed", "Open the target container before grasping the item")
        if skill not in {"navigate_to", "release", "wait"}:
            distance = objects[target].get("distance_m")
            if distance is None or distance > 1.6:
                raise SkillError("out_of_reach", "Navigate to the target before manipulating it")
        self.actions += 1
        limit = min(self.budget.max_steps_per_action, self.budget.max_sim_steps - self.backend.steps)
        try:
            effect = self.backend.execute(skill, target, limit)
        finally:
            # Failed primitives can partially move objects. Never keep their old observation valid.
            self.revision += 1
            self.snapshot = self._observe()
        return {"effect": effect, "observation": self.snapshot, "actions_used": self.actions}

    def _tool_update_plan(self, reason: str, subgoals: list[dict]) -> dict:
        by_id = {g["id"]: g for g in subgoals}
        if len(by_id) != len(subgoals) or any(not k for k in by_id):
            raise SkillError("invalid_plan", "Subgoal IDs must be unique and nonempty")
        visited = set()
        def visit(key, trail):
            if key in trail:
                raise SkillError("invalid_plan", "Plan dependencies contain a cycle")
            if key not in by_id:
                raise SkillError("invalid_plan", "Plan references an unknown dependency")
            if key in visited:
                return
            for parent in by_id[key]["dependencies"]:
                visit(parent, trail | {key})
            visited.add(key)
        for key, goal in by_id.items():
            visit(key, set())
            if goal["status"] == "done":
                evidence = self.evidence.get(goal["evidence"], {})
                if not evidence.get("ok") or evidence.get("name") != "act":
                    raise SkillError("unverified_subgoal", "A done claim requires a successful action evidence ID")
                if any(by_id[d]["status"] != "done" for d in goal["dependencies"]):
                    raise SkillError("invalid_plan", "Done goal has unfinished dependencies")
        self.plan = copy.deepcopy(subgoals)
        return {"plan": self.plan, "reason": reason, "verification": "agent_claims_with_action_evidence"}

    def _tool_remember(self, key: str, text: str, revision: int) -> dict:
        if revision != self.revision:
            raise SkillError("stale_observation", "Memory must cite the current observation revision")
        if not key or (key not in self.memory and len(self.memory) >= 80):
            raise SkillError("memory_limit", "Use a nonempty key; at most 80 entries")
        self.memory[key] = {"text": text, "revision": revision, "source": "agent_note"}
        return {"stored": key}

    def _tool_recall(self) -> dict:
        return {"plan": copy.deepcopy(self.plan), "memory": copy.deepcopy(self.memory), "revision": self.revision}

    def _tool_finish(self, outcome: str, reason: str, *, render: bool = True) -> dict:
        # Persist evaluation independently of video/replay packaging. An evaluator
        # exception is missing scoring, never a fabricated False or Q=0.
        execution_finished_at = time.time()
        expired = self.deadline.unix is not None and execution_finished_at >= self.deadline.unix
        self.closed = True
        self.recorder.run.update(scoring={'status':'running'},execution_finished_at_unix=execution_finished_at,
                                 deadline_expired_at_finish=expired)
        write_json(self.recorder.output / 'run.json', self.recorder.run)
        try:
            evaluation = self.backend.evaluate()
            task_success = evaluation['task_success']
            scoring = {'status': 'passed'}
        except Exception as exc:
            evaluation, task_success = {}, None
            scoring = {'status': 'failed', 'failure_type': type(exc).__name__, 'failure': str(exc)}
            self.recorder.event('evaluation_failure', scoring)
        evaluation_finished_at = time.time()
        result = {"status": "passed" if task_success and not expired else "failed",
                  "task_success": task_success, "evaluation": evaluation, "scoring": scoring,
                  "agent_outcome": outcome, "finish_reason": reason,
                  "actions": self.actions, "tool_calls": self.calls, "sim_steps": self.backend.steps,
                  "wall_seconds": time.monotonic() - self.started,
                  "execution_finished_at_unix": execution_finished_at,
                  "deadline_expired_at_finish": expired,
                  "evaluation_finished_at_unix": evaluation_finished_at,
                  "evaluation_finished_after_deadline": self.deadline.unix is not None and evaluation_finished_at >= self.deadline.unix}
        if expired:
            result.update(timeout=True, episode_outcome='timeout', termination_reason='episode_deadline_exceeded',
                          episode_deadline_unix=self.deadline.unix)
        self.recorder.event("independent_evaluation", result)
        self.recorder.finish(result, render=render)
        return {"closed": True, "agent_outcome": outcome}
