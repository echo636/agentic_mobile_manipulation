# Compact replay and model trace (2026-10-01)

`review_video.py` creates a new `review.mp4`; it does not overwrite `episode.mp4`, `walltime.mp4`, raw events, captures, or scores. Default motion is 2× the source simulation-time video. Source action endpoints remain visible, static non-motor intervals retain their final frame, and every tool call is represented. Bounded reading holds are editorial time, not model latency. No interpolated or resimulated frames are created. The JSON sidecar records frame selections, source/video hashes, tool identifiers, host, interpreter, PID/unit, and timing policy. Long panel text can be truncated; the HTML transcript preserves complete text.

The player defaults to compact video and permits original simulation-time / wall-time editions. Renderer labels are English. Actual model text remains in its original language; no generated Chinese explanation substitutes for it.

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
