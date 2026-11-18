"""Harness option --finish-evidence (route A, default off): item-by-item image evidence before finish(achieved).

Built only from what a real robot has: the public instruction (the model writes its own checklist from it), the
robot's own camera images (image_ref + the point the model cites) and its own step budget. No simulator state,
object identity, private executor code or evaluator result is read.

Protocol (MCP proxy side; the bridge, executor and evaluator are unchanged):
1. finish(outcome=achieved) without a checklist is answered with a request for one (not forwarded).
2. The model calls finish(achieved) again; its `reason` holds one line per required item:
       ITEM: <what x count -> where> | <image_ref> | <x>, <y>
   The image must be no older than the observation returned by the model's last act. Items of the
   first checklist cannot be dropped later: a missing one counts as unverified.
3. The proxy crops each cited point (close + mid scale) and asks a separate, tool-free call of the SAME model on
   the SAME channel, with no episode history, whether each crop shows the item as described ("judge").
4. All items confirmed -> the finish is forwarded. Otherwise, while rounds remain and enough steps remain, the
   finish is not forwarded and the unconfirmed items come back with their crops. When rounds or steps run out, the
   finish is forwarded and recorded as an UNVERIFIED completion (an honesty metric; the evaluator is untouched).
Every decision and the judge's token usage go to <log_dir>/finish_evidence.jsonl.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

MAX_ROUNDS = 3            # checklist request + up to two evidence checks that may send work back
RESERVE_MIN_STEPS = 10
LINE_RE = re.compile(
    r'^\s*(?:[-*]\s*)?(?:ITEM\s*\d*\s*[:.)-]\s*)?(?P<item>[^|]+?)\s*\|\s*(?P<ref>rgb-\d+-[a-z]+|none)\s*'
    r'(?:\|\s*\[?\s*(?P<x>[01](?:\.\d+)?)\s*,\s*(?P<y>[01](?:\.\d+)?)\s*\]?)?\s*$', re.IGNORECASE)

REQUEST = (
    'finish(outcome=achieved) was not accepted yet. Before finishing, list every item the instruction requires and '
    'show image evidence for each. Call finish(outcome=achieved) again with a reason made of one line per item, '
    'exactly:\n  ITEM: <what x count -> where> | <image_ref> | <x>, <y>\n'
    'The image_ref must come from an observation returned by your last act or later (use look, or move and look, so '
    'the item and where it is are visible), and the point must be on that item. Write "| none" if you cannot show it. '
    'The cited points will be cropped and checked one by one.')
JUDGE_INSTRUCTIONS = (
    'You check a household robot\'s claim that a task item is done. For each numbered claim you get two crops of the '
    'robot\'s own camera image around the point the robot cited (close, then wider; the magenta ring marks the '
    'point). Answer only from what the crops show: "yes" if they show the claimed item in the claimed place/state, '
    '"no" if they show something else, "unclear" if the crops do not let you tell.')
JUDGE_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['items'],
    'properties': {'items': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['index', 'verdict', 'what_is_visible'],
        'properties': {'index': {'type': 'integer'}, 'verdict': {'type': 'string', 'enum': ['yes', 'no', 'unclear']},
                       'what_is_visible': {'type': 'string'}}}}}}


def parse_checklist(reason):
    items = []
    for line in str(reason or '').splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        ref = m.group('ref').lower()
        point = None
        if ref != 'none' and m.group('x') is not None:
            point = [float(m.group('x')), float(m.group('y'))]
        items.append({'item': m.group('item').strip()[:200], 'image_ref': None if ref == 'none' else ref,
                      'point': point})
    return items


def _revision(ref):
    try:
        return int(str(ref).split('-')[1])
    except (IndexError, ValueError):
        return None


def _norm(text):
    return re.sub(r'\s+', ' ', str(text).lower()).strip()


class FinishEvidence:
    def __init__(self, *, judge=None, log_dir=None, crop=None):
        """judge(prompt_text, images[(caption, jpeg)]) -> (verdicts dict index->(verdict, note), usage, seconds)
        or None (no judge available: evidence is then only checked for freshness and shown back)."""
        self.judge, self.crop = judge, crop
        self.log = Path(log_dir) / 'finish_evidence.jsonl' if log_dir else None
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
        self.steps_remaining = None
        self.fresh_after = 0          # revision of the observation returned by the latest act
        self.rounds = 0
        self.first_checklist = None
        self.reminded = False
        self.judge_usage = {'calls': 0, 'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0,
                            'reasoning_output_tokens': 0, 'seconds': 0.0}

    def _write(self, record):
        if self.log:
            with self.log.open('a') as f:
                f.write(json.dumps({'at_unix': time.time(), **record}, ensure_ascii=False) + '\n')

    def reserve(self):
        n = len(self.first_checklist or [])
        return max(RESERVE_MIN_STEPS, 2 * n)

    def observe(self, name, result):
        """Track budget and freshness; returns a one-time reminder text or None."""
        if not isinstance(result, dict):
            return None
        budget = result.get('execution_budget')
        if isinstance(budget, dict) and isinstance(budget.get('steps_remaining'), int):
            self.steps_remaining = budget['steps_remaining']
        obs = result.get('observation')
        if name == 'act' and isinstance(obs, dict) and isinstance(obs.get('revision'), int):
            self.fresh_after = obs['revision']
        if (not self.reminded and isinstance(self.steps_remaining, int) and self.steps_remaining > 0
                and self.steps_remaining <= self.reserve()):
            self.reminded = True
            return (f'{self.steps_remaining} act/look steps remain. finish(achieved) needs fresh image evidence for '
                    'each required item (see the finish rules); keep enough steps to look at them.')
        return None

    def on_finish(self, arguments, fetch_image):
        """Returns (forward: bool, payload: dict, images: [(caption, jpeg)])."""
        if not isinstance(arguments, dict) or arguments.get('outcome') != 'achieved':
            return True, {}, []
        items = parse_checklist(arguments.get('reason'))
        if not items and self.rounds == 0:
            self.rounds += 1
            self._write({'decision': 'checklist_requested', 'steps_remaining': self.steps_remaining})
            return False, {'ok': False, 'error': {'code': 'finish_evidence_required', 'action_step_used': False,
                                                  'message': REQUEST}}, []
        self.rounds += 1
        if self.first_checklist is None and items:
            self.first_checklist = [i['item'] for i in items]
        given = {_norm(i['item']) for i in items}
        dropped = [t for t in (self.first_checklist or []) if _norm(t) not in given]
        checks, images, judge_items = [], [], []
        for idx, it in enumerate(items, 1):
            rev = _revision(it['image_ref']) if it['image_ref'] else None
            status = None
            if it['image_ref'] is None or it['point'] is None:
                status = 'no_evidence'
            elif rev is None or rev < self.fresh_after:
                status = 'stale_image'
            else:
                try:
                    data = fetch_image(it['image_ref'])
                except Exception:
                    data = None
                if data is None:
                    status = 'image_unavailable'
                else:
                    crops = self.crop(data, it['point']) if self.crop else []
                    judge_items.append((idx, it, crops))
            checks.append({'index': idx, 'item': it['item'], 'image_ref': it['image_ref'], 'point': it['point'],
                           'status': status})
        judge_record = None
        if judge_items and self.judge is not None:
            text = '\n'.join(f'Claim {idx}: {it["item"]}' for idx, it, _ in judge_items)
            jimgs = []
            for idx, it, crops in judge_items:
                for kind, data in crops:
                    jimgs.append((f'Claim {idx}, {kind} crop', data))
            try:
                verdicts, usage, seconds = self.judge(text, jimgs)
            except Exception as exc:  # judge failure: evidence counts as unclear, never as confirmed
                verdicts, usage, seconds = {}, None, 0.0
                judge_record = {'error': repr(exc)[:300]}
            self.judge_usage['calls'] += 1
            self.judge_usage['seconds'] += float(seconds or 0.0)
            for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens'):
                self.judge_usage[key] += int((usage or {}).get(key) or 0)
            judge_record = {**(judge_record or {}), 'usage': usage, 'seconds': seconds}
            for c in checks:
                if c['status'] is None:
                    verdict, note = verdicts.get(c['index'], ('unclear', ''))
                    c['status'] = {'yes': 'confirmed', 'no': 'not_confirmed'}.get(verdict, 'unclear')
                    c['judge_note'] = str(note)[:200]
        elif judge_items:
            for c in checks:
                if c['status'] is None:
                    c['status'] = 'shown_not_judged'
        open_items = [c for c in checks if c['status'] not in ('confirmed', 'shown_not_judged')]
        missing = bool(dropped) or not checks
        ok = not open_items and not missing
        can_retry = (self.rounds < MAX_ROUNDS and isinstance(self.steps_remaining, int)
                     and self.steps_remaining >= 2)
        decision = 'accepted' if ok else ('sent_back' if can_retry else 'accepted_unverified')
        self._write({'decision': decision, 'round': self.rounds, 'steps_remaining': self.steps_remaining,
                     'fresh_after_revision': self.fresh_after, 'checks': checks, 'dropped_items': dropped,
                     'judge': judge_record, 'judge_usage_total': dict(self.judge_usage)})
        if decision != 'sent_back':
            return True, {'finish_evidence': {'decision': decision}}, []
        for c in open_items:
            if c['status'] == 'not_confirmed' or c['status'] == 'unclear':
                crops = next((cr for idx, _, cr in judge_items if idx == c['index']), [])
                for kind, data in crops:
                    images.append((f'Item {c["index"]} ({c["item"][:80]}): {kind} crop of {c["image_ref"]} at your '
                                   'point; the magenta ring marks it.', data))
        lines = [f'{c["index"]}. {c["item"]} -> {c["status"].replace("_", " ")}'
                 + (f' (checker saw: {c["judge_note"]})' if c.get('judge_note') else '') for c in open_items]
        lines += [f'- {t} -> missing from this checklist' for t in dropped]
        msg = ('finish(outcome=achieved) was not accepted yet: these items are not confirmed by fresh image '
               'evidence:\n' + '\n'.join(lines) + '\nFix or re-check them, take a fresh look, and call '
               'finish(achieved) again with the full checklist (one ITEM line per required item).')
        return False, {'ok': False, 'error': {'code': 'finish_evidence_unconfirmed', 'action_step_used': False,
                                              'message': msg}}, images


def codex_judge(config):
    """Tool-free one-shot call of the same model on the same channel (codex path only), via the isolated
    CodexPlannerRequest helper. config: {model, effort, provider, gateway, output}."""
    import base64
    from ..clients.planner import CodexPlannerRequest
    request = CodexPlannerRequest(output=config['output'], provider=config['provider'], gateway=config.get('gateway'))

    def judge(text, images):
        content = [{'type': 'input_text', 'text': text}]
        for caption, data in images:
            content.append({'type': 'input_text', 'text': caption})
            content.append({'type': 'input_image',
                            'image_url': 'data:image/jpeg;base64,' + base64.b64encode(data).decode()})
        started = time.monotonic()
        reply = request({'model': config['model'], 'reasoning': {'effort': config.get('effort') or 'low'},
                         'instructions': JUDGE_INSTRUCTIONS, 'input': [{'content': content}],
                         'text': {'format': {'schema': JUDGE_SCHEMA}}})
        seconds = time.monotonic() - started
        answer = json.loads(reply['output'][0]['content'][0]['text'])
        verdicts = {int(i['index']): (i['verdict'], i.get('what_is_visible', '')) for i in answer.get('items', [])}
        return verdicts, reply.get('usage'), seconds
    return judge


def agy_judge(config):
    """Same judge for the agy client: a one-shot agy call (same model, same account) whose only tool `look` returns
    the claims and crops; the answer is the same JSON, parsed from the final text. config: {model, effort, output}."""
    import sys
    import uuid
    from dataclasses import replace
    from ..clients.agy import AgyAdapter
    from ..clients.types import ClientConfig
    from ..mcp_server import encode_within_byte_limit

    def judge(text, images):
        out = Path(config['output']) / 'calls' / str(uuid.uuid4())
        out.mkdir(parents=True)
        files = []
        for i, (caption, data) in enumerate(images):
            data, mime = encode_within_byte_limit(data, 'image/jpeg', 9600)
            path = out / (f'crop-{i}.webp' if 'webp' in mime else f'crop-{i}.jpg')
            path.write_bytes(data)
            files.append({'caption': caption, 'path': str(path), 'mime': mime})
        spec = out / 'spec.json'
        spec.write_text(json.dumps({'text': 'Claims to check:\n' + text, 'images': files}))
        prompt = ('Call the `look` tool once. It returns numbered claims and, per claim, a close and a wider crop of a '
                  'robot camera image around the cited point (magenta ring). ' + JUDGE_INSTRUCTIONS +
                  ' Reply ONLY with JSON {"items": [{"index": <claim number>, "verdict": "yes"|"no"|"unclear", '
                  '"what_is_visible": "<short>"}]}.')
        cfg = ClientConfig(client='agy', model=config['model'], instruction=prompt, mcp_command=sys.executable,
                           mcp_args=['-B', '-m', 'manipulation_agent.tools.judge_mcp', str(spec)], output=out,
                           timeout=600, agent_profile='basic-skills', isolate_client_storage=True,
                           reasoning_effort=config.get('effort'))
        adapter = AgyAdapter()
        project = adapter.prepare_project(cfg, JUDGE_INSTRUCTIONS, ['look'])
        project = replace(project, stdin=project.stdin if project.stdin is not None else cfg.prompt)
        started = time.monotonic()
        adapter.run(project, out, 600)
        seconds = time.monotonic() - started
        parsed = adapter.parse(out / 'model_events.jsonl')
        texts = [e.get('content') for e in parsed.canonical if e.get('kind') == 'assistant_text' and e.get('content')]
        usage_rows = [e.get('usage') for e in parsed.canonical if e.get('kind') == 'usage' and e.get('usage')]
        raw = (texts[-1] if texts else '').strip().strip('`').removeprefix('json').strip()
        m = re.search(r'\{.*\}', raw, re.S)
        answer = json.loads(m.group(0)) if m else {}
        verdicts = {int(i['index']): (str(i.get('verdict', 'unclear')).lower(), i.get('what_is_visible', ''))
                    for i in answer.get('items', []) if isinstance(i, dict) and 'index' in i}
        u = usage_rows[-1] if usage_rows else {}
        usage = {'input_tokens': u.get('input_tokens'), 'cached_input_tokens': u.get('cached_input_tokens'),
                 'output_tokens': u.get('output_tokens'), 'reasoning_output_tokens': u.get('thinking_tokens')}
        return verdicts, usage, seconds
    return judge
