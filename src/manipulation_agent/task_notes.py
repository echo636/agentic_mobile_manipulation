"""Versioned model-authored text; the same storage contract in both experiment arms."""
import copy
from .contracts import SkillError
from .records import now, write_json


class TaskNotes:
    def __init__(self, output):
        self.output = output
        self.state = {'version': 0, 'content': ''}

    def read(self):
        return copy.deepcopy(self.state)

    def write(self, content, expected_version, *, request_id, revision, agent_step):
        if not isinstance(content, str) or not content.strip() or len(content) > 12000:
            raise SkillError('invalid_notes', 'Write nonempty text of at most 12000 characters.')
        if type(expected_version) is not int or expected_version != self.state['version']:
            raise SkillError('notes_version_conflict', 'Read task_notes and edit the current version before writing.')
        state = {'version': self.state['version'] + 1, 'content': content, 'at': now(),
                 'request_id': request_id, 'observation_revision': revision, 'agent_step': agent_step,
                 'source': 'model_authored_notes_not_evaluator_truth'}
        write_json(self.output / 'task_notes.json', state)
        self.state = state
        return self.read()
