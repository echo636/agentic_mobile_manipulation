"""Browser regressions for display timing; fixtures are not experiment evidence.

Run with the docs interpreter and PLAYWRIGHT_BROWSERS_PATH when Chromium is
installed. Core replay evidence tests do not require this optional dependency.
"""
import unittest
import base64

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
        self.page.locator("#view-video").click()

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

    def test_action_target_is_hidden_before_call_and_stays_on_input_image(self):
        data = fixture()
        target = {"image_ref": "input-left", "point": [.25, .75]}
        data["steps"][1]["arguments"].update(primitive="toggle_on", target=target)
        before = {"revision": 1, "images": [{"view": "left", "image_ref": "input-left", "file": "input.png"}]}
        after = {"revision": 2, "images": [{"view": "left", "image_ref": "output-left", "file": "output.png"}]}
        data["steps"][0]["after"] = before
        data["steps"][1].update(before=before, after=after)
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZWQAAAAASUVORK5CYII=")
        self.page.route("https://replay.test/run/*.png", lambda route: route.fulfill(content_type="image/png", body=png))
        self.page.route("https://replay.test/run/replay.html", lambda route: route.fulfill(
            content_type="text/html", body=render_replay_page(data)))
        self.page.goto("https://replay.test/run/replay.html")
        self.page.locator("#view-video").click()
        self.page.locator("#mode-input").click()
        times = self.page.evaluate("""() => ReplayTiming.review(JSON.parse(document.getElementById('replay-data').textContent))
          .segments.filter(s=>s.step===2).map(s=>({phase:s.phase,time:s.start_seconds+.01}))""")
        for segment in times:
            self.page.locator("#video-seek").evaluate("(e,t)=>{e.value=t;e.dispatchEvent(new Event('input'))}", segment["time"])
            panel = self.page.locator("#navigation-target")
            if segment["phase"] == "decision":
                self.assertTrue(panel.is_hidden())
                self.assertIsNone(panel.get_attribute("data-point"))
                continue
            self.page.locator(".navigation-target-image").wait_for(state="visible")
            self.assertEqual(panel.get_attribute("data-image-ref"), "input-left")
            self.assertEqual(panel.get_attribute("data-point"), "[0.25,0.75]")
            self.assertIn("toggle_on", panel.inner_text())
            self.assertIn("操作对象，不代表按钮或夹爪的精确接触点", panel.inner_text())
            expected = "output-left" if segment["phase"] == "result" else "input-left"
            self.page.wait_for_function("ref => document.querySelector('.camera-left canvas').dataset.imageRef === ref", arg=expected)
            if segment["phase"] == "result":
                pixel = self.page.locator(".camera-left canvas").evaluate("e=>Array.from(e.getContext('2d').getImageData(0,0,1,1).data)")
                self.assertEqual(pixel[0], pixel[1])  # Source is gray; no red marker on the output image.
            self.page.locator(".navigation-target-image").click()
            self.assertTrue(self.page.locator("#zoom").is_visible())
            self.assertIn("toggle_on", self.page.locator("#zoom-title").inner_text())
            self.page.locator("#close-zoom").click()
        self.assertEqual(self.errors, [])

    def test_initialization_failure_has_no_fake_playback(self):
        data = fixture()
        data.update(steps=[], model_transcript=[], failure="TimeoutError: fixture initialization failed")
        self.page.goto("about:blank")
        self.page.set_content(render_replay_page(data))
        self.page.locator("#view-video").click()
        self.assertTrue(self.page.locator("#video-play").is_disabled())
        self.assertTrue(self.page.locator("#video-seek").is_disabled())
        self.assertIn(data["failure"], self.page.locator("#conversation").inner_text())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
