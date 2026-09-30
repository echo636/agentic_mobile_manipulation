from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def read_run(output: Path) -> dict:
    """Read an immutable run plus an externally observed process termination.

    A native crash cannot finalize its Python recorder. Never rewrite that raw
    file or invent a final evaluator result; attach the supervisor evidence.
    """
    run=json.loads((output/'run.json').read_text())
    termination=output/'termination.json'
    if termination.exists():
        record=json.loads(termination.read_text())
        run={**run,'raw_recorder_status':run['status'],'status':record['status'],
             'task_success':None,'failure':record.get('reason'),'external_termination':record}
    return run


def source_version() -> dict:
    root = Path(__file__).resolve().parents[2]
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    files = sorted((root / "src").rglob("*.py"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
            "source_sha256": digest.hexdigest()}


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
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
        return event_id

    def finish(self, result: dict) -> None:
        self.run.update(result, finished_at=now())
        write_json(self.output / "run.json", self.run)
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
