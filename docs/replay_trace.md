# Readable replay and model trace (2026-10-01)

The default viewing edition is now `inspection.mp4`, produced by
`inspection_video.py`. It retains every source frame in order at **1× simulation
speed**. The front camera is largest, the other three robot cameras are small,
and the third-person camera is explicitly marked replay-only. All five views
already existed in the original recording; no new simulator execution is needed.

Public assistant text, returned reasoning summaries, and tool arguments are
paginated without ellipsis or dropped characters. Each page stays for at least
5 seconds and otherwise uses 24 characters/second. These added holds are reading
time, not measured model inference. Tool-result status/error/effect is displayed
in the MP4; the synchronized HTML retains the full returned text and images.
The sidecar preserves exact page text, source-frame intervals, hashes and process
provenance. Third-person boundary stills are extracted from the archived video,
not included in model observations. The HTML shows front RGB prominently beside
scrollable original model/tool text, with three small surround views and the
third-person still. Video seeks and tool selection synchronize in both directions.

The video sits beside the conversation. In the inspection edition, the page
crops only the displayed MP4 viewport to its five-camera region; dedicated play
and seek controls remain available. Downloaded video bytes are unchanged.

The right panel is a **persistent chronological conversation**, rendered once
per episode. Selecting a step or playing the video changes only the highlight
and scroll position. All previous and later archived messages remain available,
including final messages and transport failures without simulator records.
Scrolling manually disables follow; the follow checkbox resumes synchronization.
Tool arguments and every returned text block are retained, not only the first
block. Images remain in the camera panel and downloadable original event stream.

`model_transcript` records source event indices and IDs. Started calls and their
completed results retain their separate positions, including intervening model
messages. Readable public reasoning-summary events are preserved; matching session
summaries are not duplicated. Summaries only available in the separate session
log are explicitly aligned to their next timestamped tool boundary, not given an
invented exact position relative to untimestamped assistant messages.

Publish the results page and its shared viewer assets together:

```bash
python scripts/publish_results_page.py --output-dir REPORT_DIRECTORY
```

Standalone replays inline the same JavaScript and CSS and need no external viewer
assets. `tests/test_transcript.py` covers event order, interleaving, full results,
final/unmatched calls, and returned-summary provenance.

```bash
python -m manipulation_agent.inspection_video --run-dir RUN --controller-dir CONTROLLER
python -m manipulation_agent.replay --run-dir RUN --controller-dir CONTROLLER
```

Reference: jinkai/harness at `0815cf234ee591bacd8017e9b1def4fac13e649b`,
`tools/vis/rerun_nav_viewer.py` for first/third-person views, timestamped text and
tool traces, and `tools/pluggable_harness/session_trace_adapters.py` for public
assistant/tool alignment. Its Codex adapter can store assistant messages in a
field named `reasoning`; that name alone does not establish full internal reasoning.

[Readable replay and full 32-run failure review](http://10.76.5.241:8765/replay_readable_20261001/index.html).
Validation and raw-preservation records are under
`operations/replay_readable_20261001`. Rendering success is separate from task
success. The source radio trace now has a 234.17-second readable edition; its
original compact edition remains 24.57 seconds. The similar readable duration and
229.05-second controller time are coincidental, not a latency reconstruction.

## Retained compact edition

`review_video.py` creates a new `review.mp4`; it does not overwrite `episode.mp4`, `walltime.mp4`, raw events, captures, or scores. Default motion is 2× the source simulation-time video. Source action endpoints remain visible, static non-motor intervals retain their final frame, and every tool call is represented. Bounded reading holds are editorial time, not model latency. No interpolated or resimulated frames are created. The JSON sidecar records frame selections, source/video hashes, tool identifiers, host, interpreter, PID/unit, and timing policy. Long panel text can be truncated; the HTML transcript preserves complete text.

The player defaults to the readable edition when available; compact, original
simulation-time and wall-time editions remain selectable. Renderer labels are
English. Actual model text remains in its original language; no generated Chinese
explanation substitutes for it.

## Two distinct model text channels

- `model_messages`: verbatim public assistant messages from `codex exec --json`, aligned by call order. These do not establish exact assistant output timestamps.
- `model_reasoning_summaries`: only readable `response_item.payload.reasoning.summary` / `summary_text` actually returned by the provider, aligned by recorded timestamps. These are reasoning summaries, not the full internal chain of thought.

`run_codex_controller.py` now requests `model_reasoning_summary="auto"` and persists its session. `model_trace.py` locates only the exact thread UUID in the run's bounded date range under the existing Codex session root. It exports the summary text, timestamp and source line into `model_reasoning_summaries.jsonl`, plus a session hash and availability metadata. It does not export encrypted content, system prompts, credentials, unrelated sessions, or rewrite summaries. Existing authentication and CODEX_HOME are retained.

Historical ephemeral runs did not archive provider summaries. Their replay says “Not recorded / not returned”; it does not reconstruct them. A missing per-step summary is shown separately from a missing per-run summary.

Reference inspected: the `jinkai/harness` snapshot in `repos/habitat-gs-jinkai`, particularly `bench/tool/harness/codex_agent.py::_merge_codex_session_timeline` and `tools/pluggable_harness/session_trace_adapters.py`. It uses the same distinction between stdout assistant/tool events and returned session reasoning summaries.

Provider documentation: https://developers.openai.com/api/docs/guides/reasoning

## Actual validation

The independent `operations/replay_trace_20261001` radio episode used four fixed RGB cameras, the real model and real simulator. It completed the task with independent success, Q=1, complete video and aligned image/tool evidence. Controller duration was 229.048 seconds. The provider returned one short readable summary and five items containing encrypted content; no claim is made that this is a complete explanation of every decision. The original 32-task benchmark is unchanged.

`operations/executor_fixes_20261001/html_validation.json` checks compact/default playback, seeking, original timing editions, four RGB views, exact returned-summary display, missing historical summaries, mobile layout, and JavaScript/HTTP errors. Raw diagnostic and publication records are separate from benchmark results.

To render a single completed episode:

```bash
python -m manipulation_agent.review_video --run-dir RUN --controller-dir CONTROLLER
```

The renderer refuses to overwrite an existing compact MP4. Batch publishing preserves the previous derived HTML/JSON as `*_before_review.*`. Interrupted rendering attempts are retained with a termination record. Task status must always come from the independent run evaluator, never from video-render success.
