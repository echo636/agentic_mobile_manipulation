"""Local supervisor checkpoints with retryable, asynchronous NAS publication.

Only supervisor metadata is replicated here. Simulator/controller artifacts and
resource ownership checks retain their own validation and failure semantics.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from .records import now


def atomic_text(path: Path, text: str, *, durable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Fresh, exclusively created inode: concurrent publishers and a shared
    # filesystem's delayed truncation must never reuse another write's buffer.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    replaced = False
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(text)
            if durable:
                stream.flush()
                os.fsync(stream.fileno())
        temporary.replace(path)
        replaced = True
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)
    if durable:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


class BatchStorage:
    def __init__(self, root: Path, reports: Path, manifest: dict, *, spool=None,
                 retry_seconds: float = 5, renderer=None):
        self.root, self.reports = Path(root), Path(reports)
        identity = hashlib.sha256(str(self.root.absolute()).encode()).hexdigest()[:24]
        self.spool = Path(spool) if spool else Path(f"/var/tmp/mas-batch-state-{os.getuid()}") / identity
        self.spool.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.identity = {"batch": str(self.root.absolute()),
                         "manifest_sha256": hashlib.sha256(encode(manifest).encode()).hexdigest()}
        self.manifest = deepcopy(manifest)
        self.condition = threading.Condition(threading.RLock())
        self.retry_seconds = retry_seconds
        self.renderer = renderer
        self.revision = self.synced_revision = 0
        self.snapshot = None
        self.journal_entries = []
        self.error = self.local_error = None
        self.failures = 0
        self.closed = False
        self.thread = None
        self._published = {}
        try:
            saved = json.loads((self.spool / "checkpoint.json").read_text())
        except FileNotFoundError:
            saved = None
        if saved:
            if saved["identity"] != self.identity:
                raise ValueError("Local supervisor checkpoint belongs to another batch/manifest")
            self.snapshot = saved["snapshot"]
            self.revision = saved["revision"]
            self.journal_entries = saved["journal_entries"]
        # Check local durability before any simulator can be launched.
        self._checkpoint()

    def recovered_rows(self) -> dict:
        if not self.snapshot:
            return {}
        return {r["run_id"]: deepcopy(r) for r in self.snapshot["progress"]["tasks"]}

    def status(self) -> dict:
        with self.condition:
            return {"status": "degraded" if self.error or self.local_error else
                    ("pending" if self.synced_revision < self.revision else "passed"),
                    "local_spool": str(self.spool), "revision": self.revision,
                    "synced_revision": self.synced_revision, "failures": self.failures,
                    "last_error": self.error, "local_checkpoint_error": self.local_error,
                    "updated_at": now()}

    def _checkpoint(self):
        atomic_text(self.spool / "checkpoint.json", encode({
            "identity": self.identity, "revision": self.revision,
            "snapshot": self.snapshot, "journal_entries": self.journal_entries,
        }), durable=True)

    def _warn(self, error, phase):
        message = {"at": now(), "phase": phase, "error": str(error),
                   "type": type(error).__name__, "errno": getattr(error, "errno", None)}
        if phase == "local_checkpoint":
            self.local_error = message
        else:
            self.error = message
        self.failures += 1
        try:
            with (self.spool / "storage_errors.jsonl").open("a") as stream:
                stream.write(json.dumps(message) + "\n")
        except OSError:
            pass
        try:
            os.write(2, ("Supervisor storage degraded; episode remains active: " +
                         json.dumps(message) + "\n").encode())
        except OSError:
            pass

    def _save_local(self):
        try:
            self._checkpoint()
            self.local_error = None
        except OSError as exc:
            # Retain the current in-memory state and retry. Admission checks
            # prevent additional episodes until local persistence recovers.
            self._warn(exc, "local_checkpoint")

    def submit(self, progress, validation, dashboard=None):
        with self.condition:
            self.snapshot = deepcopy({"progress": progress, "validation": validation,
                                      "dashboard": dashboard})
            self.revision += 1
            self._save_local()
            self._start()
            self.condition.notify_all()

    def journal(self, message):
        with self.condition:
            self.journal_entries.append({"id": uuid.uuid4().hex, "at": now(), "message": str(message)})
            self.revision += 1
            self._save_local()
            self._start()
            self.condition.notify_all()

    def _start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name="batch-storage-sync", daemon=True)
            self.thread.start()

    def _write(self, path, text):
        # One publishing thread serializes versions. A slow/failed old write
        # cannot overwrite a newer successfully published version.
        digest = hashlib.sha256(text.encode()).digest()
        if self._published.get(path) != digest:
            atomic_text(path, text)
            self._published[path] = digest

    def _publish(self, snapshot, entries):
        if snapshot:
            progress = snapshot["progress"]
            for row in progress["tasks"]:
                self._write(self.root / "records" / (row["run_id"] + ".json"), encode(row))
            files = {"progress.json": encode(progress),
                     "validation.json": encode(snapshot["validation"]),
                     "run_records.json": encode({"updated_at": progress["updated_at"], "runs": progress["tasks"]})}
            for name, value in files.items():
                self._write(self.root / name, value)
            for name in ("progress.json", "validation.json"):
                self._write(self.reports / "behavior100" / name, files[name])
            self._write(self.reports / "behavior100" / "manifest.json", encode(self.manifest))
            dashboard = snapshot["dashboard"]
            if dashboard is None:
                dashboard = self.renderer(progress)
            self._write(self.reports / "behavior100.html", dashboard)
        # Append to the operational journal; never replace outside audit entries.
        # Event IDs also deduplicate a retry after an uncertain append/close.
        journal = self.root / "journal.md"
        try:
            history = journal.read_text()
        except FileNotFoundError:
            history = ""
        for event in entries:
            marker = "<!-- supervisor-event:" + event["id"] + " -->"
            if marker not in history:
                line = f'\n- {event["at"]} · {event["message"]} {marker}\n'
                with journal.open("a") as stream:
                    stream.write(line)
                history += line
        self._write(self.reports / "behavior100" / "journal.md", history)

    def _loop(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.local_error or self.revision > self.synced_revision)
                if self.closed:
                    return
                revision, snapshot, entries = self.revision, self.snapshot, list(self.journal_entries)
                if self.local_error:
                    self._save_local()
            try:
                self._publish(snapshot, entries)
            except Exception as exc:
                # Metadata publication must not cancel robot execution, even if
                # a renderer/path bug appears. Preserve the error for diagnosis.
                with self.condition:
                    self._warn(exc, "shared_publication")
            else:
                with self.condition:
                    self.synced_revision = revision
                    self.error = None
                    self.condition.notify_all()
            with self.condition:
                try:
                    atomic_text(self.spool / "sync_status.json", encode(self.status()), durable=True)
                except OSError as exc:
                    self._warn(exc, "local_checkpoint")
                if self.error or self.local_error:
                    # Updates cannot defeat backoff or create an unbounded queue.
                    self.condition.wait_for(lambda: self.closed, timeout=self.retry_seconds)

    def flush(self, timeout=30) -> bool:
        with self.condition:
            target = self.revision
            self._start()
            self.condition.notify_all()
            return self.condition.wait_for(
                lambda: self.synced_revision >= target and not self.local_error, timeout=timeout)

    def close(self, timeout=30) -> bool:
        complete = self.flush(timeout)
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=1)
        return complete
