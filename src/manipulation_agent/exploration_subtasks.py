"""Verified-state handoffs between short exploration conversations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .records import now, write_json


class SubtaskCheckpoints:
    """Persist a compact physical-state boundary without carrying chat history."""

    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / "subtask_checkpoints"
        self.pending_path = self.root / "pending_thread_rotation.json"

    def records(self):
        if not self.directory.is_dir():
            return []
        return [json.loads(path.read_text()) for path in sorted(self.directory.glob("[0-9]*.json"))]

    def pending(self):
        return json.loads(self.pending_path.read_text()) if self.pending_path.is_file() else None

    @staticmethod
    def _text(value, name, limit):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} must be nonempty and at most {limit} characters")
        return value.strip()

    @classmethod
    def _text_list(cls, value, name, maximum):
        if not isinstance(value, list) or not 1 <= len(value) <= maximum:
            raise ValueError(f"{name} must contain one to {maximum} entries")
        return [cls._text(item, name, 500) for item in value]

    def checkpoint(self, arguments, *, notes, episode_key, revision, held_objects):
        required = {"subtask_id", "summary", "postconditions", "evidence_refs",
                    "next_subtask", "expected_notes_version", "revision"}
        if set(arguments) != required:
            raise ValueError("Supply the exact subtask checkpoint fields")
        if self.pending_path.exists():
            raise ValueError("A conversation rotation is already pending")
        subtask_id = arguments["subtask_id"]
        if not isinstance(subtask_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", subtask_id):
            raise ValueError("subtask_id must be a lowercase stable identifier")
        if any(record["subtask_id"] == subtask_id for record in self.records()):
            raise ValueError("This subtask checkpoint already exists")
        if type(arguments["revision"]) is not int or arguments["revision"] != revision:
            raise ValueError("Checkpoint the current observation revision")
        expected = arguments["expected_notes_version"]
        if type(expected) is not int or expected != notes.get("version"):
            raise ValueError("Read and update take_notes before checkpointing")
        if notes.get("observation_revision") != revision or notes.get("episode_key") != episode_key:
            raise ValueError("Notes must describe this episode at the current observation revision")
        agent = json.loads((self.root / "agent.json").read_text())
        if agent.get("status") != "running" or not agent.get("thread_id"):
            raise ValueError("Checkpoint requires the active exploration conversation")
        summary = self._text(arguments["summary"], "summary", 1200)
        next_subtask = self._text(arguments["next_subtask"], "next_subtask", 600)
        postconditions = self._text_list(arguments["postconditions"], "postconditions", 12)
        evidence = self._text_list(arguments["evidence_refs"], "evidence_refs", 20)
        sequence = len(self.records()) + 1
        canonical = json.dumps({"episode_key": episode_key, "subtask_id": subtask_id,
            "revision": revision, "notes_version": expected, "postconditions": postconditions,
            "evidence_refs": evidence}, sort_keys=True, separators=(",", ":"))
        checkpoint_id = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        record = {"sequence": sequence, "checkpoint_id": checkpoint_id,
            "subtask_id": subtask_id, "at": now(), "episode_key": episode_key,
            "revision": revision, "notes_version": expected, "summary": summary,
            "postconditions": postconditions, "evidence_refs": evidence,
            "held_objects": deepcopy(held_objects), "next_subtask": next_subtask,
            "source": "model_authored_postconditions_not_evaluator_truth",
            "previous_thread_id": agent["thread_id"], "provider": agent.get("provider"),
            "model": agent.get("model"), "reasoning_effort": agent.get("reasoning_effort")}
        self.directory.mkdir(exist_ok=True)
        write_json(self.directory / f"{sequence:06d}_{subtask_id}.json", record)
        pending = {"checkpoint_id": checkpoint_id, "subtask_id": subtask_id,
            "previous_thread_id": agent["thread_id"], "provider": agent.get("provider"),
            "requested_at": record["at"], "next_subtask": next_subtask}
        write_json(self.pending_path, pending)
        return {"ok": True, "checkpoint_id": checkpoint_id, "sequence": sequence,
            "conversation_rotation_required": True,
            "instruction": "Return your final response now. Do not call another tool; the host will start a fresh conversation."}

    def validate_fresh_start(self, saved_agent, provider):
        pending = self.pending()
        if pending is None:
            raise ValueError("No subtask checkpoint is awaiting a fresh conversation")
        if saved_agent.get("status") != "paused":
            raise ValueError("The previous conversation is not paused")
        clean_checkpoint = saved_agent.get("exit_code") == 0
        operator_migration = (pending.get("source") == "operator_authorized_context_migration"
            and saved_agent.get("exit_code") != 0
            and pending.get("previous_error_type") == "provider_429"
            and type(pending.get("last_observation_revision")) is int
            and isinstance(pending.get("held_objects"), dict)
            and type(pending.get("notes_version")) is int)
        if not (clean_checkpoint or operator_migration):
            raise ValueError("The checkpointing conversation did not exit cleanly")
        if pending["previous_thread_id"] != saved_agent.get("thread_id"):
            raise ValueError("Pending checkpoint belongs to another conversation")
        if pending.get("provider") != provider:
            raise ValueError("Fresh conversation keeps the recorded provider")
        return deepcopy(pending)

    def complete_rotation(self, pending, new_thread_id):
        current = self.pending()
        if current != pending:
            raise ValueError("Pending thread rotation changed")
        if not isinstance(new_thread_id, str) or not new_thread_id:
            raise ValueError("Fresh conversation needs a thread id")
        record = {**pending, "new_thread_id": new_thread_id, "started_at": now(),
            "old_chat_loaded": False}
        directory = self.root / "thread_rotations"
        directory.mkdir(exist_ok=True)
        write_json(directory / f"{pending['checkpoint_id']}.json", record)
        self.pending_path.unlink()
        return record
