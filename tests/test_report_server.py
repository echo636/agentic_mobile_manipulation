"""The site-wide theme must not alter recorded payloads or video range responses."""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from manipulation_agent.report_server import ReportHandler, themed_html, current_replay_html

urlopen = build_opener(ProxyHandler({})).open


class QuietHandler(ReportHandler):
    def log_message(self, *args):
        pass


class ReportServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'reports'
        self.root.mkdir()
        self.html = b'<!doctype html><html><head><meta charset="utf-8"></head><body><script type="application/json">{"value":"<head>","score":0}</script></body></html>'
        (self.root / 'index.html').write_bytes(self.html)
        (self.root / 'episode.mp4').write_bytes(bytes(range(256)))
        (self.root / 'score.json').write_bytes(b'{"score":0.5}')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(self.root)))
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def test_html_get_head_and_embedded_evidence(self):
        with urlopen(self.base + '/') as response:
            content = response.read()
            self.assertEqual(content, themed_html(self.html))
            self.assertEqual(int(response.headers['Content-Length']), len(content))
            self.assertIn(b'{"value":"<head>","score":0}', content)
        with urlopen(Request(self.base + '/', method='HEAD')) as response:
            self.assertEqual(int(response.headers['Content-Length']), len(content))
            self.assertEqual(response.read(), b'')
        self.assertEqual((self.root / 'index.html').read_bytes(), self.html)

    def test_media_ranges_and_json_remain_exact(self):
        for header, expected in [('bytes=10-19', bytes(range(10, 20))), ('bytes=-4', bytes(range(252, 256)))]:
            with urlopen(Request(self.base + '/episode.mp4', headers={'Range': header})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
        with urlopen(self.base + '/score.json') as response:
            self.assertEqual(response.read(), b'{"score":0.5}')
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + '/episode.mp4', headers={'Range': 'bytes=300-400'}))
        self.assertEqual(error.exception.code, 416)

    def test_assets_and_directory_index_cannot_escape_root(self):
        outside = self.root.parent / 'outside.html'
        outside.write_text('private fixture')
        folder = self.root / 'nested'
        folder.mkdir()
        (folder / 'index.html').symlink_to(outside)
        for path, code in [('/nested/', 403), ('/_report_theme/../report_server.py', 404)]:
            with self.assertRaises(HTTPError) as error:
                urlopen(self.base + path)
            self.assertEqual(error.exception.code, code)
        with urlopen(self.base + '/_report_theme/theme.js?v=1') as response:
            self.assertIn(b'mas-report-theme', response.read())

    def test_legacy_headless_html_and_duplicate_injection(self):
        legacy = b'<!doctype html><html><meta charset="utf-8"><style>body{color:black}</style><p>History</p>'
        output = themed_html(legacy)
        self.assertTrue(output.startswith(b'<!doctype html>'))
        self.assertEqual(themed_html(output), output)
        self.assertTrue(output.endswith(b'<p>History</p>'))
        evidence = b'<script type="application/json">{"text":"<head>literal"}</script>'
        self.assertIn(evidence, themed_html(legacy + evidence))

    def test_frozen_replay_gets_current_shell_without_rewriting_evidence_or_disk(self):
        payload = b'{"run_id":"fixture","text":"@@JS@@ @@CSS@@ @@DATA@@","score":0.125}'
        old = (b'<html><head><title>Old &amp; task \xc2\xb7 Replay</title></head><body>'
               b'<a class="back" href="/historical/">Back</a><div id="video-workspace"></div>'
               b'<script type="application/json" id="replay-data">' + payload + b'</script></body></html>')
        path = self.root / 'replay.html'
        path.write_bytes(old)
        with urlopen(self.base + '/replay.html?view=goals') as response:
            body = response.read()
            self.assertIn(b'window.ReplayGoalTree = class', body)
            self.assertIn(payload, body)
            self.assertIn(b'href="/historical/"', body)
            self.assertIn(b'<title>Old &amp; task \xc2\xb7 Replay</title>', body)
        self.assertEqual(path.read_bytes(), old)
        # A later publication by the frozen coordinator still gets the current UI.
        path.write_bytes(old.replace(b'0.125', b'0.25'))
        with urlopen(self.base + '/replay.html') as response:
            body = response.read()
            self.assertIn(b'window.ReplayGoalTree = class', body)
            self.assertIn(payload.replace(b'0.125', b'0.25'), body)

    def test_non_replay_html_is_not_rebuilt(self):
        self.assertEqual(current_replay_html(self.html), self.html)


if __name__ == '__main__':
    unittest.main()
