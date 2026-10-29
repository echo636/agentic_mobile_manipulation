"""Unified CLI policy entrypoint; existing batch commands may keep run_codex_controller.py."""
from run_codex_controller import main

if __name__ == "__main__":
    raise SystemExit(main(allow_client=True))
