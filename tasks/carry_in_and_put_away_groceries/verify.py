"""Check the pinned public scene and the custom task's author-side goal graph."""

import argparse
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent


def check(behavior_root: Path) -> dict:
    manifest = json.loads((HERE / "source_manifest.json").read_text())
    layout = json.loads((HERE / "scene_layout.json").read_text())
    graph = json.loads((HERE / "partial_order.json").read_text())
    bddl = (HERE / "problem0.bddl").read_text()

    for relative, expected in manifest["source_files"].items():
        path = behavior_root / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Pinned source changed: {path}")

    scene_root = (behavior_root / "datasets/2026-challenge-task-instances"
                  / "scene_test/public/house_double_floor_lower/json")
    template = json.loads((scene_root /
        "house_double_floor_lower_task_carrying_in_groceries_0_0_template-partial_rooms.json").read_text())
    state = json.loads((scene_root /
        "house_double_floor_lower_task_carrying_in_groceries_instances"
        / "house_double_floor_lower_task_carrying_in_groceries_0_301_template-tro_state.json").read_text())
    bindings = template["metadata"]["task"]["inst_to_name"]
    objects = template["objects_info"]["init_info"]
    for key, asset in layout["asset_models"].items():
        if key not in state:
            raise ValueError(f"Missing instance state: {key}")
        name = bindings[key]
        args = objects[name]["args"]
        if f"{args['category']}/{args['model']}" != asset:
            raise ValueError(f"Asset mismatch for {key}")
    for item, parent in layout["initial_locations"].items():
        if f"(inside {item} {parent})" not in bddl:
            raise ValueError(f"Initial relation missing from BDDL: {item} in {parent}")

    seen = set()
    active = set()

    def visit(node):
        if node in active:
            raise ValueError(f"Dependency cycle at {node}")
        if node in seen:
            return
        active.add(node)
        for predecessor in graph[node]["requires"]:
            if predecessor not in graph:
                raise ValueError(f"Unknown prerequisite: {predecessor}")
            visit(predecessor)
        active.remove(node)
        seen.add(node)

    goal_indices = []
    for node, entry in graph.items():
        visit(node)
        if entry["kind"] == "final_goal" and entry["evidence"] not in bddl:
            raise ValueError(f"Goal evidence missing from BDDL: {node}")
        goal_indices.extend(entry["goal_indices"])
    if sorted(goal_indices) != list(range(5)):
        raise ValueError("The five final BDDL goals must each have one milestone index")
    if "(problem carrying_in_groceries-0)" not in bddl:
        raise ValueError("The public scene task ID must remain carrying_in_groceries")
    return {"status": "passed", "level": "static_source_and_goal_graph",
            "task": manifest["base_task"], "instance": manifest["instance_id"],
            "pinned_files": len(manifest["source_files"]), "milestones": len(graph)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--behavior-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.behavior_root.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
