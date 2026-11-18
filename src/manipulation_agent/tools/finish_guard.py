"""Optional deferral of early finishes while act/look budget remains (MCP proxy only).

Harness-side component, off by default: `mcp_server --finish-guard on`. The simulator, executor, evaluator and
the bridge tool catalog are identical with the guard on or off; a deferred finish never reaches the bridge.

Rules (ported unchanged from the frozen experiment line, terra_direct_mcp_20261104 source_infra_clean4..carry11,
with optional `--state-memory on` verification upgrade):
- `achieved` is deferred while >=5 steps remain, at most twice (or up to 3 times when `--state-memory on` detects
  concrete unresolved public state such as a held item, active process, or unverified drop); after a deferral it is
  accepted once at least one act/look call followed and no active public state blocker remains.
- When `--state-memory on` is active, enclosures recorded in `state_memory` with verified `place_inside` followed by
  `close` count as memory-verified so the agent is explicitly instructed NOT to reopen verified closed doors/enclosures.
- `blocked` and `aborted` share one counter. They are deferred while >=15 steps remain, at most 5 times; after a
  deferral a repeated stop is accepted only after 3 failed act/look attempts in a row across materially distinct
  recovery primitives (a completed act restarts the count).
- Every deferral lists the model's own act calls that the executor reported completed. No task, scene, object
  identity, private state or evaluator result.
"""
import json
from pathlib import Path

from ..agent.state_memory import StateMemory

THRESHOLDS = {'blocked': 15, 'aborted': 15, 'achieved': 5}
MAX_DEFERRALS = {'stop': 5, 'achieved': 2}
MAX_ACHIEVED_ISSUE_DEFERRALS = 3
STOP_OUTCOMES = ('blocked', 'aborted')
ATTEMPTS = 3
INTENT_CHARS = 140
REASON_CHARS = 400
MESSAGES = {
    'blocked': ('finish(outcome={outcome}) was not accepted yet: {remaining} act/look steps remain. Remaining work is not '
                'a blocker, and missed pixel selections (nothing_lifted, not_placed, no_visible_change, '
                'placement errors) are not blocked subgoals. Reinspect the newest RGB, move closer or use another view, '
                'and reselect. finish(outcome={outcome}) is accepted only after at least {attempts} further act/look attempts '
                'in a row have failed on the obstacle (a completed act is progress and restarts this count), or when '
                'fewer than {threshold} steps remain.'),
    'blocked_repeat': ('finish(outcome={outcome}) was not accepted yet: {remaining} act/look steps remain and {made} '
                       'failed act/look attempt(s) in a row followed the previous deferral. Make at least {attempts} '
                       'materially different attempts (another view, stance, target point, or order of the remaining '
                       'subgoals) before finishing {outcome} again; a completed act is progress, so continue from it.'),
    'blocked_same_primitive': ('finish(outcome={outcome}) was not accepted yet: {remaining} act/look steps remain and '
                               'all {made} failed action attempts repeated the same primitive '
                               '({primitive}) without trying a structurally different recovery action. Repeating '
                               'grasp/cut on a closed door or distant frame is not a blocked task: try open on a '
                               'closed door/drawer, navigate_to to walk closer or enter another room, release to free '
                               'your hand, or work on another independent subgoal first.'),
    'achieved': ('finish(outcome=achieved) was not accepted yet: {remaining} act/look steps remain. Verify the '
                 'instruction against fresh RGB before finishing: use look (or move) so that every required object '
                 'and its destination are visible in a new image, and count each required item in that image, not '
                 'from memory. A required item you cannot see in any current view is not verified: find it. '
                 'Continue any unresolved item. Then finish again with a reason that lists, item by '
                 'item, the count you see and the image_ref where you see it.'),
    'achieved_memory': ('finish(outcome=achieved) was not accepted yet: {remaining} act/look steps remain. Perform '
                        'an item-by-item check against fresh RGB and your public state_memory before finishing:\n'
                        '1. Enclosures recorded in state_memory as verified_closed after verified place_inside are '
                        'ALREADY confirmed — DO NOT reopen verified closed doors/enclosures just to re-view their '
                        'interior, as reopening can disturb settled items or violate closed-state requirements.\n'
                        '2. For open/exterior destinations, inspect fresh RGB (use look or navigate_to if needed) '
                        'and count visible items; check whether any currently open enclosure must be closed.\n'
                        '3. Then call finish(outcome=achieved) listing RGB image_ref evidence for visible targets '
                        'and state_memory records for verified closed enclosures.'),
    'achieved_unresolved': ('finish(outcome=achieved) was not accepted yet: {remaining} act/look steps remain and '
                            'public execution records show unresolved state:\n{issues}\n'
                            'Resolve each item above (without needlessly reopening already-verified closed enclosures), '
                            'verify in fresh RGB, then call finish(outcome=achieved) again.'),
    'achieved_repeat': ('finish(outcome=achieved) was not accepted yet: no look or act followed the previous '
                        'deferral. Make at least one fresh observation that shows the required results, count them '
                        'in it, then finish again.'),
}

# Counters are persisted so that a client restart (provider resume, rolling handoff, unfinished-turn nudge) keeps them.
STATE_FIELDS = ('steps_remaining', 'deferrals', 'calls_since_blocked', 'calls_since_achieved', 'completed',
                'failures_in_row', 'failed_primitives_in_row', 'history')


class FinishGuard:
    def __init__(self, path=None, *, state_memory=False):
        self.steps_remaining = None
        self.deferrals = {}
        self.calls_since_blocked = 0
        self.calls_since_achieved = 0
        self.completed = []
        self.failures_in_row = 0
        self.failed_primitives_in_row = []
        self.history = []
        self.state_memory_enabled = bool(state_memory)
        self.memory = StateMemory() if self.state_memory_enabled else None
        self.path = path
        if path:
            try:
                state = json.loads(Path(path).read_text())
                for key in STATE_FIELDS:
                    if key in state:
                        setattr(self, key, state[key])
                self.completed = [tuple(item) for item in self.completed]
                if state.get('state_memory_enabled') and isinstance(state.get('state_memory_data'), dict):
                    self.state_memory_enabled = True
                    self.memory = StateMemory.from_dict(state['state_memory_data'])
            except Exception:
                pass

    def _save(self):
        if not self.path:
            return
        try:
            path = Path(self.path)
            tmp = path.with_name(path.name + '.tmp')
            payload = {key: getattr(self, key) for key in STATE_FIELDS}
            if self.state_memory_enabled and self.memory is not None:
                payload['state_memory_enabled'] = True
                payload['state_memory_data'] = self.memory.to_dict()
            tmp.write_text(json.dumps(payload, ensure_ascii=False))
            tmp.replace(path)
        except Exception:
            pass

    def observe(self, result, name=None, arguments=None):
        """Update the counters from one bridge result. Never changes the result."""
        if isinstance(result, dict) and 'state_memory' in result and not self.state_memory_enabled:
            self.state_memory_enabled = True
            if self.memory is None:
                self.memory = StateMemory()
        if self.state_memory_enabled and self.memory is not None and name is not None and isinstance(result, dict):
            self.memory.observe(name, arguments if isinstance(arguments, dict) else {}, result)

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
                prim = str((arguments or {}).get('primitive') or name)
                self.failed_primitives_in_row.append(prim)
            elif completed:
                self.failures_in_row = 0
                self.failed_primitives_in_row = []
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

    def _memory_unresolved_issues(self) -> list[str]:
        if not self.state_memory_enabled or self.memory is None:
            return []
        issues: list[str] = []
        if not self.completed:
            issues.append('- No manipulation action has completed yet in this episode.')
        if self.memory.hand_holding.get('status') == 'holding_item':
            held = self.memory.hand_holding.get('item') or 'held_item'
            issues.append(
                f'- Gripper is still holding an item ({held}); place it at its required destination or release it.'
            )
        if self.memory.process_state.get('any_running'):
            el = self.memory.process_state.get('elapsed_active_s') or 0.0
            issues.append(
                f'- An active background process is still running (elapsed {el:.1f}s); complete independent work '
                'or wait until an action receipt reports any_process_running=False.'
            )
        unverified = self.memory.unverified_items()
        if unverified:
            issues.append(
                f"- Item(s) released without verified placement postcondition: {', '.join(unverified[:4])}; "
                'inspect RGB and re-place if displaced.'
            )
        if self.memory.unresolved_notes_goals:
            issues.append(
                f"- Unfinished subgoals remain in your task_notes: {', '.join(self.memory.unresolved_notes_goals[:6])}."
            )
        return issues

    def _memory_checklist_block(self) -> str:
        if not self.state_memory_enabled or self.memory is None:
            return ''
        parts = [self.memory.render_memory_summary()]
        closed_verified = self.memory.verified_closed_containers()
        if closed_verified:
            desc = '; '.join(
                f"{cid}@{info['location']} ({len(info['items_inside'])} verified item(s) inside: "
                f"{', '.join(info['items_inside'][:6]) or 'none'}, closed at step {info.get('closed_at_step')})"
                for cid, info in list(closed_verified.items())[:6]
            )
            parts.append(f'Memory-verified closed enclosures (do NOT reopen): {desc}.')
        open_map = self.memory.open_containers()
        if open_map:
            open_desc = ', '.join(
                f"{cid}@{info['location']} ({len(info['items_inside'])} item(s) placed inside)"
                for cid, info in list(open_map.items())[:6]
            )
            parts.append(f'Currently open enclosures (close if required by instruction): {open_desc}.')
        return '\n'.join(parts)

    def receipts(self):
        if not self.completed:
            base = 'Your act calls have reported no completed operations in this episode.'
        else:
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
            tail = (
                'This lists your own successful act calls; cross-check exterior items in fresh RGB and rely on '
                'verified_closed enclosure records in state_memory rather than reopening closed doors.'
                if self.state_memory_enabled
                else 'This lists only your own successful act calls; objects may have moved since. Compare it with '
                     'every object, quantity, destination and final hand state the instruction requires, and with '
                     'the newest RGB.'
            )
            base = (
                'Completed operations per primitive: ' + summary + '.\n'
                'Your act calls that the executor reported completed in this episode (step, primitive, and your own '
                'stated intent when you gave one):\n' + '\n'.join(lines) + '\n' + tail
            )
        mem_block = self._memory_checklist_block()
        return base + ('\n' + mem_block if mem_block else '')

    def deferral(self, arguments, *, evidence_mode=False):
        """Return the deferral result for this finish request, or None when the finish goes to the bridge.

        evidence_mode (v10b, --finish-evidence on): for finish(achieved) only the unresolved-records deferral is
        kept; the format/verification deferrals are left to the finish-evidence check. Without it: unchanged."""
        outcome = arguments.get('outcome') if isinstance(arguments, dict) else None
        if outcome not in THRESHOLDS or self.steps_remaining is None or self.steps_remaining < THRESHOLDS[outcome]:
            return None
        counter = 'stop' if outcome in STOP_OUTCOMES else outcome
        count = self.deferrals.get(counter, 0)

        if counter == 'stop':
            if count >= MAX_DEFERRALS['stop']:
                return None
            if self.state_memory_enabled:
                action_prims = [p for p in self.failed_primitives_in_row if p != 'look']
                distinct_failed = set(action_prims)
                if count and self.failures_in_row >= ATTEMPTS and len(distinct_failed) >= 2:
                    return None
                if len(action_prims) >= 2 and len(distinct_failed) == 1:
                    only_prim = next(iter(distinct_failed))
                    message = MESSAGES['blocked_same_primitive'].format(
                        outcome=outcome, remaining=self.steps_remaining,
                        made=len(action_prims), primitive=only_prim,
                    )
                else:
                    key = 'blocked_repeat' if count else 'blocked'
                    message = MESSAGES[key].format(outcome=outcome, remaining=self.steps_remaining,
                                                   made=self.failures_in_row, attempts=ATTEMPTS,
                                                   threshold=THRESHOLDS[outcome])
                    if self.memory is not None and self.memory.unresolved_notes_goals and self.failures_in_row == 0:
                        message += (
                            ' Unfinished subgoals still listed in your task_notes: '
                            + ', '.join(self.memory.unresolved_notes_goals[:6]) + '.'
                        )
            else:
                distinct_failed = set(self.failed_primitives_in_row)
                if count and self.failures_in_row >= ATTEMPTS and len(distinct_failed) >= 2:
                    return None
                if count and self.failures_in_row >= ATTEMPTS and len(distinct_failed) == 1:
                    only_prim = next(iter(distinct_failed))
                    message = MESSAGES['blocked_same_primitive'].format(
                        outcome=outcome, remaining=self.steps_remaining, made=self.failures_in_row, primitive=only_prim)
                else:
                    key = 'blocked_repeat' if count else 'blocked'
                    message = MESSAGES[key].format(outcome=outcome, remaining=self.steps_remaining,
                                                   made=self.failures_in_row, attempts=ATTEMPTS,
                                                   threshold=THRESHOLDS[outcome])
            self._record('deferred', arguments)
            self.calls_since_blocked = 0
            self.failures_in_row = 0
            self.failed_primitives_in_row = []
        else:
            if evidence_mode:
                issues = self._memory_unresolved_issues() if self.state_memory_enabled else []
                if not issues or count >= MAX_ACHIEVED_ISSUE_DEFERRALS:
                    return None
                message = MESSAGES['achieved_unresolved'].format(remaining=self.steps_remaining,
                                                                 issues='\n'.join(issues))
            elif self.state_memory_enabled:
                issues = self._memory_unresolved_issues()
                cap = MAX_ACHIEVED_ISSUE_DEFERRALS if issues else MAX_DEFERRALS['achieved']
                if count >= cap:
                    return None
                if issues:
                    message = MESSAGES['achieved_unresolved'].format(
                        remaining=self.steps_remaining, issues='\n'.join(issues),
                    )
                else:
                    if count and self.calls_since_achieved >= 1:
                        return None
                    key = 'achieved_repeat' if count else 'achieved_memory'
                    message = MESSAGES[key].format(remaining=self.steps_remaining)
            else:
                if count >= MAX_DEFERRALS['achieved']:
                    return None
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
