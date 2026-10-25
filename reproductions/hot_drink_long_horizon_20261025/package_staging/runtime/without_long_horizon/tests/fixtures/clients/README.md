These fixtures are public event envelopes, not benchmark scores or live model acceptance tests.

- `codex_recorded.jsonl`: selected non-reasoning rows from the project's recorded `operations/compare100_20261002/fresh100_20261003/original/controllers/mas_fresh100_original_044_chopping_wood_i301_s0_r1/model_events.jsonl` (finish rows only). Real CLI-generated events, including an actual finish result.
- `opencode_format.jsonl`: synthetic manipulation data in the `tool_use/part/state` format parsed by the local Jinkai snapshot `bench/tool/harness/opencode_agent.py`. This is a format fixture, not captured OpenCode execution.
- `kimi_format.jsonl`: synthetic manipulation data in the documented assistant/tool stream-json format and Jinkai `bench/tool/harness/kimi_agent.py` parser. Image content is explicitly fake fixture bytes.

References checked on 2026-10-04: https://moonshotai.github.io/kimi-cli/en/customization/print-mode.html and https://opencode.ai/docs/agents .
