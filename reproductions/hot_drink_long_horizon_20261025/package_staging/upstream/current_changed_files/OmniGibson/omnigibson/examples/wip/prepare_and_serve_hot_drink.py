"""Load the custom prepare-and-serve hot drink long-horizon task."""

import os

import yaml

import omnigibson as og
from omnigibson.macros import gm


gm.ENABLE_OBJECT_STATES = True
gm.USE_GPU_DYNAMICS = False


def main():
    """Create one continuous episode with a dependency-aware partial-order task."""
    config_filename = os.path.join(og.example_config_path, "r1pro_behavior.yaml")
    with open(config_filename, "r") as f:
        cfg = yaml.load(f, Loader=yaml.FullLoader)

    cfg["scene"].update(
        {
            "scene_model": "house_single_floor",
            "load_room_types": ["kitchen", "living_room"],
        }
    )
    cfg["task"].update(
        {
            "activity_name": "prepare_and_serve_hot_drink",
            "activity_definition_id": 0,
            "activity_instance_id": 0,
            "type": "PartialOrderBehaviorTask",
            "online_object_sampling": True,
            "use_presampled_robot_pose": False,
            "termination_config": {"max_steps": 1800},
            "partial_order": {
                "retrieve_water": {"goal_indices": [0], "requires": []},
                "boil_water": {"goal_indices": [1, 2], "requires": ["retrieve_water"]},
                "gather_tableware": {"goal_indices": [3, 4, 5, 6], "requires": []},
                "set_table": {"goal_indices": [7], "requires": ["gather_tableware"]},
                "prepare_snacks": {"goal_indices": [8, 9], "requires": ["set_table"]},
                "pour_drink": {"goal_indices": [10], "requires": ["boil_water"]},
                "serve_drink": {
                    "goal_indices": [11],
                    "requires": ["pour_drink", "prepare_snacks"],
                },
            },
        }
    )

    env = og.Environment(configs=cfg)
    og.log.info("Loaded prepare_and_serve_hot_drink; the episode stays in one environment.")

    try:
        env.reset()
        while True:
            action = env.robots[0].action_space.sample() * 0.0
            _, _, terminated, truncated, _ = env.step(action)
            if terminated or truncated:
                break
    finally:
        og.shutdown()


if __name__ == "__main__":
    main()
