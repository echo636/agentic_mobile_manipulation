"""Deterministic CPU contract fixture. Never used as evidence of simulator success."""
from __future__ import annotations

import copy

from .contracts import SkillError


class FakeBackend:
    mode = "mock_state"

    def __init__(self):
        self.steps = 0
        self.held = None
        self.objects = [
            {"id": "radio.n.01_1", "category": "radio", "distance_m": 4.0, "states": {"toggled_on": False}},
            {"id": "apple.n.01_1", "category": "apple", "distance_m": 0.7, "states": {}},
            {"id": "cabinet.n.01_1", "category": "cabinet", "distance_m": 0.9, "states": {"open": False}},
        ]
        self.fail_next = False

    def observe(self):
        return {"objects": copy.deepcopy(self.objects), "held_object": self.held, "images": []}

    def execute(self, skill, target, max_steps):
        if max_steps < 1:
            raise SkillError("action_timeout", "No physics budget")
        self.steps += 1
        if self.fail_next:
            self.fail_next = False
            raise SkillError("execution_error", "Injected partial execution", changed=True)
        obj = next((o for o in self.objects if o["id"] == target), None)
        if skill == "navigate_to":
            obj["distance_m"] = 0.8
        elif skill == "grasp":
            self.held = target
        elif skill in {"release", "place_inside", "place_on_top"}:
            self.held = None
        elif skill in {"toggle_on", "toggle_off"}:
            obj["states"]["toggled_on"] = skill == "toggle_on"
        elif skill in {"open", "close"}:
            obj["states"]["open"] = skill == "open"
        return {"mock": True, "steps": 1}

    def evaluate(self):
        success = self.objects[0]["states"]["toggled_on"]
        return {"task_success": success, "protocol": "mock_contract_only", "official_submission_eligible": False}

    def provenance(self):
        return {"name": "mock", "gpu_uuid": None, "validation_level": "cpu_contract_only"}

    def close(self):
        pass
