"""Browser checks for complete public tool traces; fixtures are not task runs."""
import base64
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


ASSET = Path(__file__).parents[1] / "src/manipulation_agent/replay_assets/trace.js"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZWQAAAAASUVORK5CYII="
)


def image(view="front", reference=None, file=None, verification="matched_archive_sha256"):
    return {"view": view, "image_ref": reference or view, "file": file or f"frames/{view}.png",
            "sha256": "fixture", "verification": verification}


def event(kind, sequence, **values):
    return {"kind": kind, "sequence": sequence, "source": "model_events.jsonl",
            "source_event_index": sequence + 1, **values}


def result(sequence, call_id="observe", images=None, **values):
    images = images if images is not None else [image(view) for view in ("front", "right", "back", "left")]
    return event("tool_result", sequence, call_id=call_id, tool="observe", step=1,
                 images=images, image_count=len(images), text_blocks=['{"ok":true}', "Exact second block"],
                 image_delivery={"status": "verified", "attachment_count": len(images),
                                 "verified_count": len(images), "diagnostics": []}, **values)


@unittest.skipUnless(sync_playwright, "optional Playwright dependency")
class ToolTraceBrowserTests(unittest.TestCase):
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
        self.page = self.browser.new_page(viewport={"width": 1200, "height": 900})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("https://trace.test/reports/run/**", lambda route: route.fulfill(content_type="image/png", body=PNG))
        self.page.set_content('''<base href="https://trace.test/reports/run/replay.html">
          <style>.tt-image-link,.tt-image{display:block}.tt-image{width:64px;height:64px}
          .tt-overlay-wrap{position:relative}.tt-point-marker{position:absolute;pointer-events:none}</style>
          <main id="tool-trace-feed"></main>''')
        self.page.add_script_tag(content=ASSET.read_text())

    def render(self, entries, **extra):
        data = {"model_transcript": entries, "steps": [], **extra}
        self.page.evaluate("""data => {
          window.replays=[]; window.opened=[];
          window.trace=new ReplayToolTrace(document.getElementById('tool-trace-feed'),data,{
            onReplay: step => replays.push(step),
            onImage: (image,context) => opened.push({image,url:context.url,target:context.target})
          });
        }""", data)

    def test_complete_chronology_and_original_text_are_visible(self):
        original = "Exact <script>window.injected=true</script>\nSecond line " + "全文 " * 100
        entries = [event("assistant", 1, text=original),
                   event("provider_summary", 2, text="Exact returned summary"),
                   event("tool_call", 3, call_id="observe", tool="observe", arguments={}, step=1),
                   result(4), event("assistant", 5, text="Exact final message")]
        self.render(entries)
        self.assertEqual(self.page.locator(".tt-entry:visible").count(), 4)
        self.assertEqual(self.page.locator(".tt-original").first.text_content(), original)
        self.assertIn("Exact final message", self.page.locator("#tool-trace-feed").inner_text())
        self.assertEqual(self.page.locator(".tt-summary .tt-label").inner_text(), "推理摘要 · 接口原文")
        self.assertEqual(self.page.locator(".tt-tool").get_attribute("data-result-sequence"), "4")
        self.assertEqual(self.page.locator(".tt-tool img:visible").count(), 4)
        self.assertEqual(self.page.locator(".tt-detail[open]").count(), 0)
        self.assertIn("Exact second block", self.page.locator(".tt-tool").text_content())
        self.assertIsNone(self.page.evaluate("window.injected"))
        self.assertEqual(self.page.locator("#tool-trace-feed script").count(), 0)
        self.assertEqual(self.errors, [])

    def test_interleaved_message_prevents_tool_card_merge(self):
        self.render([event("tool_call", 1, call_id="one", tool="observe", arguments={}),
                     event("assistant", 2, text="Text recorded while waiting"),
                     result(3, call_id="one"),
                     event("tool_call", 4, call_id="unfinished", tool="finish", arguments={})])
        self.assertEqual(self.page.locator(".tt-entry").evaluate_all("els=>els.map(e=>e.dataset.kind)"),
                         ["tool_call", "assistant", "tool_result", "tool_call"])
        self.assertEqual(self.page.locator(".tt-entry").nth(2).locator("img").count(), 4)
        self.assertNotIn("未记录工具返回", self.page.locator(".tt-entry").first.inner_text())
        self.assertIn("未记录工具返回", self.page.locator(".tt-entry").last.inner_text())

    def test_missing_attachments_never_use_backend_observations(self):
        missing = result(2, images=[])
        missing.update(image_count=4, image_delivery={"status": "unverified", "attachment_count": 4,
                       "verified_count": 0, "diagnostics": [{"code": "raw_missing"}]})
        self.render([event("tool_call", 1, call_id="observe", tool="observe", arguments={}, step=1), missing],
                    steps=[{"index": 1, "before": {"images": [image()]}, "after": {"images": [image()]},
                            "new_observation": True}])
        self.assertEqual(self.page.locator(".tt-image").count(), 0)
        self.assertIn("未用后台观测补图", self.page.locator("#tool-trace-feed").inner_text())
        self.assertIn("raw_missing", self.page.locator("#tool-trace-feed").text_content())

    def test_navigation_marker_uses_previously_verified_attachment_only(self):
        entries = [result(1, images=[image(file="frames/before.png")]),
                   event("tool_call", 2, call_id="navigate", tool="act", step=2,
                         arguments={"primitive": "navigate_to", "target": {"image_ref": "front", "point": [.25, .75]}}),
                   result(3, call_id="navigate", images=[image(file="frames/after.png")])]
        self.render(entries)
        self.assertEqual(self.page.locator(".tt-point-marker").count(), 1)
        self.assertEqual(self.page.locator(".tt-target img").get_attribute("src"),
                         "https://trace.test/reports/run/frames/before.png")
        self.assertEqual(self.page.locator(".tt-point-marker").evaluate("e=>[e.style.left,e.style.top]"), ["25%", "75%"])
        self.assertIn("不是机器人最终位置", self.page.locator(".tt-target").inner_text())
        self.page.locator(".tt-target a").click()
        opened = self.page.evaluate("opened")
        self.assertEqual(opened[0]["target"]["point"], [.25, .75])
        self.assertEqual(self.page.evaluate("replays"), [])
        self.page.locator(".tt-entry").last.locator(".tt-replay").click()
        self.assertEqual(self.page.evaluate("replays"), [2])
        self.assertEqual(self.page.locator(".tt-image").count(), 3)
        self.assertEqual(self.errors, [])

    def test_unverified_future_and_unsafe_files_cannot_supply_target(self):
        unknown = image(verification="not_verified")
        bad_files = ["../outside.png", "https://external.test/x.png", "javascript:alert(1)"]
        entries = [result(1, images=[unknown] + [image(file=path) for path in bad_files]),
                   event("tool_call", 2, call_id="nav", tool="act",
                         arguments={"primitive": "navigate_to", "target": {"image_ref": "front", "point": [.5, .5]}}),
                   result(3, call_id="nav", images=[image(verification="raw_attachment_sha256")])]
        self.render(entries)
        self.assertEqual(self.page.locator(".tt-target").count(), 0)
        self.assertEqual(self.page.locator(".tt-image").count(), 1)
        self.assertIn("先前工具返回原图未核验", self.page.locator("#tool-trace-feed").inner_text())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
