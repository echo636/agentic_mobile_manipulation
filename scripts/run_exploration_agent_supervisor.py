"""Rotate conversations only at explicit subtask checkpoints; never retry failures."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--initial", choices=("resume", "fresh-thread", "new"), default="resume")
    args = parser.parse_args()
    entry = Path(__file__).with_name("run_exploration_pipeline.py")
    mode = args.initial
    while True:
        command = [sys.executable, "-u", str(entry), "run", "--session", str(args.session),
                   "--provider", args.provider]
        if mode == "resume":
            command.append("--resume")
        elif mode == "fresh-thread":
            command.append("--fresh-thread")
        result = subprocess.run(command)
        if result.returncode != 0:
            return result.returncode
        pending = args.session / "pending_thread_rotation.json"
        if not pending.is_file():
            return 0
        data = json.loads(pending.read_text())
        if not data.get("checkpoint_id"):
            raise RuntimeError("Invalid pending thread rotation")
        mode = "fresh-thread"


if __name__ == "__main__":
    raise SystemExit(main())
