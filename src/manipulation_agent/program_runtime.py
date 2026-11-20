"""Run frozen trusted skill code on the simulator owner thread, through its tools."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback
import types

from .contracts import SkillError, validate
from .oracle_contracts import ACTION_TOOLS
from .program_library import source_files, digest, runtime_identity
from .records import now, write_json


class ProgramFailure(RuntimeError):
    pass


class SkillContext:
    """No simulator objects: only serialized tools, current observations and records.

    This API is a control-flow contract for reviewed local Python, not a sandbox.
    Failures keep the current joint/gripper commands; never drop an object to recover.
    """
    def __init__(self, harness, entries, parent_request_id, *, allow_draft=False):
        self.harness, self.entries = harness, entries
        self.parent_request_id, self.allow_draft = parent_request_id, allow_draft
        self.stack, self.calls = [], []

    @property
    def observation(self):
        return copy.deepcopy(self.harness.snapshot)

    @property
    def frames(self):
        return copy.deepcopy(self.harness.snapshot.get('images', []))

    def require(self, condition, why):
        if not condition:
            raise ProgramFailure(why)

    def call(self, tool, **arguments):
        self.require(tool in (set(ACTION_TOOLS) - {'navigate_to'}) | {'observe_scene'},
                     'Program context permits native manipulation and observation only')
        self.harness.deadline.check()
        if tool in ACTION_TOOLS:
            arguments['revision'] = self.harness.revision
        ident = 'program-' + hashlib.sha256(self.parent_request_id.encode()).hexdigest()[:24] + '-' + str(len(self.calls))
        old_origin = getattr(self.harness, '_call_origin', None)
        try:
            reply = self.harness.call(tool, arguments, ident, origin='compiled_skill')
        finally:
            self.harness._call_origin = old_origin
        self.calls.append({'tool': tool, 'arguments': arguments, 'request_id': ident,
                           'evidence_id': reply.get('evidence_id'), 'ok': reply['ok'],
                           'revision': self.harness.revision})
        self.require(reply['ok'], f"{tool}: {(reply.get('error') or {}).get('message', 'physical verification failed')}")
        return reply

    def observe(self):
        self.call('observe_scene')
        return self.observation

    def run(self, name, **parameters):
        entry = self.entries.get(name)
        self.require(entry is not None, 'Unknown frozen executable skill')
        self.require(entry['status'] == 'verified' or (entry['status'] == 'draft' and self.allow_draft),
                     'Draft execution requires explicit allow_draft=true; deprecated skills cannot run')
        self.require(name not in self.stack and len(self.stack) < 16, 'Cyclic/excessive skill composition')
        if self.stack:
            self.require(name in self.entries[self.stack[-1]]['uses'], 'Dependency absent from uses')
        validate(parameters, entry['parameters'])
        self.require(runtime_identity()['runtime_sha256'] == entry['runtime_sha256'],
                     'Harness runtime changed after skill publication/freeze; republish and revalidate')
        files = source_files(entry['directory'])
        self.require(digest(files, code_only=True) == entry['code_sha256'], 'Frozen program code changed')
        self.require(digest(files) == entry['snapshot_sha256'], 'Frozen program snapshot changed')
        # A distinct namespace per frozen content avoids cross-version import caches.
        namespace = '_mas_program_' + entry['snapshot_sha256']
        module_name = namespace + '.entry'
        if module_name not in sys.modules:
            package = types.ModuleType(namespace)
            package.__path__ = [str(entry['directory'] / 'code')]
            sys.modules[namespace] = package
            spec = importlib.util.spec_from_file_location(module_name, entry['directory'] / 'code/run.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(module_name, None)
                raise
        self.stack.append(name)
        try:
            result = sys.modules[module_name].main(self, **parameters)
            self.require(isinstance(result, dict) and type(result.get('ok')) is bool and
                         isinstance(result.get('measured'), dict) and isinstance(result.get('why'), str),
                         'Skill must return {ok: bool, measured: dict, why: str}')
            json.dumps(result, allow_nan=False)
            self.require(result['ok'], result['why'])
            return result
        finally:
            self.stack.pop()


def run_program(harness, entries, name, parameters, parent_request_id, allow_draft):
    context = SkillContext(harness, entries, parent_request_id, allow_draft=allow_draft)
    entry = entries.get(name)
    if entry is None:
        raise SkillError('unknown_program', 'Choose a frozen program from list_programs')
    ident = 'run-' + hashlib.sha256(parent_request_id.encode()).hexdigest()[:24]
    started = time.monotonic()
    record = {'invocation_id': ident, 'name': name, 'version': entry['version'],
              'code_sha256': entry['code_sha256'], 'snapshot_sha256': entry['snapshot_sha256'],
              'runtime_sha256': entry['runtime_sha256'],
              'library_commit': entry.get('library_commit'),
              'dependency_code_sha256': entry['dependency_code_sha256'],
              'status_at_start': entry['status'], 'parameters': parameters,
              'parent_request_id': parent_request_id, 'started_at': now(),
              'initial_revision': harness.revision, 'start_frames': context.frames,
              'unattended': True, 'motion_source': 'frozen_executable_experience',
              'scope': 'code subgoal; whole-task success and independent program review remain separate'}
    (harness.recorder.output / 'program_runs').mkdir(exist_ok=True)
    harness.recorder.event('program_started', record)
    try:
        result = context.run(name, **parameters)
    except (ProgramFailure, SkillError) as exc:
        result = {'ok': False, 'measured': {}, 'why': str(exc)}
    except Exception as exc:
        folder = harness.recorder.output / 'program_runs'
        folder.mkdir(exist_ok=True)
        (folder / (ident + '.traceback.txt')).write_text(traceback.format_exc())
        result = {'ok': False, 'measured': {}, 'why': 'program_exception:' + type(exc).__name__}
    result = {**result, 'frames': context.frames, 'calls': context.calls,
              'latest_revision': harness.revision, 'stopped_at_first_failure': not result['ok']}
    record.update(result=result, finished_at=now(), duration_seconds=time.monotonic() - started)
    write_json(harness.recorder.output / 'program_runs' / (ident + '.json'), record)
    harness.recorder.event('program_finished', record)
    return {'ok': result['ok'], 'execution': {'program': name, 'version': entry['version'],
            'invocation_id': ident, 'code_sha256': entry['code_sha256'], **result},
            **({'error': {'code': 'compiled_skill_failed', 'message': result['why'],
                         'world_may_have_changed': bool(context.calls)}} if not result['ok'] else {}),
            'observation': copy.deepcopy(harness.snapshot), 'actions_used': harness.actions}
