import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from manipulation_agent.clients import ClientConfig, get_adapter
from manipulation_agent.clients.events import parse_events, successful_finish_event, write_derived_events
from manipulation_agent.reporting.efficiency_table import token_usage
from manipulation_agent.reporting.transcript import build_transcript


class ClaudeAndAgyAdapterTests(unittest.TestCase):
    def config(self, root: Path, client: str, **kwargs):
        return ClientConfig(
            client,
            "gemini-3.8-flash-low" if client == "agy" else "sonnet",
            "Inspect and finish.",
            "/usr/bin/python",
            ["-m", "manipulation_agent.mcp_server"],
            root,
            **kwargs,
        )

    def test_claude_prepare_project_and_resume_enforce_mcp_only_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            adapter = get_adapter("claude")
            prepared = adapter.prepare_project(
                self.config(root, "claude", reasoning_effort="low"),
                "Policy instructions here.",
                ["initialize", "act", "finish"],
            )
            self.assertIn("--tools", prepared.argv)
            tools_idx = prepared.argv.index("--tools")
            self.assertEqual(prepared.argv[tools_idx + 1], "")
            self.assertIn("--strict-mcp-config", prepared.argv)
            self.assertIn("--allowedTools", prepared.argv)
            allow_idx = prepared.argv.index("--allowedTools")
            self.assertEqual(
                prepared.argv[allow_idx + 1],
                "mcp__manipulation__initialize,mcp__manipulation__act,mcp__manipulation__finish",
            )
            self.assertIn("--append-system-prompt", prepared.argv)
            self.assertNotIn("--system-prompt", prepared.argv)
            self.assertIn("Policy instructions here.", prepared.argv[prepared.argv.index("--append-system-prompt") + 1])
            self.assertIn("--effort", prepared.argv)
            self.assertEqual(prepared.argv[prepared.argv.index("--effort") + 1], "low")
            mcp_cfg = json.loads((prepared.cwd / "mcp.json").read_text())
            self.assertEqual(set(mcp_cfg["mcpServers"]), {"manipulation"})

            sid = "cfc32785-deed-4625-b3d3-bc792fbf81fc"
            resumed = adapter.prepare_resume(prepared, sid, "Continue episode.")
            self.assertEqual(resumed.argv[-2:], ["--resume", sid])
            self.assertEqual(resumed.stdin, "Continue episode.")
            self.assertEqual(resumed.cwd, prepared.cwd)

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap required for AgyAdapter")
    def test_agy_prepare_project_and_resume_isolate_config_and_gate_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake_home = root / "home"
            (fake_home / ".gemini" / "antigravity-cli" / "builtin").mkdir(parents=True)
            (fake_home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").write_text("SECRET_DO_NOT_COPY")
            output = root / "episode"
            output.mkdir()
            adapter = get_adapter("agy")
            with patch("pathlib.Path.home", return_value=fake_home):
                prepared = adapter.prepare_project(
                    self.config(output, "agy", reasoning_effort="low"),
                    "Policy instructions for agy.",
                    ["initialize", "act", "finish"],
                )
            self.assertEqual(prepared.argv[0], shutil.which("bwrap"))
            self.assertTrue(prepared.argv[-1].startswith("-p=Use ONLY `call_mcp_tool`"))
            self.assertIsNone(prepared.stdin)
            # Auth token file in episode output is an empty mount point, never a copy of secret bytes!
            mount_placeholder = output / "agy_isolated_gemini" / "antigravity-cli" / "antigravity-oauth-token"
            self.assertEqual(mount_placeholder.read_text(), "")
            self.assertNotIn("SECRET_DO_NOT_COPY", (output / "agy_project" / "GEMINI.md").read_text())
            self.assertIn("Policy instructions for agy.", (output / "agy_project" / "GEMINI.md").read_text())
            hooks = json.loads((output / "agy_project" / ".agents" / "hooks.json").read_text())
            self.assertIn("mcp-only-gate", hooks)
            # agy's default 3 min per-call MCP deadline must not cut a running executor action (300 s cap).
            server = json.loads((output / "agy_isolated_gemini" / "mcp_config.json").read_text())["mcpServers"]["manipulation"]
            self.assertGreaterEqual(server["timeoutSeconds"], 360)

            cid = "829f852d-dcb1-47a7-b455-7ee67f11cf78"
            resumed = adapter.prepare_resume(prepared, cid, "Continue from current state.")
            self.assertEqual(resumed.argv[-3:], ["--conversation", cid, "-p=Continue from current state."])
            self.assertEqual(resumed.mcp_prefix, prepared.mcp_prefix)

    def test_claude_stream_json_parsing_links_images_finish_and_non_duplicated_tokens(self):
        events = [
            {"type": "system", "subtype": "init", "session_id": "cfc32785-deed-4625-b3d3-bc792fbf81fc"},
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "content": [
                        {"type": "text", "text": "Initializing."},
                        {"type": "tool_use", "id": "toolu_init", "name": "mcp__manipulation__initialize", "input": {}},
                    ],
                    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0, "output_tokens": 10},
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_init",
                            "content": [
                                {"type": "text", "text": '{"ok": true, "initialized": true}'},
                                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "RklYVFVSRQ=="}},
                            ],
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "msg_2",
                    "content": [
                        {"type": "tool_use", "id": "toolu_fin", "name": "mcp__manipulation__finish", "input": {"outcome": "achieved", "reason": "done"}},
                    ],
                    "usage": {"input_tokens": 3, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 1000, "output_tokens": 15},
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_fin",
                            "content": [{"type": "text", "text": '{"ok": true, "closed": true, "agent_outcome": "achieved"}'}],
                        }
                    ]
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "cfc32785-deed-4625-b3d3-bc792fbf81fc",
                "usage": {"input_tokens": 5, "cache_creation_input_tokens": 1200, "cache_read_input_tokens": 1000, "output_tokens": 25},
            },
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "model_events.jsonl"
            raw.write_text("".join(json.dumps(e) + "\n" for e in events))
            parsed = parse_events(raw, "claude")
            write_derived_events(root, parsed, "claude")
            self.assertEqual(parsed.tools_called, ["initialize", "finish"])
            self.assertTrue(parsed.formal_finish_observed)
            self.assertTrue(successful_finish_event(events[4]))
            timeline = build_transcript(parsed.compat, [], [])
            self.assertEqual(sum(x.get("image_count", 0) for x in timeline), 1)
            usage = token_usage(root)
            self.assertEqual(usage["input_tokens"], 2205)
            self.assertEqual(usage["cached_input_tokens"], 1000)
            self.assertEqual(usage["output_tokens"], 25)

    def test_agy_stream_json_parsing_unwraps_call_mcp_tool_and_deduplicates_step_usage(self):
        events = [
            {"event": "init", "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78"},
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78",
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "usage": {"input_tokens": 8000, "output_tokens": 30, "cache_read_tokens": 0, "total_tokens": 8030},
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78",
                    "step_index": 2,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {"name": "call_mcp_tool", "parameters": {"ServerName": "manipulation", "ToolName": "initialize", "Arguments": {}}},
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78",
                    "step_index": 2,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {
                        "name": "call_mcp_tool",
                        "parameters": {"ServerName": "manipulation", "ToolName": "initialize", "Arguments": {}},
                        "output": '{"ok": true, "initialized": true}',
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78",
                    "step_index": 3,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {
                        "name": "call_mcp_tool",
                        "parameters": {"ServerName": "manipulation", "ToolName": "finish", "Arguments": {"outcome": "achieved", "reason": "ok"}},
                        "output": '{"ok": true, "closed": true}',
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {"conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78", "step_index": 4, "state": "ACTIVE", "step_type": "agent_response", "text_delta": "Task "},
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78",
                    "step_index": 4,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "complete.",
                    "usage": {"input_tokens": 2000, "output_tokens": 20, "cache_read_tokens": 7500, "total_tokens": 2020},
                },
            },
            {
                "event": "result",
                "result": {
                    "conversation_id": "829f852d-dcb1-47a7-b455-7ee67f11cf78",
                    "status": "SUCCESS",
                    "response": "Task complete.",
                    "usage": {"input_tokens": 10000, "output_tokens": 50, "thinking_tokens": 0, "cache_read_tokens": 7500, "total_tokens": 10050},
                },
            },
        ]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "model_events.jsonl"
            raw.write_text("".join(json.dumps(e) + "\n" for e in events))
            parsed = parse_events(raw, "agy")
            write_derived_events(root, parsed, "agy")
            self.assertEqual(parsed.tools_called, ["initialize", "finish"])
            self.assertTrue(parsed.formal_finish_observed)
            self.assertTrue(successful_finish_event(events[4]))
            msgs = [e["content"] for e in parsed.canonical if e["kind"] == "assistant_text"]
            self.assertEqual(msgs, ["Task complete."])
            usage = token_usage(root)
            # Total input = uncached (10000) + cache_read (7500) = 17500, NOT double-counted with step_updates!
            self.assertEqual(usage["input_tokens"], 17500)
            self.assertEqual(usage["cached_input_tokens"], 7500)
            self.assertEqual(usage["output_tokens"], 50)

    def test_v2_inline_image_encoding_and_auxiliary_arg_normalization(self):
        import io
        from PIL import Image
        from manipulation_agent.contracts import Budget
        from manipulation_agent.mcp_server import encode_within_byte_limit
        from manipulation_agent.observations.mock_rgb import MockRGBBackend
        from manipulation_agent.records import Recorder
        from manipulation_agent.runtime.vision_harness import VisionHarness

        # Create a synthetic 512x512 noisy RGB JPEG > 30 KB and verify <= 9600 byte inline encoding
        img = Image.effect_noise((512, 512), 80.0).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        orig_bytes = buf.getvalue()
        self.assertGreater(len(orig_bytes), 20000)

        same_bytes, same_mime = encode_within_byte_limit(orig_bytes, "image/jpeg", 0)
        self.assertEqual(same_bytes, orig_bytes)
        self.assertEqual(same_mime, "image/jpeg")

        compact_bytes, compact_mime = encode_within_byte_limit(orig_bytes, "image/jpeg", 9600)
        self.assertLessEqual(len(compact_bytes), 9600)
        self.assertEqual(compact_mime, "image/webp")

        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run_aux"
            backend = MockRGBBackend(out)
            rec = Recorder(out, {"output": str(out)})
            harness = VisionHarness(
                backend, rec, Budget(wall_seconds=None), profile="basic-skills",
                agent_step_limit=20, goal_skill="none", auxiliary_feedback=True,
            )
            init_res = harness.call("initialize", {}, "req-init")
            rev = init_res["observation"]["revision"]
            ref = init_res["observation"]["images"][0]["image_ref"]

            # Permissive read_skill variants work when auxiliary_feedback=True
            r1 = harness.call("read_skill", {"name": "visual-manipulation/SKILL.md"}, "req-rs-1")
            self.assertTrue(r1["ok"], r1)
            r2 = harness.call("read_skill", {"Name": "visual-manipulation", "Resource": "SKILL.md"}, "req-rs-2")
            self.assertTrue(r2["ok"], r2)
            r3 = harness.call("read_skill", {"skill_name": "pick-and-place"}, "req-rs-3")
            self.assertTrue(r3["ok"], r3)

            # Permissive act(action=...) alias works when auxiliary_feedback=True
            a1 = harness.call("act", {"action": "toggle_on", "revision": rev, "target": {"image_ref": ref, "point": [0.5, 0.5]}}, "req-act-1")
            self.assertTrue(a1["ok"], a1)


if __name__ == "__main__":
    unittest.main()
