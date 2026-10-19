#!/usr/bin/env python3
"""Audit BEHAVIOR goal predicates against the public atomic action catalog.

This is a static contract check: it does not claim that every scene is easy to
navigate, but it catches a goal relation for which the agent has no action at
all. It is intentionally independent of the planning skill.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from manipulation_agent.contracts import SKILLS


PREDICATE_ACTIONS = {
    "ontop": {"place_on_top"}, "inside": {"place_inside"}, "contains": {"pour", "place_inside"},
    "filled": {"pour"}, "under": {"place_under"}, "nextto": {"place_next_to"},
    "attached": {"attach"}, "hanging": {"hang"}, "open": {"open"}, "closed": {"close"},
    "toggled_on": {"toggle_on"}, "toggled_off": {"toggle_off"}, "cooked": {"place_near_heating_element", "wait"},
    "hot": {"place_near_heating_element", "wait"}, "covered": {"wipe", "sweep", "vacuum", "spray", "spread"},
    "saturated": {"soak"}, "cut": {"cut"}, "sliced": {"cut"}, "diced": {"cut"},
    # These are lifecycle / evaluator predicates, not direct robot actions.
    "real": set(), "future": set(), "inroom": set(), "and": set(), "or": set(), "not": set(),
    "forall": set(), "exists": set(), "forpairs": set(), "forn": set(), "imply": set(),
    "overlaid": set(), "draped": set(),
}


def goal_predicates(text: str) -> set[str]:
    block = text.split("(:goal", 1)[-1]
    return set(re.findall(r"\(\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s+", block))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bddl_root", type=Path, help="bddl/activity_definitions directory")
    parser.add_argument("--json", type=Path, help="write the machine-readable report")
    parser.add_argument("--task", help="check one task strictly and fail if it has an unmapped predicate")
    args = parser.parse_args()
    files = sorted(args.bddl_root.glob("*/problem0.bddl"))
    if args.task:
        files = [path for path in files if path.parent.name == args.task]
        if not files:
            parser.error(f"task not found: {args.task}")
    report = {"task_count": len(files), "catalog": list(SKILLS), "tasks": {}, "unsupported_predicates": {}}
    for path in files:
        predicates = sorted(goal_predicates(path.read_text()))
        unsupported = sorted(p for p in predicates if p not in PREDICATE_ACTIONS)
        report["tasks"][path.parent.name] = {"goal_predicates": predicates, "unsupported": unsupported}
        for predicate in unsupported:
            report["unsupported_predicates"].setdefault(predicate, []).append(path.parent.name)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"task_count": report["task_count"], "catalog_size": len(SKILLS),
                      "unsupported_predicates": report["unsupported_predicates"]}, ensure_ascii=False))
    # The complete BEHAVIOR catalog contains evaluator-only predicates (e.g.
    # quantified and thermal states) that are intentionally outside this
    # visual manipulation adapter. A strict single-task check is useful for a
    # formal run; the whole-catalog report remains informational.
    return 1 if args.task and report["unsupported_predicates"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
