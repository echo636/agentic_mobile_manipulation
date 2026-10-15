"""Report-only HTTP server with shared themes and unchanged media byte ranges."""
import argparse
import html
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import urlsplit

ASSETS = Path(__file__).with_name('report_assets')
THEME_HEAD = (b'\n<link rel="stylesheet" href="/_report_theme/theme.css">'
              b'<script src="/_report_theme/theme.js"></script>\n')


def current_replay_html(data):
    """Serve current replay UI even when a frozen experiment publishes an old shell.

    Raw pages, JSON, model notes, scores and media on disk remain immutable.
    Only the known replay template with its explicit JSON archive is eligible.
    """
    payload = re.search(br'<script type="application/json" id="replay-data">(.*?)</script>', data, re.S)
    if not payload or b'id="video-workspace"' not in data:
        return data
    from .replay_shell import render_replay_shell
    title = re.search(br'<title>(.*?)</title>', data, re.S)
    back = re.search(br'<a class="back" href="([^"]*)"', data)
    output = render_replay_shell(payload[1].decode('utf-8'),
        title=html.unescape(title[1].decode('utf-8')).removesuffix(' · Replay') if title else 'Episode',
        back_url=html.unescape(back[1].decode('utf-8')) if back else '/').encode('utf-8')
    for name in (b'replay_audit.json', b'video.json', b'model_public_events.jsonl'):
        attribute = b' href="' + name + b'"'
        if attribute not in data:
            output = output.replace(attribute, b'')
    assert re.search(br'<script type="application/json" id="replay-data">(.*?)</script>', output, re.S)[1] == payload[1]
    return output


def themed_html(data):
    """Add presentation resources without parsing/reencoding embedded evidence."""
    if THEME_HEAD in data:
        return data
    boundary = re.search(br'<(?:body|script|style)\b', data, re.I)
    prolog = data[:boundary.start()] if boundary else data
    head = re.search(br'<head(?:\s[^>]*)?>', prolog, re.I)
    if head:
        position = head.end()
    else:
        opening = re.search(br'<html(?:\s[^>]*)?>|<!doctype[^>]*>', prolog, re.I)
        position = opening.end() if opening else 0
    return data[:position] + THEME_HEAD + data[position:]


class ReportHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.byte_range = None
        url_path = urlsplit(self.path).path
        if url_path.startswith('/_report_theme/'):
            name = url_path.removeprefix('/_report_theme/')
            if name not in {'theme.css', 'theme.js'}:
                self.send_error(404)
                return None
            return self.send_bytes((ASSETS / name).read_bytes(), self.guess_type(name))
        path = Path(self.translate_path(self.path))
        if not path.resolve().is_relative_to(Path(self.directory).resolve()):
            self.send_error(403)
            return None
        if path.is_dir() and url_path.endswith('/'):
            for index in ('index.html', 'index.htm'):
                if (path / index).is_file():
                    path = path / index
                    break
        if not path.resolve().is_relative_to(Path(self.directory).resolve()):
            self.send_error(403)
            return None
        if path.is_file() and path.suffix.lower() in {'.html', '.htm'}:
            data = path.read_bytes()
            if path.name == 'replay.html':
                data = current_replay_html(data)
            return self.send_bytes(themed_html(data), 'text/html; charset=utf-8')
        value = self.headers.get('Range')
        if not value or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
        if not match or not any(match.groups()):
            self.send_error(400, 'Single byte range required')
            return None
        left, right = match.groups()
        start = int(left) if left else max(0, size - int(right))
        end = min(int(right), size - 1) if left and right else size - 1
        if start > end or start >= size:
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        stream = path.open('rb')
        stream.seek(start)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Last-Modified', self.date_time_string(path.stat().st_mtime))
        self.end_headers()
        self.byte_range = (start, end)
        return stream

    def send_bytes(self, data, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        return BytesIO(data)

    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def copyfile(self, source, outputfile):
        try:
            if self.byte_range is None:
                return super().copyfile(source, outputfile)
            remaining = self.byte_range[1] - self.byte_range[0] + 1
            while remaining:
                data = source.read(min(1024 * 1024, remaining))
                if not data:
                    break
                outputfile.write(data)
                remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--bind', default='0.0.0.0')
    args = parser.parse_args()
    ThreadingHTTPServer((args.bind, args.port), partial(
        ReportHandler, directory=str(args.directory.resolve()))).serve_forever()


if __name__ == '__main__':
    main()
