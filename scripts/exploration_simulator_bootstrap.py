"""Read task argv from JSON before entering the existing simulator CLI.

Keep the native OS command line short, matching the established worker launcher.
No instruction text is interpolated into a shell or evaluated as Python.
"""
import json
from pathlib import Path
import runpy
import sys


def main():
    args = json.loads(Path(sys.argv[1]).read_text())
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise TypeError("Simulator argv must be a JSON string array")
    sys.argv = ["vision_cli", *args]
    runpy.run_module("manipulation_agent.vision_cli", run_name="__main__")


if __name__ == "__main__":
    main()
