# Four-camera async validation — 2026-09-30

The current configuration has four fixed 512×512 cameras: front/back/left/right, relative to the robot base. Capture reads all four at one frozen simulation state and does not turn the robot. No wrist images enter the policy.

## Executed evidence

| Level | Result | Scope |
| --- | --- | --- |
| CPU contracts | 60 tests passed | Async lifecycle, stale targets, cancellation, ownership, camera geometry, core harness |
| Real MCP / revised camera rig | 16 checks passed | 5 captures, 2 jobs, actual ImageContent hashes; zero robot actions/steps in observation-only probe |
| Real LLM closed loop | Passed | turning_on_radio / public_test / instance301 / seed0; 2 actions, 8 tools, 155 control steps |
| Independent task evaluator | Success, Q=1 | Initial goal false, final true; evaluation unavailable to the active policy |
| Model/simulator evidence | 18 checks passed | All12 actual image payloads match archives; clean matching source, Skill bundle and exact calls/results |
| Episode video | 10 checks passed | 159 H264 frames,30FPS,5.3s; every155 control steps plus4 observation boundaries |
| Browser replay | Passed | 4 pages, four directions, exact job JSON, links, playback, mobile layout; noJS/HTTP errors |
| Replay unit regression | 9 tests passed | After presentation fixes |

Simulation and policy source: `a87a0493edb6ebcd390c0d3bc8f0e3736d584c79`, clean, source SHA256 `82a535a2d35c2458ef12109fc691b37cf875bb65a3c3fa17910d51a78860f7d3`. Later commits change replay/report presentation and validation, not these executed episodes.

The real policy called list_skills → read_skill → start_observation → read_skill → get_observation → navigate_to using the **left** camera → toggle_on using a fresh **front** camera → finish. It did not request a look/turn for observation. Original public assistant messages and MCP calls are preserved verbatim in replay; no hidden reasoning is reconstructed.

## Runtime and reproducibility

- Host: S134 / ZJU3DV-3090; interpreter `/mnt/data2/home/xujingyi/agentic_mobile_manip/envs/behavior/bin/python`.
- GPU: `GPU-99a7efe6-7e2d-a5b7-f781-c9856a61e8b4` (index3).
- Task unit: `mas-surround-radio-r1-20260930.service`, PID1669363; output `/mnt/data2/home/xujingyi/agentic_mobile_manip/runs/mas_surround_radio_r1`.
- Controller: V05 / ZJU3DV-V05, PID3088635, docs Python3.13, codex-cli0.159.2, gpt-6-astra;96.883s. Simulator unit exited0 and released GPU allocation.
- Dependencies: OmniGibson3.9.2 at `b1979916ec1549b10a4e65e630bc6504a9af1b00`, Isaac5.1.0.0, torch2.7.0+cu128, BDDL3.7.0, MCP1.28.1; behavior assets3.9.0, robot assets3.8.2. Full task/scene/config hashes remain in run.json.
- Project batch: `operations/async_surround_20260930`; includes journal, run_records, validation, pre/postflight, preserved failures and source patch. Large artifacts stay outside Git.

## Iterations and limits

The first8cm mount passed API and pose checks but front RGB had major robot-shell occlusion. The revised35cm fixed mount passed the repeated real-simulator probe and visual inspection of all four images. Neither robot geometry nor images were hidden/edited.

Two launch attempts failed before any policy tool call because child processes lacked explicit PYTHONPATH; both logs remain and retries used the same untouched simulator episode. Browser checks found a programmatic video seek overwriting an explicit selected step, plus a hidden link to a nonexistent public transcript on scripted probes. Both presentation issues were fixed and rechecked.

Observation-only probes intentionally finish aborted and have task_success=false; their passed camera checks are not task successes. The radio result is one regression in an **ideal motor executor** research setting; it is not a100-task evaluation or an official physical-control challenge submission. Planning/Stack/memory remain outside the active profile.

[Live four-camera report](http://10.76.5.241:8765/surround_observation.html) · [Radio replay](http://10.76.5.241:8765/manipulation_runs/mas_surround_radio_r1/replay.html) (lab network/VPN)
