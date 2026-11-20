"""Continuation memory and context reduction preserve simulator/evidence state."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from manipulation_agent.clients import ClientConfig, get_adapter
from manipulation_agent.exploration_budget import (
    DEFAULT_CONTEXT_POLICY, deliver_rgb, development_reply, tune_prepared_project,
)
from manipulation_agent.exploration_frontend import tool_specs
from manipulation_agent.exploration_notes import ExplorationNotes
from manipulation_agent.exploration_session import ExplorationSession, prepare_session


class NotesTests(unittest.TestCase):
    def test_notes_survive_restart_and_preserve_old_versions_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            notes = ExplorationNotes(directory)
            self.assertEqual(notes.read()['version'], 0)
            reply = notes.dispatch({'content': 'Held can; failed lift. Do not release.', 'expected_version': 0},
                                   episode_key='same-scene', observation_revision=7)
            self.assertNotIn('content', reply)
            self.assertFalse(reply['context_compacted'])
            restored = ExplorationNotes(directory)
            self.assertEqual(restored.read()['observation_revision'], 7)
            restored.dispatch({'content': 'Lift recovered; placement untested.', 'expected_version': 1})
            old = json.loads((Path(directory) / 'context_note_history/000001.json').read_text())
            self.assertIn('Do not release', old['content'])
            self.assertEqual(restored.read()['version'], 2)
            with self.assertRaises(ValueError):
                notes.dispatch({'content': 'stale writer', 'expected_version': 0})
            self.assertEqual(restored.read()['version'], 2)

    def test_invalid_notes_cannot_erase_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            notes = ExplorationNotes(directory)
            for arguments in ({'content': '', 'expected_version': 0},
                              {'content': 'x' * 6001, 'expected_version': 0},
                              {'content': 'valid', 'expected_version': True},
                              {'content': 'valid'}, {'expected_version': 0}):
                with self.assertRaises(ValueError):
                    notes.dispatch(arguments)
            self.assertEqual(notes.read()['version'], 0)


class BudgetTests(unittest.TestCase):
    def test_blocking_calls_and_lower_compaction_limit_survive_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ClientConfig('codex', 'gpt-6-astra', 'task', '/usr/bin/python', [],
                                  Path(directory), blocking_tool_yield_ms=2147483647, auto_compact_token_limit=48000)
            adapter = get_adapter('codex')
            project = adapter.prepare_project(config, 'notes workflow', ['take_notes'])
            project = tune_prepared_project(project, {'context_policy': DEFAULT_CONTEXT_POLICY},
                                             'notes workflow', ['take_notes', 'run_development'])
            for _ in range(2):
                project = tune_prepared_project(project, {'context_policy': DEFAULT_CONTEXT_POLICY},
                                                 'notes workflow', ['take_notes', 'run_development'])
            resumed = adapter.prepare_resume(project, '00000000-0000-0000-0000-000000000001', 'continue')
            configs = [resumed.argv[i + 1] for i, arg in enumerate(resumed.argv[:-1]) if arg == '-c']
            self.assertEqual(configs.count('model_auto_compact_token_limit=48000'), 1)
            self.assertIn('features.code_mode_host=true', configs)
            self.assertIn('features.code_mode.default_exec_yield_time_ms=2147483647', configs)
            self.assertIn('mcp_servers.manipulation.enabled_tools=["take_notes", "run_development"]', configs)
            self.assertEqual(resumed.argv[:3], ['codex', 'exec', 'resume'])

    def test_compact_execution_keeps_failure_and_full_record_unchanged(self):
        execution = {'clean': True, 'controller_result': {'ok': False, 'error': 'still holding'},
                     'calls': [{'ok': True, 'request_id': str(i)} for i in range(1000)] +
                              [{'ok': False, 'error': {'message': 'failed lift'}, 'request_id': 'failed'}]}
        before = copy.deepcopy(execution)
        summary = development_reply(execution, 'debug-id')
        self.assertEqual(execution, before)
        self.assertNotIn('calls', summary)
        self.assertFalse(summary['controller_result']['ok'])
        self.assertEqual(summary['call_summary']['first_failed']['request_id'], 'failed')
        self.assertEqual(summary['call_summary']['count'], 1001)
        self.assertLess(len(json.dumps(summary)), len(json.dumps(execution)) / 10)

    def test_on_demand_images_do_not_disable_available_cameras(self):
        config = {'context_policy': DEFAULT_CONTEXT_POLICY}
        self.assertFalse(deliver_rgb('run_development', config))
        self.assertFalse(deliver_rgb('observe_scene', config))
        self.assertTrue(deliver_rgb('get_cached_rgb', config))
        self.assertTrue(deliver_rgb('run_development', {}))  # historical protocol
        names = {tool['name'] for tool in tool_specs([])}
        self.assertTrue({'take_notes', 'get_cached_rgb', 'patch_workspace_file', 'checkpoint_subtask'} <= names)


class SessionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'session'
        cfg = {'task': 'fixture', 'context_policy': DEFAULT_CONTEXT_POLICY,
               'exploration': [{'key': 'dev', 'identity': {'task': 'x', 'instance': 1, 'seed': 0}}],
               'formal': [{'key': 'eval', 'identity': {'task': 'x', 'instance': 2, 'seed': 0}}]}
        prepare_session(self.root, cfg, [])
        self.session = ExplorationSession(self.root, None)
        self.addCleanup(self.session.close)

    def test_cached_selected_rgb_never_calls_simulator_and_rejects_stale_revision(self):
        class ForbiddenRobot:
            def call(self, *args):
                raise AssertionError('cached RGB must not call the bridge')
        self.session.episode = ForbiddenRobot()
        images = [{'view': view, 'image_ref': view, 'sha256': 'hash'} for view in
                  ['front', 'back', 'left', 'right', 'left_wrist', 'right_wrist']]
        self.session.presentation.snapshot = {'revision': 9, 'images': images, 'capture': {'sim_step': 100}}
        before = copy.deepcopy(self.session.presentation.snapshot)
        result = self.session.dispatch('get_cached_rgb', {'views': ['left_wrist'], 'revision': 9})
        self.assertEqual([x['view'] for x in result['observation']['images']], ['left_wrist'])
        self.assertEqual(self.session.presentation.snapshot, before)
        for arguments in ({'views': ['left_wrist'], 'revision': 8},
                          {'views': ['left_wrist', 'left_wrist'], 'revision': 9},
                          {'views': ['missing'], 'revision': 9}):
            with self.assertRaises(ValueError):
                self.session.dispatch('get_cached_rgb', arguments)

    def test_file_pagination_and_hash_checked_patch_avoid_full_code_replay(self):
        path = self.root / 'workspace/code/large.py'
        text = 'prefix\n' + 'a' * 15000 + '\nsuffix\n'
        path.write_text(text)
        result = self.session.dispatch('read_workspace_file', {'path': 'code/large.py'})
        self.assertEqual(len(result['text']), 12000)
        tail = self.session.dispatch('read_workspace_file', {'path': 'code/large.py', 'offset': result['next_offset']})
        self.assertEqual(result['text'] + tail['text'], text)
        arguments = {'path': 'code/large.py', 'expected_sha256': result['sha256'],
                     'old_text': 'suffix', 'new_text': 'changed'}
        reply = self.session.dispatch('patch_workspace_file', arguments)
        self.assertNotEqual(reply['sha256'], result['sha256'])
        with self.assertRaises(ValueError):
            self.session.dispatch('patch_workspace_file', arguments)
        for path in ['../context_notes.json', '/etc/passwd', 'memory/../../config.json']:
            with self.assertRaises(ValueError):
                self.session.dispatch('read_workspace_file', {'path': path})

    def test_subtask_checkpoint_requires_current_notes_and_requests_fresh_thread(self):
        class LiveEpisode: pass
        self.session.episode = LiveEpisode()
        self.session.state['active'] = {'spec': {'key': 'dev'}, 'status': 'running'}
        self.session.presentation.snapshot = {'revision': 9, 'images': [],
            'oracle_3d': {'robot': {'arms': {'left': {'held_object': None},
                                             'right': {'held_object': 'bottle'}}}}}
        (self.root / 'agent.json').write_text(json.dumps({'status': 'running',
            'thread_id': 'old-thread', 'provider': 'fixture', 'model': 'model',
            'reasoning_effort': 'high'}))
        note = self.session.dispatch('take_notes', {'content': 'Bottle retained; next place it.',
            'expected_version': 0})
        arguments = {'subtask_id': 'pickup_floor_bottle', 'summary': 'Floor bottle retained.',
            'postconditions': ['right hand retains intended bottle'],
            'evidence_refs': ['development/run/execution.json#request-1'],
            'next_subtask': 'Place retained bottle into the assigned bottle bucket.',
            'expected_notes_version': note['version'], 'revision': 9}
        reply = self.session.dispatch('checkpoint_subtask', arguments)
        self.assertTrue(reply['conversation_rotation_required'])
        record = self.session.subtasks.records()[0]
        self.assertEqual(record['held_objects'], {'right': 'bottle'})
        self.assertTrue(self.session.status()['conversation_rotation_pending'])
        with self.assertRaises(ValueError):
            self.session.dispatch('checkpoint_subtask', arguments)
        agent = json.loads((self.root / 'agent.json').read_text())
        agent.update(status='paused', exit_code=0)
        (self.root / 'agent.json').write_text(json.dumps(agent))
        pending = self.session.subtasks.validate_fresh_start(agent, 'fixture')
        rotation = self.session.subtasks.complete_rotation(pending, 'new-thread')
        self.assertFalse(rotation['old_chat_loaded'])
        self.assertFalse(self.session.status()['conversation_rotation_pending'])
        self.assertTrue((self.root / 'thread_rotations' /
                         (reply['checkpoint_id'] + '.json')).is_file())

    def test_subtask_checkpoint_rejects_stale_revision_and_notes(self):
        class LiveEpisode: pass
        self.session.episode = LiveEpisode()
        self.session.state['active'] = {'spec': {'key': 'dev'}, 'status': 'running'}
        self.session.presentation.snapshot = {'revision': 4, 'oracle_3d': {'robot': {'arms': {}}}}
        (self.root / 'agent.json').write_text(json.dumps({'status': 'running',
            'thread_id': 'old-thread', 'provider': 'fixture'}))
        self.session.dispatch('take_notes', {'content': 'State at revision four.', 'expected_version': 0})
        base = {'subtask_id': 'placed_can', 'summary': 'Can placed.',
            'postconditions': ['can is inside bucket'], 'evidence_refs': ['event-1'],
            'next_subtask': 'Pick the next object.', 'expected_notes_version': 1, 'revision': 3}
        with self.assertRaisesRegex(ValueError, 'current observation'):
            self.session.dispatch('checkpoint_subtask', base)
        self.session.presentation.snapshot['revision'] = 5
        base['revision'] = 5
        with self.assertRaisesRegex(ValueError, 'Notes must describe'):
            self.session.dispatch('checkpoint_subtask', base)

    def test_operator_context_migration_can_replace_a_429_thread_but_not_other_failures(self):
        store = self.session.subtasks
        pending = {'checkpoint_id': 'operator-migration', 'subtask_id': 'context_migration',
            'previous_thread_id': 'old-thread', 'provider': 'fixture',
            'requested_at': 'now', 'next_subtask': 'continue current task',
            'source': 'operator_authorized_context_migration',
            'previous_error_type': 'provider_429', 'last_observation_revision': 191,
            'held_objects': {}, 'notes_version': 3}
        (self.root / 'pending_thread_rotation.json').write_text(json.dumps(pending))
        agent = {'status': 'paused', 'exit_code': 1, 'thread_id': 'old-thread',
                 'provider': 'fixture'}
        self.assertEqual(store.validate_fresh_start(agent, 'fixture'), pending)
        pending.pop('last_observation_revision')
        (self.root / 'pending_thread_rotation.json').write_text(json.dumps(pending))
        with self.assertRaisesRegex(ValueError, 'did not exit cleanly'):
            store.validate_fresh_start(agent, 'fixture')


if __name__ == '__main__':
    unittest.main()
