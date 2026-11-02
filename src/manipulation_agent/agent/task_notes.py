"""Versioned model-authored text; the same storage contract in both experiment arms."""
import copy
import json
from collections import deque
from ..contracts import SkillError
from ..records import now, write_json
from .task_planning import task_advice


def merge_goal_update(content, update):
    """Apply model-authored changes; also used to reconstruct exact replay notes."""
    notes, patch = json.loads(content), json.loads(update)
    if not isinstance(notes, dict) or notes.get('format') != 'goal-tree-v2' or not isinstance(patch, dict):
        raise ValueError('update requires existing goal-tree-v2 notes and a JSON object')
    goals = {g['id']: g for g in notes['goals']}
    for change in patch.get('goals', []):
        key = change['id']
        goals[key] = {**goals.get(key, {}), **change}
    merged = {**notes, **patch, 'goals': list(goals.values()), 'active': patch.get('active')}
    if 'planning' in patch:
        merged['planning'] = {**notes.get('planning', {}), **patch['planning']}
    return json.dumps(merged, separators=(',', ':'))


class TaskNotes:
    def __init__(self, output):
        self.output = output
        self.state = {'version': 0, 'content': ''}
        self.feedback = deque(maxlen=64)
        self.feedback_count = 0
        self.feedback_seconds = 0.
        self.feedback_simulation_seconds = 0.
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
        elapsed = result.get('runtime', {}).get('observed_simulation_seconds')
        self.feedback_simulation_seconds = (self.feedback_simulation_seconds + elapsed
            if elapsed is not None and self.feedback_simulation_seconds is not None else None)

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
        pending_sim = pending.get('observed_simulation_seconds') if pending is not None else 0.
        simulation_seconds = (self.feedback_simulation_seconds + pending_sim
            if self.feedback_simulation_seconds is not None and pending_sim is not None else None)
        return dict(status='reconcile', notes_version=self.state['version'],
            checkpoint_status=advice['status'], active=advice.get('active'),
            checkpoint_coverage=advice.get('coverage'),
            selection=advice.get('selection'),
            next=None, actions_since_checkpoint=count,
            execution=dict(tool_seconds=round(self.feedback_seconds +
                (pending or {}).get('tool_seconds', 0.), 3),
                observed_simulation_seconds=simulation_seconds,
                scope='Tool wall time excludes model requests; simulation delta comes from public capture timestamps'),
            latest_receipt={k: latest[k] for k in ('primitive', 'ok', 'effect', 'error') if k in latest},
            failed_since_checkpoint=failed,
            next_decision=('No method was selected at this checkpoint. Reconcile actual progress and '
                           'known unfinished work, read the comparison, then select a method.'
                           if not (advice.get('selection') or {}).get('method_id') else
                          'Reconcile the failure and affected goals/resources from RGB and receipts; '
                           'update changed nodes and beliefs to compare revised methods.' if failed else
                           'Check the returned RGB/receipt against the current method. Continue its local steps '
                           'if supported; on completion, new information or a branch change, update notes '
                           'to recompute the remaining DAG. Use operation=update to keep unchanged branches. '
                           'Tool success alone does not complete a goal.'))

    def update(self, content, expected_version, **metadata):
        """Reconcile changed results and beliefs without resending other branches."""
        try:
            merged = merge_goal_update(self.state['content'], content)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise SkillError('invalid_notes_update',
                'Update existing goal-tree-v2 notes with changed goals by id and changed planning fields; '
                'other top-level fields replace their previous value.') from None
        return self.write(merged, expected_version, **metadata)

    def select(self, content, expected_version, **metadata):
        """Record the choice after seeing a comparison, without rewriting the graph."""
        if self.feedback_count:
            raise SkillError('notes_need_reconciliation',
                'Reconcile completed/failed work and current beliefs with update or write before selecting the next method.')
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
        self.feedback_simulation_seconds = 0.
        return self.read()
