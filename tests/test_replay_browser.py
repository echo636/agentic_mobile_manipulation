"""Browser regressions for display timing; fixtures are not experiment evidence.

Run with the docs interpreter and PLAYWRIGHT_BROWSERS_PATH when Chromium is
installed. Core replay evidence tests do not require this optional dependency.
"""
import unittest

from manipulation_agent.replay import render_replay_page

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def fixture():
    transcript, steps = [], []
    for index, tool in enumerate(("observe", "act", "finish"), 1):
        args = {"primitive": "navigate_to"} if tool == "act" else {}
        transcript.extend([
            {"sequence": len(transcript) + 1, "step": index,
             "kind": "assistant", "text": f"Exact model text {index}."},
            {"sequence": len(transcript) + 2, "step": index,
             "kind": "tool_call", "call_id": str(index), "tool": tool,
             "arguments": args},
            {"sequence": len(transcript) + 3, "step": index,
             "kind": "tool_result", "call_id": str(index), "tool": tool,
             "text_blocks": ['{"ok":false,"error":{"code":"fixture_failure"}}']},
        ])
        steps.append({"index": index, "tool": tool, "arguments": args,
                      "status": "failed", "video_start_seconds": .1,
                      "before": None, "after": {"revision": index, "images": []},
                      "result": {"ok": False, "error": {"code": "fixture_failure"}}})
    transcript.append({"sequence": 10, "step": None, "kind": "assistant",
                       "text": "Exact final model text."})
    return {"run_id": "browser-fixture", "config": {"task": "fixture"},
            "instruction": "Browser fixture only", "steps": steps,
            "model_transcript": transcript, "video": None,
            "audit": None, "has_public_trace": True,
            "evaluation_offline_only": {"task_success": False}}


@unittest.skipUnless(sync_playwright, "optional Playwright browser dependency")
class ReplayBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(headless=True)
        except Exception as error:
            cls.runtime.stop()
            raise unittest.SkipTest(f"Chromium not installed: {error}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.set_content(render_replay_page(fixture()))

    def test_default_first_step_and_future_are_hidden(self):
        self.assertEqual(self.page.locator("#current-step").get_attribute("data-step"), "1")
        self.assertEqual(self.page.locator("#current-step").get_attribute("data-phase"), "decision")
        self.assertEqual(self.page.locator(".conversation-entry:visible").count(), 1)
        self.assertEqual(self.page.locator(".conversation-original:visible").inner_text(), "Exact model text 1.")
        self.page.locator("#show-all").check()
        self.assertEqual(self.page.locator(".conversation-entry:visible").count(), 10)
        self.page.locator("#show-all").uncheck()
        self.assertEqual(self.page.locator(".conversation-entry:visible").count(), 1)
        self.assertEqual(self.errors, [])

    def test_same_media_timestamp_steps_and_final_are_reachable(self):
        for index in range(3):
            self.page.locator("#step-track button").nth(index).click()
            self.assertEqual(self.page.locator("#current-step").get_attribute("data-step"), str(index + 1))
            self.assertEqual(self.page.locator(".conversation-entry:visible").count(), index * 3 + 1)
        self.page.locator("#video-seek").evaluate("e=>{e.value=e.max;e.dispatchEvent(new Event('input'))}")
        self.assertEqual(self.page.locator("#current-step").get_attribute("data-phase"), "final")
        self.assertIn("Exact final model text.", self.page.locator("#conversation").inner_text())
        self.assertEqual(self.errors, [])

    def test_tool_and_result_have_distinct_disclosure_and_observations(self):
        times = self.page.evaluate("""() => {
          const data=JSON.parse(document.getElementById('replay-data').textContent);
          return ReplayTiming.review(data).segments.filter(s=>s.step===2)
            .map(s=>({phase:s.phase,time:s.start_seconds+.01}));
        }""")
        for segment in times:
            self.page.locator("#video-seek").evaluate("(e,t)=>{e.value=t;e.dispatchEvent(new Event('input'))}", segment["time"])
            self.assertEqual(self.page.locator("#current-step").get_attribute("data-phase"), segment["phase"])
            expected = 6 if segment["phase"] == "result" else 5 if segment["phase"] == "tool" else 4
            self.assertEqual(self.page.locator(".conversation-entry:visible").count(), expected)
            self.assertEqual(self.page.locator("#input-side").input_value(), "after" if segment["phase"] == "result" else "before")
        self.assertIn("fixture_failure", self.page.locator(".conversation-entry:visible").last.inner_text())
        self.assertEqual(self.errors, [])

    def test_primary_camera_can_change_without_video(self):
        self.page.locator(".camera-spectator").click()
        self.assertTrue(self.page.locator(".camera-spectator").evaluate("e=>e.classList.contains('camera-primary')"))
        self.assertFalse(self.page.locator(".camera-front").evaluate("e=>e.classList.contains('camera-primary')"))
        self.assertEqual(self.errors, [])

    def test_initialization_failure_has_no_fake_playback(self):
        data = fixture()
        data.update(steps=[], model_transcript=[], failure="TimeoutError: fixture initialization failed")
        self.page.set_content(render_replay_page(data))
        self.assertTrue(self.page.locator("#video-play").is_disabled())
        self.assertTrue(self.page.locator("#video-seek").is_disabled())
        self.assertIn(data["failure"], self.page.locator("#conversation").inner_text())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
