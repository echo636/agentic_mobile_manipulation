"""Scripted motor diagnostic over the real bridge; never an autonomous model result."""
import argparse
import copy
import json
from pathlib import Path
from uuid import uuid4

from manipulation_agent.bridge import rpc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default="http://127.0.0.1:29441")
    parser.add_argument("--arm", choices=["left", "right"], default="right")
    parser.add_argument("--dz-m", type=float, default=.03)
    parser.add_argument("--prepare-arm", action=argparse.BooleanOptionalAction, default=True,
                        help="Move to the reported ready joint posture before testing Cartesian tracking")
    parser.add_argument("--keep-open", action="store_true", help="Keep the diagnostic episode available for follow-up checks")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not -.08 <= args.dz_m <= .08:
        parser.error("Probe displacement must be within +/- 8 cm")
    args.output.mkdir(parents=True, exist_ok=False)
    responses = []
    def call(name, arguments):
        result = rpc(args.bridge, name, arguments, "motor-probe-" + uuid4().hex)
        responses.append({"tool": name, "arguments": arguments, "result": result})
        (args.output / "calls.json").write_text(json.dumps(responses, ensure_ascii=False,
                                                          allow_nan=False, indent=2))
        return result
    initial = call("initialize", {})
    observation = initial["observation"]
    state = observation["oracle_3d"]["robot"]["arms"][args.arm]
    if args.prepare_arm:
        prepared = call("move_joints", {"group": "arm_" + args.arm,
                                       "positions_rad": state["ready_joint_positions_rad"],
                                       "revision": observation["revision"]})
        if not prepared["ok"]:
            call("finish", {"outcome": "aborted", "reason": "Scripted ready-posture diagnostic failed"})
            print(json.dumps({"reach_and_return": False, "failed_stage": "ready_posture"}))
            return 2
        observation = prepared["observation"]
        state = observation["oracle_3d"]["robot"]["arms"][args.arm]
    original = copy.deepcopy(state["eef_pose"])
    target = copy.deepcopy(original)
    target["position_m"][2] += args.dz_m
    target["gripper"] = "hold"
    result = call("execute_waypoints", {"arm": args.arm, "frame": "robot_base",
                                       "waypoints": [target], "revision": observation["revision"]})
    reached = result["ok"]
    if reached:
        original["gripper"] = "hold"
        result = call("execute_waypoints", {"arm": args.arm, "frame": "robot_base",
                                           "waypoints": [original],
                                           "revision": result["observation"]["revision"]})
        reached = result["ok"]
    if not args.keep_open:
        call("finish", {"outcome": "aborted",
                        "reason": "Scripted reach-and-return motor diagnostic; not a task policy"})
    summary = {"validation_level": "scripted_real_simulator_motor_diagnostic",
               "reach_and_return": reached, "arm": args.arm, "dz_m": args.dz_m}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))
    return 0 if reached else 2


if __name__ == "__main__":
    raise SystemExit(main())
