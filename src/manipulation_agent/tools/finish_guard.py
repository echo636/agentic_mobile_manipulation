"""Optional deferral of early finishes while act/look budget remains (MCP proxy only).

Harness-side component, off by default: `mcp_server --finish-guard on`. The simulator, executor, evaluator and
the bridge tool catalog are identical with the guard on or off; a deferred finish never reaches the bridge.

Rules (ported unchanged from the frozen experiment line, terra_direct_mcp_20261104 source_infra_clean4..carry11):
- `achieved` is deferred while >=5 steps remain, at most twice; after a deferral it is accepted once at least one
  act/look call followed.
- `blocked` and `aborted` share one counter. They are deferred while >=15 steps remain, at most 5 times; after a
  deferral a repeated stop is accepted only after 3 failed act/look attempts in a row (a completed act restarts
  the count).
- Every deferral lists the model's own act calls that the executor reported completed. No task, scene, object
  identity, private state or evaluator result.

Adaptations to the merged executor:
- The merged receipts have no `error.action_step_used`. A failed act/look counts as an attempt when the public
  step budget went down, or when no budget was reported (the frozen default).
- The merged act schema carries `intent` only with the tool-intent protocol; without it the receipt lists the
  primitive alone.
- The merged receipts report no hand state, so the achieved message no longer refers to `robot.hand`.
- Every deferral and every accepted finish seen by the guard is appended to the state file as `history`
  (analysis only, never shown to the model).
"""
import json
from pathlib import Path

THRESHOLDS = {'blocked': 15, 'aborted': 15, 'achieved': 5}
MAX_DEFERRALS = {'stop': 5, 'achieved': 2}
STOP_OUTCOMES = ('blocked', 'aborted')
ATTEMPTS = 3
INTENT_CHARS = 140
REASON_CHARS = 400
MESSAGES = {
    'blocked': ('finish(outcome={outcome}) was not accepted yet: {remaining} act/look steps remain. Remaining work is not '
                'a blocker, and missed pixel selections (fixed_object, pre_condition_error, unsupported_relation, '
                'placement errors) are not blocked subgoals. Reinspect the newest RGB, move closer or use another view, '
                'and reselect. finish(outcome={outcome}) is accepted only after at least {attempts} further act/look attempts '
                'in a row have failed on the obstacle (a completed act is progress and restarts this count), or when '
                'fewer than {threshold} steps remain.'),
    'blocked_repeat': ('finish(outcome={outcome}) was not accepted yet: {remaining} act/look steps remain and {made} '
                       'failed act/look attempt(s) in a row followed the previous deferral. Make at least {attempts} '
                       'materially different attempts (another view, stance, target point, or order of the remaining '
                       'subgoals) before finishing {outcome} again; a completed act is progress, so continue from it.'),
    'achieved': ('finish(outcome=achieved) was not accepted yet: {remaining} act/look steps remain. Verify the '
                 'instruction against fresh RGB before finishing: use look (or move) so that every required object '
                 'and its destination are visible in a new image, and count each required item in that image, not '
                 'from memory. A required item you cannot see in any current view is not verified: find it. '
                 'Continue any unresolved item. Then finish again with a reason that lists, item by '
                 'item, the count you see and the image_ref where you see it.'),
    'achieved_repeat': ('finish(outcome=achieved) was not accepted yet: no look or act followed the previous '
                        'deferral. Make at least one fresh observation that shows the required results, count them '
                        'in it, then finish again.'),
}

# Counters are persisted so that a client restart (provider resume, unfinished-turn nudge) keeps them.
STATE_FIELDS = ('steps_remaining', 'deferrals', 'calls_since_blocked', 'calls_since_achieved', 'completed',
                'failures_in_row', 'history')


class FinishGuard:
    def __init__(self, path=None):
        self.steps_remaining = None
        self.deferrals = {}
        self.calls_since_blocked = 0
        self.calls_since_achieved = 0
        self.completed = []
        self.failures_in_row = 0
        self.history = []
        self.path = path
        if path:
            try:
                state = json.loads(Path(path).read_text())
                for key in STATE_FIELDS:
                    if key in state:
                        setattr(self, key, state[key])
                self.completed = [tuple(item) for item in self.completed]
            except Exception:
                pass

    def _save(self):
        if not self.path:
            return
        try:
            path = Path(self.path)
            tmp = path.with_name(path.name + '.tmp')
            tmp.write_text(json.dumps({key: getattr(self, key) for key in STATE_FIELDS}, ensure_ascii=False))
            tmp.replace(path)
        except Exception:
            pass

    def observe(self, result, name=None, arguments=None):
        """Update the counters from one bridge result. Never changes the result."""
        before = self.steps_remaining
        budget = result.get('execution_budget') if isinstance(result, dict) else None
        reported = isinstance(budget, dict) and isinstance(budget.get('steps_remaining'), int)
        if reported:
            self.steps_remaining = budget['steps_remaining']
        if name in ('act', 'look'):
            self.calls_since_blocked += 1
            self.calls_since_achieved += 1
        effect = result.get('effect') if isinstance(result, dict) and result.get('ok') else None
        completed = name == 'act' and isinstance(effect, dict) and effect.get('status') == 'completed'
        if name in ('act', 'look') and isinstance(result, dict):
            error = result.get('error') if isinstance(result.get('error'), dict) else {}
            step_used = error.get('action_step_used')
            if step_used is None:
                step_used = not (reported and isinstance(before, int)) or self.steps_remaining < before
            if not result.get('ok') and step_used:
                self.failures_in_row += 1
            elif completed:
                self.failures_in_row = 0
        if completed:
            intent = ' '.join(str((arguments or {}).get('intent') or '').split())
            if len(intent) > INTENT_CHARS:
                intent = intent[:INTENT_CHARS - 3] + '...'
            pieces = (effect.get('cut') or {}).get('pieces_created') if isinstance(effect.get('cut'), dict) else None
            step = budget.get('steps_used') if isinstance(budget, dict) else None
            self.completed.append((step, str(effect.get('primitive')), intent,
                                   pieces if isinstance(pieces, int) else None))
        if name == 'finish' and isinstance(result, dict):
            self._record('accepted' if result.get('ok') else 'rejected_by_bridge', arguments)
        self._save()

    def _record(self, decision, arguments):
        arguments = arguments if isinstance(arguments, dict) else {}
        self.history.append({'decision': decision, 'outcome': arguments.get('outcome'),
                             'reason': str(arguments.get('reason') or '')[:REASON_CHARS],
                             'steps_remaining': self.steps_remaining, 'failures_in_row': self.failures_in_row,
                             'completed_operations': len(self.completed)})

    def receipts(self):
        if not self.completed:
            return 'Your act calls have reported no completed operations in this episode.'
        lines = []
        for step, primitive, intent, pieces in self.completed:
            extra = f' ({pieces} pieces created)' if pieces else ''
            lines.append(f'- step {step}: {primitive}{extra}' + (f': {intent}' if intent else ''))
        totals = {}
        for _, primitive, _, pieces in self.completed:
            count, made = totals.get(primitive, (0, 0))
            totals[primitive] = (count + 1, made + (pieces or 0))
        summary = ', '.join(f'{primitive} x{count}' + (f' ({made} pieces created in total)' if made else '')
                            for primitive, (count, made) in totals.items())
        return ('Completed operations per primitive: ' + summary + '.\n'
                'Your act calls that the executor reported completed in this episode (step, primitive, and your own '
                'stated intent when you gave one):\n' + '\n'.join(lines) + '\nThis lists only your own successful act '
                'calls; objects may have moved since. Compare it with every object, quantity, destination and final '
                'hand state the instruction requires, and with the newest RGB.')

    def deferral(self, arguments):
        """Return the deferral result for this finish request, or None when the finish goes to the bridge."""
        outcome = arguments.get('outcome') if isinstance(arguments, dict) else None
        if outcome not in THRESHOLDS or self.steps_remaining is None or self.steps_remaining < THRESHOLDS[outcome]:
            return None
        counter = 'stop' if outcome in STOP_OUTCOMES else outcome
        count = self.deferrals.get(counter, 0)
        if count >= MAX_DEFERRALS[counter]:
            return None
        if counter == 'stop':
            if count and self.failures_in_row >= ATTEMPTS:
                return None
            key = 'blocked_repeat' if count else 'blocked'
            message = MESSAGES[key].format(outcome=outcome, remaining=self.steps_remaining,
                                           made=self.failures_in_row, attempts=ATTEMPTS,
                                           threshold=THRESHOLDS[outcome])
            self._record('deferred', arguments)
            self.calls_since_blocked = 0
            self.failures_in_row = 0
        else:
            if count and self.calls_since_achieved >= 1:
                return None
            message = MESSAGES['achieved_repeat' if count else outcome].format(remaining=self.steps_remaining)
            self._record('deferred', arguments)
            self.calls_since_achieved = 0
        self.deferrals[counter] = count + 1
        self._save()
        return {'ok': False, 'error': {'code': 'finish_deferred_budget_remaining', 'world_may_have_changed': False,
                                       'message': message + '\n' + self.receipts()},
                'execution_budget': {'steps_remaining': self.steps_remaining},
                'finish_guard': {'deferred': dict(sorted(self.deferrals.items()))}}
