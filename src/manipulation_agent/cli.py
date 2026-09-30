from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .contracts import Budget
from .harness import Harness
from .records import Recorder
from . import bridge, fake_backend, omnigibson_backend, policies


def main() -> int:
    import faulthandler
    import signal
    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1, all_threads=False)
    parser = argparse.ArgumentParser(description="Independent manipulation agent research harness")
    parser.add_argument("--backend", choices=["mock", "omnigibson"], default="mock")
    parser.add_argument("--policy", choices=["scripted", "responses", "serve"], default="scripted")
    parser.add_argument("--task", default="turning_on_radio")
    parser.add_argument("--instance", type=int, default=301)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--instruction", default="Turn on the radio on the living-room table.")
    parser.add_argument("--max-actions", type=int, default=80)
    parser.add_argument("--max-sim-steps", type=int, default=20000)
    parser.add_argument("--port", type=int, default=29430)
    parser.add_argument("--controller", default="unspecified", help="Recorded external model/client for serve mode")
    args = parser.parse_args()
    policy = None
    # Check credentials BEFORE spending time and GPU memory on simulator startup.
    if args.policy == "responses":
        from .policies import ResponsesPolicy
        policy = ResponsesPolicy.from_env()
    config = vars(args).copy()
    config["output"] = str(args.output.resolve())
    config["validation_level"] = "cpu_contract_only" if args.backend == "mock" else (
        "real_model_real_simulator_oracle_symbolic" if policy else "scripted_real_simulator" if args.policy == "scripted" else "tool_service_requires_controller_evidence")
    recorder = Recorder(args.output, config)
    backend = None
    try:
        if args.backend == "mock":
            from .fake_backend import FakeBackend
            backend = FakeBackend()
        else:
            from .omnigibson_backend import OmniGibsonBackend
            backend = OmniGibsonBackend(args.task, args.instance, args.output, seed=args.seed, max_steps=args.max_sim_steps)
        harness = Harness(backend, recorder, Budget(max_actions=args.max_actions, max_sim_steps=args.max_sim_steps))
        if args.policy == "scripted":
            from .policies import scripted_episode
            scripted_episode(harness, args.task)
        elif args.policy == "serve":
            from .bridge import serve
            serve(harness, args.port)
        else:
            policy.run(harness, args.instruction)
        return 0 if recorder.run.get("task_success") else 2
    except BaseException as exc:
        (args.output / "traceback.txt").write_text(traceback.format_exc())
        recorder.event("runtime_failure", {"type": type(exc).__name__, "message": str(exc)})
        recorder.finish({"status": "failed", "task_success": None, "failure": type(exc).__name__})
        raise
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    sys.exit(main())
