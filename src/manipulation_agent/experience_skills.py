"""Persistent task/action experience, with reviewed versions and offline candidates.

This module has no simulator or model dependencies. Candidates never become live
instructions automatically; a curator supplies the replacement skill document.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from .records import now, write_json


PROTOCOLS = {"rgb_only", "oracle_waypoints"}
CLAIMS = {"whole_task_success", "engineering_success", "partial_or_failure"}


def configured_root():
    value = os.environ.get("MAS_EXPERIENCE_ROOT")
    if value:
        return Path(value)
    runtime = os.environ.get("MAS_V02_RUNTIME")
    return Path(runtime) / "experience_skills" if runtime else None


def checked_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value):
        raise ValueError("Use a task/action identifier, not a path")
    return value


def skill_name(kind, key, protocol):
    slug = key.lower().replace("_", "-")
    prefix = "behavior-" if kind == "task" else "oracle-" if protocol == "oracle_waypoints" else "rgb-"
    name = prefix + slug
    if len(name) > 64:
        name = name[:51].rstrip("-") + "-" + hashlib.sha256(key.encode()).hexdigest()[:12]
    return name


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protocol_for(config):
    if config.get("observation_mode") == "oracle_3d_waypoints" or config.get("agent_profile") == "oracle_waypoints":
        return "oracle_waypoints"
    if config.get("observation_mode") == "rgb_only":
        return "rgb_only"
    raise ValueError("Source must record its RGB or Oracle observation protocol")


def source_facts(run):
    run = Path(run).resolve()
    env = run / "env" if (run / "env/run.json").is_file() else run
    record = json.loads((env / "run.json").read_text())
    if record.get("status") in {None, "starting", "running"}:
        raise ValueError("Archive only a finished episode")
    config = record.get("config", {})
    task = checked_key(config.get("task"))
    engineering_path = run / "engineering_validation.json"
    engineering = json.loads(engineering_path.read_text()) if engineering_path.is_file() else {}
    evaluation = record.get("evaluation") or {}
    official = evaluation.get("official_task_success", record.get("task_success"))
    if official is not None and type(official) is not bool:
        raise ValueError("Official success must be a boolean or unknown")
    hashes = {"run.json": file_hash(env / "run.json")}
    for filename in ("events.jsonl", "scene_geometry_repair.json"):
        if (env / filename).is_file():
            hashes[filename] = file_hash(env / filename)
    for filename in ("engineering_validation.json", "transfer_validation.json"):
        if (run / filename).is_file():
            hashes[filename] = file_hash(run / filename)
    repair = json.loads((env / "scene_geometry_repair.json").read_text()) if (env / "scene_geometry_repair.json").is_file() else {}
    facts = {"run": str(run), "run_id": record.get("run_id", run.name), "task": task,
             "protocol": protocol_for(config), "backend": config.get("backend"),
             "instance": config.get("instance"), "seed": config.get("seed"),
             "ik_backend": config.get("ik_backend"), "grasping_mode": config.get("grasping_mode"),
             "official_task_success": official,
             "engineering_success": engineering.get("engineering_success", engineering.get("success")),
             "sampled_goal_checks_passed": engineering.get("sampled_goal_checks_passed"),
             "scene_floor_repairs": len(repair.get("added_floors", [])), "sha256": hashes}
    return facts, record, env


class ExperienceStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def entry(self, kind, key, protocol):
        if kind not in {"task", "action"} or protocol not in PROTOCOLS:
            raise ValueError("Unknown experience kind or observation protocol")
        path = self.root / (kind + "s") / checked_key(key) / protocol
        # Curated source roots can be external; entry components cannot redirect.
        for part in (path.parent.parent, path.parent, path):
            if part.is_symlink():
                raise ValueError("Experience directory symlinks are not allowed")
        return path

    def current(self, kind, key, protocol):
        folder = self.entry(kind, key, protocol)
        pointer = folder / "current.json"
        if not pointer.exists():
            return None
        if pointer.is_symlink():
            raise ValueError("Experience pointer symlinks are not allowed")
        metadata = json.loads(pointer.read_text())
        if any(metadata.get(k) != v for k, v in {"kind": kind, "key": key, "protocol": protocol,
                                                "name": skill_name(kind, key, protocol)}.items()):
            raise ValueError("Experience pointer identity mismatch")
        revision = metadata.get("revision")
        if type(revision) is not int or revision < 1:
            raise ValueError("Invalid experience revision")
        version = folder / "revisions" / f"{revision:06d}"
        for part in (version.parent, version, version / "SKILL.md", version / "record.json"):
            if part.is_symlink():
                raise ValueError("Experience revision symlinks are not allowed")
        if file_hash(version / "SKILL.md") != metadata.get("document_sha256") or file_hash(version / "record.json") != metadata.get("record_sha256"):
            raise ValueError("Experience revision integrity mismatch")
        return {**metadata, "directory": version}

    @contextlib.contextmanager
    def locked(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / ".write.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def publish(self, kind, key, protocol, *, description, body, runs, claim,
                expected_revision, action_keys=()):
        """Publish a complete, curated replacement; retain all earlier versions."""
        folder = self.entry(kind, key, protocol)
        if claim not in CLAIMS or not runs:
            raise ValueError("Supply evidence runs and a supported claim level")
        if not isinstance(description, str) or not description.strip() or len(description) > 500:
            raise ValueError("Supply a concise skill description")
        if not isinstance(body, str) or not body.strip() or len(body.encode()) > 24000:
            raise ValueError("Supply a curated document of at most 24 KB")
        actions = sorted({checked_key(k) for k in action_keys})
        sources = []
        for run in runs:
            facts, _, _ = source_facts(run)
            if facts["protocol"] != protocol or (kind == "task" and facts["task"] != key):
                raise ValueError("Evidence task or observation protocol mismatch")
            if claim == "whole_task_success" and facts["official_task_success"] is not True:
                raise ValueError("Whole-task success requires the independent official score")
            if claim == "engineering_success" and facts["engineering_success"] is not True:
                raise ValueError("Engineering success requires independent engineering validation")
            sources.append({**facts, "claim": claim})
        with self.locked(folder):
            previous = self.current(kind, key, protocol)
            active_revision = previous["revision"] if previous else 0
            if type(expected_revision) is not int or expected_revision != active_revision:
                raise ValueError("Stale experience revision; read the current document before updating")
            if previous:
                old_record = json.loads((previous["directory"] / "record.json").read_text())
                old_sources = old_record["sources"]
            else:
                old_sources = []
            combined = {s["run"]: s for s in old_sources}
            combined.update({s["run"]: s for s in sources})
            versions = folder / "revisions"
            versions.mkdir(exist_ok=True)
            revision = max([int(p.name) for p in versions.iterdir() if p.is_dir() and p.name.isdigit()] + [0]) + 1
            version = versions / f"{revision:06d}"
            version.mkdir()
            name = skill_name(kind, key, protocol)
            text = ("---\nname: " + name + "\ndescription: " + json.dumps(description, ensure_ascii=False) +
                    "\n---\n\n# " + key.replace("_", " ") + "\n\n" +
                    f"Scope: {protocol}; {kind}={key}; revision={revision}.\n" +
                    "Past experience only. Recompute targets from the current observations; do not replay old coordinates or object IDs.\n\n" +
                    body.strip() + "\n\n## Recorded evidence\n\n")
            for source in combined.values():
                text += (f"- {Path(source['run']).name}: task={source['task']}, instance={source['instance']}, seed={source['seed']}; "
                         f"official whole-task success={source['official_task_success']}; engineering success={source['engineering_success']}; "
                         f"claim={source['claim']}; grasping={source['grasping_mode']}; fixed-floor repairs={source['scene_floor_repairs']}.\n")
            (version / "SKILL.md").write_text(text)
            metadata = {"schema_version": 1, "name": name, "kind": kind, "key": key, "protocol": protocol,
                        "revision": revision, "previous_active_revision": active_revision,
                        "description": description, "action_keys": actions, "updated_at": now()}
            write_json(version / "record.json", {**metadata, "sources": list(combined.values()),
                                                "document_sha256": file_hash(version / "SKILL.md")})
            pointer = {**metadata, "document_sha256": file_hash(version / "SKILL.md"),
                       "record_sha256": file_hash(version / "record.json")}
            write_json(folder / "current.json", pointer)
        return pointer

    def activate(self, kind, key, protocol, *, revision, expected_revision):
        folder = self.entry(kind, key, protocol)
        with self.locked(folder):
            current = self.current(kind, key, protocol)
            if current is None or current["revision"] != expected_revision:
                raise ValueError("Stale experience revision")
            if type(revision) is not int or revision < 1:
                raise ValueError("Invalid rollback revision")
            version = folder / "revisions" / f"{revision:06d}"
            if version.is_symlink() or (version / "record.json").is_symlink() or (version / "SKILL.md").is_symlink():
                raise ValueError("Experience revision symlinks are not allowed")
            metadata = json.loads((version / "record.json").read_text())
            if any(metadata.get(k) != v for k,v in {"kind":kind, "key":key, "protocol":protocol, "revision":revision}.items()):
                raise ValueError("Rollback identity mismatch")
            if metadata["document_sha256"] != file_hash(version / "SKILL.md"):
                raise ValueError("Rollback document integrity mismatch")
            pointer = {k:v for k,v in metadata.items() if k != "sources"}
            pointer["record_sha256"] = file_hash(version / "record.json")
            write_json(folder / "current.json", pointer)
            return pointer

    def select(self, task, protocol):
        if protocol not in PROTOCOLS:
            raise ValueError("Unknown observation protocol")
        selected = []
        if task is not None:
            current = self.current("task", checked_key(task), protocol)
            if current:
                selected.append(current)
        actions = self.root / "actions"
        if actions.is_symlink():
            raise ValueError("Action directory symlinks are not allowed")
        if actions.is_dir():
            for action in sorted(actions.iterdir()):
                if action.is_dir():
                    current = self.current("action", action.name, protocol)
                    if current:
                        selected.append(current)
        return selected

    def archive(self, run):
        """Retain offline evidence candidates; never update current skills here."""
        facts, record, env = source_facts(run)
        calls, failures = [], []
        events_path = env / "events.jsonl"
        if events_path.is_file():
            with events_path.open() as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("kind") == "tool_call":
                        args = event.get("arguments", {})
                        calls.append(args.get("primitive", event.get("name")))
                    if event.get("kind") == "tool_result" and event.get("result", {}).get("ok") is False:
                        failures.append({"evidence_id": event["id"], "tool": event.get("name"),
                                         "code": (event["result"].get("error") or {}).get("code")})
        folder = self.root / "candidates" / facts["protocol"] / facts["task"]
        folder.mkdir(parents=True, exist_ok=True)
        identity = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        path = folder / (identity + ".json")
        candidate = {"schema_version": 1, "review_status": "candidate", "source": facts,
                     "actions": calls, "failures": failures,
                     "agent_outcome": record.get("agent_outcome"),
                     "agent_claim_not_independent_validation": (record.get("finish_reason") or "")[:4000]}
        if not path.exists():
            write_json(path, candidate)
        return path

    def inventory(self, tasks):
        rows = []
        for task in sorted(tasks):
            row = {"task": task, "protocols": {}}
            for protocol in sorted(PROTOCOLS):
                entry = self.current("task", task, protocol)
                row["protocols"][protocol] = ({"revision": entry["revision"], "name": entry["name"]}
                                                if entry else None)
            rows.append(row)
        return rows
