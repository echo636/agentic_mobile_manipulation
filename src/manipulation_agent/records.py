from __future__ import annotations

import hashlib
import html
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonlAppend:
    """Episode-owned append handle; every complete row is flushed for live readers."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def append(self, row):
        if self.stream is None:
            self.stream = self.path.open('a', encoding='utf-8')
        self.stream.write(json.dumps(row) + '\n')
        self.stream.flush()

    def close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.close()


def _finite_diagnostic(value, path, warnings):
    """Keep invalid private numbers as null plus their exact path and value.

    This is only for diagnostic events: public responses and evaluator records
    remain strict, and no invalid score is converted into a successful result.
    """
    if isinstance(value, float) and not math.isfinite(value):
        warnings.append({'path': path, 'value': str(value)})
        return None
    if isinstance(value, dict):
        return {key: _finite_diagnostic(item, path + [key], warnings)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_diagnostic(item, path + [index], warnings)
                for index, item in enumerate(value)]
    return value


def write_json(path: Path, value: dict) -> None:
    # A shared fixed .tmp name can be truncated/replaced by another writer.
    # Publish a complete fresh inode, never an overwritten staging file.
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    replaced = False
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            stream.write(text)
        tmp.replace(path)
        replaced = True
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)


def read_run(output: Path) -> dict:
    """Read an immutable run plus an externally observed process termination.

    A native crash cannot finalize its Python recorder. Never rewrite that raw
    file or invent a final evaluator result; attach the supervisor evidence.
    """
    run=json.loads((output/'run.json').read_text())
    termination=output/'termination.json'
    if termination.exists():
        record=json.loads(termination.read_text())
        run={**run,'raw_recorder_status':run['status'],'external_termination':record}
        if run['status']=='running':
            run.update(status=record['status'],task_success=None,failure=record.get('reason'))
        # The supervisor can know a hard wall-clock outcome even if CUDA died
        # before Python could persist a score. Keep the score unknown rather
        # than inventing Q=0; termination is nevertheless a final outcome.
        for key in ('episode_outcome', 'termination_reason', 'deadline_exceeded',
                    'episode_deadline_unix', 'evaluation_after_deadline', 'evaluation_finished_after_deadline',
                    'deadline_expired_at_finish', 'execution_finished_at_unix',
                    'evaluation_finished_at_unix'):
            if key in record:
                run[key] = record[key]
    return run


def release_source_digest(root: Path) -> str:
    """Hash the executable custom-task source with the release manifest format."""
    files = [*sorted((root / 'src/manipulation_agent').rglob('*.py')),
             root / 'scripts/validate_custom_task_physics.py']
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def source_version(root: Path | None = None) -> dict:
    root = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    files = sorted((root / "src").rglob("*.py"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    freeze = root / 'source_freeze.json'
    if freeze.is_file():
        manifest = json.loads(freeze.read_text())
        release_digest = release_source_digest(root)
        commit = manifest.get('source_commit', '')
        if (len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit)
                or manifest.get('source_sha256') != release_digest):
            raise ValueError('Frozen executable source does not match source_freeze.json')
        return {'commit': commit, 'dirty': False, 'source_sha256': digest.hexdigest(),
                'release_source_sha256': release_digest, 'identity_method': 'verified_source_freeze'}
    # An archive stored underneath some unrelated checkout must not inherit
    # that checkout's commit through git's parent-directory discovery.
    git_root = git('rev-parse', '--show-toplevel')
    own_checkout = git_root is not None and Path(git_root).resolve() == root
    return {'commit': git('rev-parse', 'HEAD') if own_checkout else None,
            'dirty': bool(git('status', '--porcelain')) if own_checkout else None,
            'source_sha256': digest.hexdigest(),
            'release_source_sha256': (release_source_digest(root)
                if (root / 'scripts/validate_custom_task_physics.py').is_file() else None),
            'identity_method': 'git_checkout' if own_checkout else 'unversioned'}


class Recorder:
    def __init__(self, output: Path, config: dict):
        output.mkdir(parents=True, exist_ok=False)
        self.output = output
        self.sequence = 0
        self.run = {"schema_version": 1, "run_id": output.name, "status": "running",
                    "started_at": now(), "host": platform.node(), "pid": os.getpid(),
                    "interpreter": sys.executable, "unit": os.environ.get("MAS_UNIT"),
                    "gpu_uuid": os.environ.get("MAS_GPU_UUID"),
                    "output_path": str(output.resolve()), "source": source_version(),
                    "config": config, "task_success": None}
        write_json(output / "run.json", self.run)
        shutil.copytree(Path(__file__).resolve().parents[2] / "src", output / "source_snapshot",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    def event(self, kind: str, payload: dict) -> str:
        self.sequence += 1
        event_id = f"event-{self.sequence:05d}"
        event = {"id": event_id, "at": now(), "kind": kind, **payload}
        if kind in {'private_executor_result', 'private_executor_error'}:
            warnings = []
            event = _finite_diagnostic(event, [], warnings)
            if warnings:
                event['serialization_warnings'] = warnings
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        return event_id

    def finish(self, result: dict, *, render: bool = True) -> None:
        self.run.update(result, finished_at=now())
        write_json(self.output / "run.json", self.run)
        if render:
            self.render()

    def render(self) -> None:
        events_file = self.output / "events.jsonl"
        events = [json.loads(line) for line in events_file.read_text().splitlines()] if events_file.exists() else []
        esc = lambda value: html.escape(json.dumps(value, ensure_ascii=False, indent=2))
        cards = "".join(f'<details><summary>{html.escape(e["id"])} · {html.escape(e["kind"])}</summary><pre>{esc(e)}</pre></details>' for e in events)
        images = "".join(f'<figure><img loading="lazy" src="frames/{p.name}"><figcaption>{html.escape(p.name)}</figcaption></figure>' for p in sorted((self.output / "frames").glob("*.jpg")))
        page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        page += '<title>Manipulation episode</title><style>body{font:16px system-ui;margin:32px auto;max-width:1200px;padding:0 18px;background:#f4f7fa;color:#182b42}pre{white-space:pre-wrap;overflow-wrap:anywhere}details,article{padding:16px;background:white;border:1px solid #dce3eb;margin:10px 0;border-radius:9px}summary{cursor:pointer}#frames{display:flex;flex-wrap:wrap}figure{margin:8px}img{width:280px;max-width:100%}.note{border-left:4px solid #d18a22;padding:12px}</style>'
        page += f'<h1>{html.escape(self.output.name)}</h1><p class="note">研究评测：symbolic 执行器与 oracle 状态须按记录区分。工具通过、模型声称完成与 BDDL 任务成功是三件事。</p>'
        page += f'<p><a href="run.json">结构化运行记录</a> · <a href="events.jsonl">完整事件轨迹</a></p><article><pre>{esc(self.run)}</pre></article><h2>逐步事件</h2>{cards}<h2>机器人观测</h2><div id="frames">{images}</div></html>'
        (self.output / "index.html").write_text(page, encoding="utf-8")
        if self.run.get('config', {}).get('observation_mode') == 'rgb_only' or self.run.get('config', {}).get('backend') == 'omnigibson':
            from .replay import render_replay
            render_replay(self.output)
            page = page.replace('<h1>', '<p><a href="replay.html">▶ 打开逐步 Replay</a></p><h1>', 1)
            (self.output / 'index.html').write_text(page, encoding='utf-8')
