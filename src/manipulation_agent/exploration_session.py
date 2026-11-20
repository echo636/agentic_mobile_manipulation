"""Session-level Explore -> Compile -> Rehearse -> Submit -> Formal lifecycle.

Factories own startup/resource leases; this CPU-only coordinator never imports OG.
A session is operated by ONE exploration agent, and formal code has no live agent.
"""
from __future__ import annotations
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import threading
import uuid
from .exploration_workspace import create_workspace, freeze_project, runner_fingerprint
from .exploration_runner import run_controller
from .oracle_presentation import OraclePresentation
from .records import write_json, now
from .exploration_notes import ExplorationNotes
from .exploration_budget import context_policy, development_reply
from .exploration_subtasks import SubtaskCheckpoints
from .manip_runtime import ManipRecipes, RecipeContext, checked_invocation, TOOLS as MANIP_TOOLS
from .exploration_qualification import assess, qualification, policy as qualification_policy

LIFECYCLE = {"read_workspace_file", "write_workspace_file", "run_development", "status", "start_episode", "finish", "register", "rehearse", "submit",
             "set_oracle_focus", "get_oracle_geometry", "take_notes", "get_cached_rgb", "patch_workspace_file", "checkpoint_subtask"}
LIFECYCLE |= MANIP_TOOLS
PROGRAM_QUERIES = {"set_oracle_focus", "get_oracle_geometry"}
def prepare_session(root, config, tools):
    qualification_policy(config)
    root = Path(root).resolve()
    exploration, formal = config["exploration"], config["formal"]
    if not exploration or not formal:
        raise ValueError("Provide exploration and held-out formal schedules")
    keys = [x["key"] for x in exploration + formal]
    if len(set(keys)) != len(keys):
        raise ValueError("Episode identities must be unique; formal layouts must be disjoint")
    layout_sets = []
    for schedule in (exploration, formal):
        layouts = set()
        for entry in schedule:
            identity = entry.get("identity")
            if not isinstance(identity, dict) or set(identity) != {"task", "instance", "seed"}:
                raise ValueError("Each scheduled episode needs exact task/instance/seed identity")
            layouts.add((identity["task"], identity["instance"]))
        layout_sets.append(layouts)
    if layout_sets[0] & layout_sets[1]:
        raise ValueError("Formal task instances must not have been used in exploration")
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "config.json", config)
    write_json(root / "state.json", {"phase": "exploring", "exploration_used": 0,
               "formal_used": 0, "active": None, "bundles": {}, "rehearsals": [],
               "formal_results": [], "submitted": None,
               "config_sha256": hashlib.sha256((root/"config.json").read_bytes()).hexdigest()})
    create_workspace(root / "workspace", config["task"], tools)

class ExplorationSession:
    def __init__(self, root, factory, *, runner=run_controller):
        self.root = Path(root).resolve()
        self.config = json.loads((self.root / "config.json").read_text())
        self.state = json.loads((self.root / "state.json").read_text())
        if self.state.get("config_sha256") != hashlib.sha256((self.root/"config.json").read_bytes()).hexdigest():
            raise ValueError("Immutable session configuration changed")
        self.factory, self.runner = factory, runner
        self.lock = threading.RLock()
        self.owner = (self.root / "owner.lock").open("a")
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.owner.close()
            raise
        self.episode = None
        self.notes = ExplorationNotes(self.root)
        self.subtasks = SubtaskCheckpoints(self.root)
        self.presentation = OraclePresentation()
        self.manip_recipes = ManipRecipes(self.root / 'workspace/code')
        if self.state["active"]:
            # No automatic reset after process/provider restart.
            try:
                self.episode = self.factory.attach(self.state["active"])
            except Exception as exc:
                self.state["active"].update(status="recovery_required", attach_error=type(exc).__name__)
            else:
                self.state["active"]["status"] = "running"
            self.save()

    def close(self):
        if self.owner.closed:
            return
        fcntl.flock(self.owner, fcntl.LOCK_UN)
        self.owner.close()

    def save(self):
        write_json(self.root / "state.json", self.state)

    def event(self, kind, **data):
        with (self.root / "session_events.jsonl").open("a") as f:
            f.write(json.dumps({"at": now(), "kind": kind, **data}, allow_nan=False) + "\n")

    def status(self):
        checkpoints = self.subtasks.records()
        return {"ok": True, "phase": self.state["phase"],
                "episode_live": self.episode is not None,
                "active": deepcopy(self.state["active"]),
                "recovery_required": self.state["active"] is not None and self.episode is None,
                "exploration_episodes_left": len(self.config["exploration"]) - self.state["exploration_used"],
                "bundles": [{**entry, 'qualification': qualification(self.config, ident, self.state['rehearsals']),
                             "rehearsal_qualified": qualification(self.config, ident, self.state['rehearsals'])['qualified']}
                    for ident, entry in self.state["bundles"].items()],
                "submitted": self.state["submitted"],
                "subtask_checkpoints": [{k: entry[k] for k in
                    ("sequence", "checkpoint_id", "subtask_id", "summary", "revision", "next_subtask")}
                    for entry in checkpoints],
                "conversation_rotation_pending": self.subtasks.pending() is not None,
                "scope": "Oracle 3D research; independent official score; no RAX benchmark equivalence",
                "next_step": "explore or develop controller" if self.state["phase"] == "exploring"
                             else "exploration closed; host runs frozen evaluation"}

    def start(self, phase):
        if self.episode is not None or self.state["active"] is not None:
            raise ValueError("Finish or reconcile the recorded episode before starting another")
        counter = phase + "_used"
        index = self.state[counter]
        schedule = self.config[phase]
        if index >= len(schedule):
            raise ValueError("No remaining scheduled episodes")
        self.state[counter] += 1
        spec = deepcopy(schedule[index])
        # Charge before launch: failed startup remains an attempted episode.
        self.state["active"] = {"phase": phase, "spec": spec, "status": "starting"}
        self.save()
        self.event("episode_launch", phase=phase, schedule_index=index)
        def record_launch(identity):
            self.state["active"]["launcher"] = identity
            self.save()
        try:
            self.episode = self.factory.start(spec, on_launch=record_launch)
        except BaseException as exc:
            self.event("episode_launch_failed", exception=type(exc).__name__, phase=phase)
            # A timeout/launcher exit does not establish that its systemd unit
            # exited. Preserve even the pre-Popen window for operator recovery.
            self.state["active"].update(status="recovery_required", launch_error=type(exc).__name__)
            self.save()
            raise
        self.state["active"]["status"] = "running"
        self.save()
        self.presentation = OraclePresentation()
        reply = self.episode.call("initialize", {}, str(uuid.uuid4()))
        self.presentation.remember(reply)
        return reply

    def finish(self, arguments=None):
        if self.episode is None:
            raise ValueError("No active episode")
        reply = self.episode.call("finish", arguments or {
            "outcome": "aborted", "reason": "Exploration controller returned; evaluate independently"},
            str(uuid.uuid4()))
        if not reply.get("closed"):
            raise RuntimeError("Finish did not acknowledge closure")
        # Wait for the factory-owned simulator's final evaluator/recording cleanup.
        result = self.episode.collect()
        self.event("episode_closed", result=result)
        self.episode = None
        self.state["active"] = None
        self.save()
        return result

    def call_robot(self, name, arguments, request_id):
        if self.episode is None:
            raise ValueError("Start an exploration episode first")
        if name in PROGRAM_QUERIES:
            arguments = deepcopy(arguments)
            if "revision" not in arguments and self.presentation.snapshot is not None:
                arguments["revision"] = self.presentation.snapshot["revision"]
            return self.presentation.query(name, arguments)
        specs = {x["name"]: x for x in self.episode.tools}
        if name not in specs or name in {"finish", "initialize"}:
            if name != "initialize":
                raise ValueError("Tool is not a robot operation")
        arguments = deepcopy(arguments)
        schema = specs[name]["inputSchema"] if name in specs else {}
        if "revision" in schema.get("required", []) and "revision" not in arguments:
            if self.presentation.snapshot is None:
                raise ValueError("Initialize before acting")
            arguments["revision"] = self.presentation.snapshot["revision"]
        reply = self.episode.call(name, arguments, request_id)
        self.presentation.remember(reply)
        return reply

    def archive_procedures(self, output):
        """Keep broker evidence beside the episode without editing raw events."""
        trace = Path(output) / 'procedure_trace.jsonl'
        active = self.state.get('active')
        if trace.is_file() and active and active['spec'].get('run_dir'):
            target = Path(active['spec']['run_dir']) / 'env' / 'procedure_trace.jsonl'
            if target.parent.is_dir():
                with target.open('a') as stream:
                    stream.write(trace.read_text())

    def dispatch(self, name, arguments):
        with self.lock:
            if name == "status":
                return self.status()
            if name == 'take_notes':
                if self.state['phase'] != 'exploring' and arguments:
                    raise ValueError('Exploration is closed')
                active = self.state.get('active') or {}
                return self.notes.dispatch(arguments,
                    episode_key=active.get('spec', {}).get('key'),
                    observation_revision=(self.presentation.snapshot or {}).get('revision'))
            if name == 'checkpoint_subtask':
                if self.episode is None or self.state.get('active') is None:
                    raise ValueError('Checkpoint requires the live exploration episode')
                snapshot = self.presentation.snapshot
                if snapshot is None:
                    raise ValueError('Observe the current scene before checkpointing')
                robot = snapshot.get('oracle_3d', {}).get('robot', {})
                arms = robot.get('arms', {}) if isinstance(robot, dict) else {}
                held = {side: arm.get('held_object') for side, arm in arms.items()
                        if isinstance(arm, dict) and arm.get('held_object') is not None}
                active = self.state['active']
                return self.subtasks.checkpoint(arguments, notes=self.notes.read(),
                    episode_key=active['spec']['key'], revision=snapshot['revision'], held_objects=held)
            if self.state["phase"] != "exploring":
                raise ValueError("Exploration is closed")
            if name == 'list_manip_recipes':
                return {'ok': True, 'recipes': self.manip_recipes.catalog()}
            if name == 'read_manip_recipe':
                entry = self.manip_recipes.read(arguments['recipe'])
                entry['controller_path'] = str(Path('code') / entry['project'] / 'controller.py')
                return {'ok': True, **entry}
            if name == 'manip':
                if self.episode is not None and any(t['name'] == 'write_manip_program' for t in self.episode.tools):
                    # This mode submits pure geometry code to the owner harness,
                    # not an imperative RAX recipe with child robot RPCs.
                    return self.presentation.packet(self.call_robot(name, arguments, str(uuid.uuid4())))
                if self.episode is None or self.presentation.snapshot is None:
                    raise ValueError('Initialize the live episode before Manip')
                entry = self.manip_recipes.get(arguments['recipe'])
                invocation = checked_invocation(entry, arguments)
                if invocation['revision'] != self.presentation.snapshot['revision']:
                    raise ValueError('Stale Manip revision')
                frozen = freeze_project(self.root / 'workspace', entry['project'], self.root / 'debug_bundles')
                run_id = uuid.uuid4().hex
                path = self.root / 'development' / run_id
                allowed = ({t['name'] for t in self.episode.tools} - LIFECYCLE) | PROGRAM_QUERIES
                allowed.discard('initialize')
                ctx = RecipeContext(self.call_robot, lambda: self.presentation.snapshot, invocation['constraints'],
                                    notes_path=self.root / 'recipe_notes' / str(self.state['exploration_used']) / entry['recipe'])
                execution = self.runner(self.root / 'debug_bundles' / frozen['bundle'], frozen['code_sha256'],
                                        path, ctx.invoke, allowed, invocation=invocation)
                self.archive_procedures(path)
                execution.update(unattended=False, scope='recipe development; not independent qualification')
                result = execution.get('controller_result') or {}
                reply = {'ok': execution.get('clean') is True and result.get('ok') is True and not ctx.failed,
                         'recipe': entry['recipe'], 'project': entry['project'],
                         'source_sha256': entry['source_sha256'], 'bundle': frozen['bundle'],
                         'execution': execution, 'run_id': run_id,
                         'validation': 'development only; independent evaluator required',
                         'observation': self.presentation.project(self.presentation.snapshot)}
                self.event('manip_development', **{k: v for k, v in reply.items() if k != 'observation'})
                if context_policy(self.config)['compact_development_results']:
                    reply['execution'] = development_reply(execution, run_id)
                return reply
            if name == 'get_cached_rgb':
                snapshot = self.presentation.snapshot
                if snapshot is None:
                    raise ValueError('No cached observation; initialize or observe once first')
                if set(arguments) != {'revision', 'views'} or type(arguments['revision']) is not int:
                    raise ValueError('Supply current revision and views')
                if arguments['revision'] != snapshot['revision']:
                    raise ValueError('Stale RGB revision; use the latest tool response')
                views = arguments['views']
                frames = {frame['view']: frame for frame in snapshot.get('images', [])}
                if (not isinstance(views, list) or not 1 <= len(views) <= 6
                        or any(not isinstance(v, str) or v not in frames for v in views)
                        or len(set(views)) != len(views)):
                    raise ValueError('Select one to six unique views from current images metadata')
                return {'ok': True, 'source': 'cached_current_observation', 'no_simulator_step': True,
                        'observation': {'revision': snapshot['revision'],
                            'capture': deepcopy(snapshot.get('capture')),
                            'images': [deepcopy(frames[v]) for v in views]}}
            if name in {"read_workspace_file", "write_workspace_file", "patch_workspace_file"}:
                relative = Path(arguments["path"])
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise ValueError("Use a workspace-relative path")
                workspace = self.root / "workspace"
                path = workspace / relative
                if not path.resolve().is_relative_to(workspace):
                    raise ValueError("Path escapes workspace")
                if any(part.is_symlink() for part in [path, *path.parents] if part != workspace.parent):
                    raise ValueError("Symlinks are not workspace files")
                if name == "read_workspace_file":
                    if not path.is_file() or path.stat().st_size > 100_000:
                        raise ValueError("Missing/oversized text file")
                    text = path.read_text()
                    offset, limit = arguments.get('offset', 0), arguments.get('max_chars', 12000)
                    if type(offset) is not int or not 0 <= offset <= len(text):
                        raise ValueError('offset must address a character in this file, or its end')
                    if type(limit) is not int or not 1 <= limit <= 24000:
                        raise ValueError('max_chars must be between 1 and 24000')
                    end = min(len(text), offset + limit)
                    return {"ok": True, "path": relative.as_posix(), "text": text[offset:end],
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                            'offset': offset, 'next_offset': end if end < len(text) else None,
                            'total_chars': len(text), 'truncated': end < len(text)}
                if relative.parts[0] not in {"code", "memory", "experiments"}:
                    raise ValueError("Only code/, memory/, experiments/ are agent-writable")
                if name == 'patch_workspace_file':
                    if not path.is_file() or path.stat().st_size > 100_000:
                        raise ValueError('Missing/oversized text file')
                    raw = path.read_bytes()
                    if arguments['expected_sha256'] != hashlib.sha256(raw).hexdigest():
                        raise ValueError('File changed: read the current file before patching')
                    old, new = arguments['old_text'], arguments['new_text']
                    if (not isinstance(old, str) or not old or not isinstance(new, str)
                            or len(old) > 24000 or len(new) > 24000):
                        raise ValueError('Use nonempty old_text and bounded replacement text')
                    content = raw.decode()
                    if content.count(old) != 1:
                        raise ValueError('Patch must match exactly once')
                    content = content.replace(old, new, 1)
                else:
                    content = arguments["content"]
                if not isinstance(content, str) or len(content.encode()) > 100_000:
                    raise ValueError("Bounded UTF-8 text required")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                self.event("workspace_written", path=relative.as_posix())
                return {"ok": True, "path": relative.as_posix(),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            if name == "run_development":
                if self.episode is None:
                    raise ValueError("Start an exploration episode before development execution")
                entry = freeze_project(self.root / "workspace", arguments["project"], self.root / "debug_bundles")
                allowed = {x["name"] for x in self.episode.tools} - LIFECYCLE
                allowed.add("initialize")
                allowed.update(PROGRAM_QUERIES)
                run_id = uuid.uuid4().hex
                path = self.root / "development" / run_id
                execution = self.runner(self.root / "debug_bundles" / entry["bundle"], entry["code_sha256"],
                                        path, self.call_robot, allowed)
                self.archive_procedures(path)
                execution["unattended"] = False
                execution["scope"] = "development only; not rehearsal qualification"
                self.event("development_run", execution=execution)
                logs = {n: (path / n).read_text()[-4000:] if (path / n).exists() else ""
                        for n in ("stdout.log", "stderr.log")}
                projected = (development_reply(execution, run_id)
                             if context_policy(self.config)['compact_development_results'] else execution)
                reply = {"ok": True, "execution": projected, "logs": logs}
                if self.presentation.snapshot:
                    reply["observation"] = self.presentation.project(self.presentation.snapshot)
                return reply
            if name == "start_episode":
                return self.presentation.packet(self.start("exploration"))
            if name == "finish":
                return {"ok": True, "episode_closed": True, "evaluation": self.finish(arguments)}
            if name in {"get_oracle_geometry", "set_oracle_focus"}:
                return self.presentation.query(name, arguments)
            if name == "register":
                entry = freeze_project(self.root / "workspace", arguments["project"], self.root / "bundles")
                self.state["bundles"][entry["bundle"]] = entry
                self.save()
                self.event("registered", bundle=entry["bundle"], project=entry["project"])
                return {"ok": True, **entry}
            if name == "rehearse":
                if self.episode is not None or self.state["active"] is not None:
                    raise ValueError("Finish exploration motion before independent rehearsal")
                return self.run_bundle(arguments["bundle"], "exploration")
            if name == "submit":
                bundle = arguments["bundle"]
                if self.episode is not None or self.state["active"] is not None:
                    raise ValueError("Finish the episode before submission")
                gate = qualification(self.config, bundle, self.state['rehearsals'])
                if not gate['qualified']:
                    raise ValueError('Exact bundle needs independent rehearsal qualification: '+json.dumps(gate))
                self.state.update(phase="submitted", submitted=bundle)
                self.save()
                self.event("submitted", bundle=bundle)
                return {"ok": True, "submitted": bundle, "formal_scores_visible": False}
            return self.presentation.packet(self.call_robot(name, arguments, str(uuid.uuid4())))

    def run_bundle(self, bundle, phase):
        if self.state["active"] is not None:
            raise ValueError("Reconcile the recorded episode before running a bundle")
        if bundle not in self.state["bundles"]:
            raise ValueError("Unknown registered bundle")
        entry = self.state["bundles"][bundle]
        if entry["runner_sha256"] != runner_fingerprint():
            raise ValueError("Runner changed: register and rehearse a new bundle")
        try:
            self.start(phase)
        except Exception as exc:
            failed = {"bundle": bundle, "run_id": uuid.uuid4().hex, "phase": phase,
                      "execution": {"clean": False, "startup_error": type(exc).__name__},
                      "evaluation": {"official_task_success": None, "not_evaluated": True}}
            key = "rehearsals" if phase == "exploration" else "formal_results"
            self.state[key].append(failed)
            self.save()
            self.event("bundle_start_failed", **failed)
            return {"ok": False, "recovery_required": self.state["active"] is not None, **failed}
        run_id = uuid.uuid4().hex
        episode_spec = deepcopy(self.state['active']['spec'])
        path = self.root / "runs" / run_id
        allowed = {x["name"] for x in self.episode.tools} - LIFECYCLE
        # initialize is a read-only prepared snapshot; permitted in standalone code.
        allowed.add("initialize")
        allowed.update(PROGRAM_QUERIES)
        execution = evaluation = None
        try:
            execution = self.runner(self.root / "bundles" / bundle, entry["code_sha256"], path,
                                    self.call_robot, allowed)
        except Exception as exc:
            execution = {"clean": False, "exception": type(exc).__name__}
        finally:
            self.archive_procedures(path)
            evaluation = self.finish()
        record = {"bundle": bundle, "run_id": run_id, "execution": execution,
                  "evaluation": evaluation, "phase": phase,
                  'episode_key': episode_spec['key'], 'episode_identity': episode_spec['identity']}
        if execution is None:
            execution = record["execution"] = {"clean": False, "exception": True}
        if phase == 'exploration' and qualification_policy(self.config) is not None:
            record['qualification'] = assess(self.config, record, path)
        key = "rehearsals" if phase == "exploration" else "formal_results"
        self.state[key].append(record)
        self.save()
        self.event("bundle_run", **record)
        return {"ok": True, **record}

    def evaluate(self):
        with self.lock:
            if self.state["phase"] not in {"submitted", "formal"}:
                raise ValueError("Submit a rehearsed frozen bundle first")
            if self.state["active"] is not None:
                raise ValueError("Reconcile the recorded episode before continuing formal evaluation")
            self.state["phase"] = "formal"
            self.save()
            while self.state["formal_used"] < len(self.config["formal"]):
                result = self.run_bundle(self.state["submitted"], "formal")
                if result.get("recovery_required"):
                    raise RuntimeError("Formal startup uncertain; reconcile the recorded episode before continuing")
            self.state["phase"] = "complete"
            self.save()
            return deepcopy(self.state["formal_results"])
