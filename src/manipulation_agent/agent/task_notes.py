"""Versioned model-authored text; the same storage contract in both experiment arms."""
import copy
import json
from collections import deque
from ..contracts import SkillError
from ..records import now, write_json
from .task_planning import task_advice


class TaskNotes:
    def __init__(self, output):
        self.output = output
        self.state = {'version': 0, 'content': ''}
        self.feedback = deque(maxlen=64)
        self.feedback_count = 0
        self.feedback_seconds = 0.
        self._planning_version = None
        self._planning_advice = None

    def read(self):
        return copy.deepcopy({**self.state, 'action_feedback_since_write': {
            'entries': list(self.feedback), 'omitted': self.feedback_count-len(self.feedback)}})

    def record_action(self, name, arguments, result, agent_step):
        """Retain only the caller's action and its already-public receipt."""
        if not isinstance(arguments, dict):
            arguments = {}
        self.feedback.append(copy.deepcopy({
            'tool': name, 'primitive': arguments.get('primitive', name),
            'target': arguments.get('target'), 'agent_step': agent_step,
            **{key: result[key] for key in ('ok', 'evidence_id', 'effect', 'error', 'runtime') if key in result}}))
        self.feedback_count += 1
        self.feedback_seconds += result.get('runtime', {}).get('tool_seconds', 0.)

    def planning(self, pending=None):
        """Expose advice in the normal loop without reissuing a stale next step.

        Only the model can reconcile RGB/receipts with its semantic goal state.
        A receipt invalidates the checkpoint's next recommendation, not the
        actual action or goal. Multi-action local methods can continue in RGB.
        """
        if self._planning_version != self.state['version']:
            self._planning_advice = task_advice(self.state['content'])
            self._planning_version = self.state['version']
        advice = copy.deepcopy(self._planning_advice)
        advice['notes_version'] = self.state['version']
        count = self.feedback_count + (pending is not None)
        if not count:
            return advice
        latest = pending if pending is not None else self.feedback[-1]
        failed = latest.get('ok') is False or any(entry.get('ok') is False for entry in self.feedback)
        return dict(status='reconcile', notes_version=self.state['version'],
            checkpoint_status=advice['status'], active=advice.get('active'),
            selection=advice.get('selection'),
            next=None, actions_since_checkpoint=count,
            execution=dict(tool_seconds=round(self.feedback_seconds +
                (pending or {}).get('tool_seconds', 0.), 3),
                scope='Server action and observation time; excludes model requests and notes'),
            latest_receipt={k: latest[k] for k in ('primitive', 'ok', 'effect', 'error') if k in latest},
            failed_since_checkpoint=failed,
            next_decision=('Reconcile the failure and affected goals/resources from RGB and receipts; '
                           'update notes to compare revised methods.' if failed else
                           'Check the returned RGB/receipt against the current method. Continue its local steps '
                           'if supported; on completion, new information or a branch change, update notes '
                           'to recompute the remaining DAG. Tool success alone does not complete a goal.'))

    def select(self, content, expected_version, **metadata):
        """Record the choice after seeing a comparison, without rewriting the graph."""
        if self.feedback_count:
            raise SkillError('notes_need_reconciliation',
                'Update completed/failed work and current beliefs with write before selecting the next method.')
        try:
            choice = json.loads(content)
            notes = json.loads(self.state['content'])
            method_id = choice['method_id']
            owner, method = next((g, m) for g in notes['goals'] for m in g.get('methods', [])
                                 if m['id'] == method_id)
            notes['active'] = dict(goal_id=owner['id'], method_id=method_id,
                serves=[owner['id'], *method.get('also', [])],
                steps=choice['steps'], next_check=choice['next_check'])
        except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
            raise SkillError('invalid_selection',
                'Select an existing method with JSON method_id, steps and next_check.') from None
        return self.write(json.dumps(notes, separators=(',', ':')), expected_version, **metadata)

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
        self.feedback.clear()
        self.feedback_count = 0
        self.feedback_seconds = 0.
        return self.read()
