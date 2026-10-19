"""BEHAVIOR task with a dependency graph over goal milestones."""

from omnigibson.tasks.behavior_task import BehaviorTask
from omnigibson.termination_conditions.termination_condition_base import SuccessCondition
from omnigibson.termination_conditions.timeout import Timeout
from omnigibson.utils.python_utils import classproperty


class PartialOrderPredicateGoal(SuccessCondition):
    """Success condition that completes milestones when their prerequisites are met.

    The underlying BDDL goal remains a conjunction of state predicates. This condition
    adds a partial-order interpretation: independent milestones may complete in any
    order, while a milestone is counted only after all of its prerequisites have
    completed.
    """

    def __init__(self, check_goal_fn, milestones):
        self._check_goal_fn = check_goal_fn
        self._milestones = milestones
        self._completed = set()
        self._goal_status = None
        super().__init__()

    def reset(self, task, env):
        super().reset(task, env)
        self._completed = set()
        self._goal_status = {"satisfied": [], "unsatisfied": []}

    def _step(self, task, env, action):
        _, goal_status = self._check_goal_fn()
        satisfied = set(goal_status["satisfied"])

        changed = True
        while changed:
            changed = False
            for name, milestone in self._milestones.items():
                if name in self._completed:
                    continue
                if not milestone["requires"].issubset(self._completed):
                    continue
                if set(milestone["goal_indices"]).issubset(satisfied):
                    self._completed.add(name)
                    changed = True

        completed_indices = {
            index
            for name, milestone in self._milestones.items()
            if name in self._completed
            for index in milestone["goal_indices"]
        }
        all_indices = {
            index
            for milestone in self._milestones.values()
            for index in milestone["goal_indices"]
        }
        self._goal_status = {
            "satisfied": sorted(completed_indices),
            "unsatisfied": sorted(all_indices - completed_indices),
            "completed_milestones": sorted(self._completed),
        }
        return len(self._completed) == len(self._milestones)

    @property
    def goal_status(self):
        return self._goal_status


class PartialOrderBehaviorTask(BehaviorTask):
    """BehaviorTask variant whose goal milestones follow a partial-order DAG."""

    def __init__(self, partial_order=None, **kwargs):
        if not partial_order:
            raise ValueError("partial_order must define at least one milestone")
        self.partial_order = self._normalize_partial_order(partial_order)
        super().__init__(**kwargs)

    @staticmethod
    def _normalize_partial_order(partial_order):
        normalized = {}
        seen_goal_indices = set()
        for name, milestone in partial_order.items():
            indices = tuple(sorted(set(milestone.get("goal_indices", []))))
            requires = frozenset(milestone.get("requires", []))
            if not indices:
                raise ValueError(f"Milestone '{name}' must reference at least one goal index")
            if any(index < 0 for index in indices):
                raise ValueError(f"Milestone '{name}' contains a negative goal index")
            duplicate_indices = seen_goal_indices.intersection(indices)
            if duplicate_indices:
                raise ValueError(f"Goal indices cannot belong to multiple milestones: {sorted(duplicate_indices)}")
            seen_goal_indices.update(indices)
            normalized[name] = {"goal_indices": indices, "requires": requires}

        names = set(normalized)
        for name, milestone in normalized.items():
            unknown = milestone["requires"] - names
            if unknown:
                raise ValueError(f"Milestone '{name}' references unknown prerequisites: {sorted(unknown)}")
            if name in milestone["requires"]:
                raise ValueError(f"Milestone '{name}' cannot depend on itself")

        visiting = set()
        visited = set()

        def visit(name):
            if name in visiting:
                raise ValueError("partial_order contains a dependency cycle")
            if name in visited:
                return
            visiting.add(name)
            for prerequisite in normalized[name]["requires"]:
                visit(prerequisite)
            visiting.remove(name)
            visited.add(name)

        for name in names:
            visit(name)
        return normalized

    def _create_termination_conditions(self):
        return {
            "timeout": Timeout(max_steps=self._termination_config["max_steps"]),
            "predicate": PartialOrderPredicateGoal(
                check_goal_fn=lambda: self.compiled_task.check_goal(self._evaluate_predicate),
                milestones=self.partial_order,
            ),
        }

    @classproperty
    def default_termination_config(cls):
        return {"max_steps": 500}
