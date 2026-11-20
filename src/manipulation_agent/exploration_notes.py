"""Bounded session memory, independent of simulator state and model history."""
from copy import deepcopy
import json
from pathlib import Path

from .records import now, write_json

MAX_NOTE_CHARS = 6000


class ExplorationNotes:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'context_notes.json'

    def read(self):
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {'version': 0, 'content': '', 'source': 'model_authored_not_evaluator_truth'}

    def dispatch(self, arguments, *, episode_key=None, observation_revision=None):
        if set(arguments) - {'content', 'expected_version'}:
            raise ValueError('Use content and expected_version, or empty arguments to read notes')
        state = self.read()
        if 'content' not in arguments:
            if arguments:
                raise ValueError('expected_version requires content')
            return {'ok': True, **deepcopy(state)}
        content = arguments['content']
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_NOTE_CHARS:
            raise ValueError(f'Write nonempty notes of at most {MAX_NOTE_CHARS} characters')
        version = arguments.get('expected_version')
        if type(version) is not int or version != state['version']:
            raise ValueError('Notes version conflict: read take_notes before replacing the summary')
        previous = state
        state = {'version': version + 1, 'content': content, 'at': now(),
                 'episode_key': episode_key, 'observation_revision': observation_revision,
                 'source': 'model_authored_not_evaluator_truth'}
        # The current record is atomic; archived versions preserve failures and
        # earlier hypotheses without replaying them into every model request.
        history = self.root / 'context_note_history'
        history.mkdir(exist_ok=True)
        archive = history / f'{previous["version"]:06d}.json'
        if archive.exists():
            if json.loads(archive.read_text()) != previous:
                raise ValueError('Archived notes conflict; preserve records for reconciliation')
        else:
            write_json(archive, previous)
        write_json(self.path, state)
        return {'ok': True, 'version': state['version'], 'characters': len(content),
                'persisted': True, 'context_compacted': False,
                'source': state['source']}
