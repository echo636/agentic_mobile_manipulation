"""An optional visual planner. It advises; only the actor can execute actions."""
from __future__ import annotations

from collections import deque
from graphlib import TopologicalSorter
import hashlib
import json
import time
from pathlib import Path
import uuid

from ..records import JsonlAppend, now, write_json

ACTOR_INSTRUCTION = '''
Use plan_task after the initial inspection to obtain a work plan. Execute its next work segment using current RGB and your manipulation skills. Consult it again when a segment is complete, new facts change the method, or progress is blocked; handle ordinary local corrections yourself. Give a short progress summary with important discoveries, remaining work and uncertainties. The planner receives the current images and recent action feedback automatically. Its plan is advice: revise it when observations disagree.
'''

PLANNER_INSTRUCTION = '''Plan efficient robot work from the user's task, RGB observations and public action feedback.
Return one DAG of remaining work with observable outcomes, and select the next coherent work segment. Cover the whole remaining task coarsely; detail only near-term work. Keep uncertain identities, locations and results uncertain. Discover missing facts when needed without making unrelated work wait.
Write each work entry as one short sentence naming the work and its result; leave atomic-tool procedures to the actor.
Compare feasible ways to achieve the same results, including changing work order, combining work, reusing tools or setup, carrying groups and doing useful work during timed processes. Account for extra handling and likely recovery, not just travel. Choose one practical arrangement; do not output candidate debates or invented numerical costs.
Edges are necessary prerequisites, not a preferred sequence. Preserve unaffected unfinished work when revising and remove completed work. Use stable node ids. Return concise JSON: nodes[{id, work, after}], next (an ordered list of node ids for the next segment). When nothing remains, both lists are empty. You advise; the actor selects pixels and executes. Do not claim success from a plan or an unverified actor belief.
'''

PLAN_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'nodes': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'id': {'type': 'string'}, 'work': {'type': 'string'},
                           'after': {'type': 'array', 'items': {'type': 'string'}}},
            'required': ['id', 'work', 'after']}},
        'next': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['nodes', 'next'],
}

PLANNER_TOOL = {
    'name': 'plan_task',
    'description': 'Ask the visual planning subagent for an efficient remaining-work DAG and next segment. '
                   'Summarize progress, discoveries and uncertainties briefly. Current RGB and recent '
                   'public action feedback are supplied automatically. This does not move the robot.',
    'inputSchema': {'type': 'object', 'properties': {'progress': {'type': 'string'}},
                    'required': ['progress'], 'additionalProperties': False},
    'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': True},
}


def parse_plan(response):
    text = ''.join(part.get('text', '') for item in response.get('output', [])
                   if item.get('type') == 'message' for part in item.get('content', [])
                   if part.get('type') == 'output_text')
    plan = json.loads(text)
    # Check only the returned graph contract; no world-state or action gates.
    nodes = {node['id']: node for node in plan['nodes']}
    if len(nodes) != len(plan['nodes']) or any(
            dependency not in nodes for node in nodes.values() for dependency in node['after']):
        raise ValueError('Plan has duplicate or missing node ids')
    tuple(TopologicalSorter({key: node['after'] for key, node in nodes.items()}).static_order())
    completed = set()
    for key in plan['next']:
        if key not in nodes or key in completed or not set(nodes[key]['after']) <= completed:
            raise ValueError('Next segment does not respect its prerequisites')
        completed.add(key)
    if nodes and not completed:
        raise ValueError('Remaining work has no next segment')
    return plan


class PlanningSubagent:
    def __init__(self, *, instruction, model, effort, request, output, capabilities):
        self.instruction, self.model, self.effort = instruction, model, effort
        self.request, self.capabilities = request, capabilities
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.events = JsonlAppend(self.output / 'events.jsonl')
        plan_path = self.output / 'plan.json'
        self.plan = json.loads(plan_path.read_text()) if plan_path.exists() else None
        self.observation = None
        self.images = []
        self.feedback = deque(maxlen=8)
        self.feedback_count = 0
        self.action_seconds = 0.

    def observe(self, name, arguments, result):
        """Receive only the same MCP reply as the actor, including actual pixels."""
        body = next((json.loads(part.text) for part in result.content
                     if part.type == 'text' and part.text.startswith('{')), {})
        observation = body.get('observation')
        if observation and observation.get('observation_mode') == 'rgb_only':
            images = [part for part in result.content if part.type == 'image']
            if len(images) != len(observation['images']) or not images:
                raise ValueError('RGB metadata arrived without matching image content')
            self.observation, self.images = observation, images
        if name in {'act', 'look'}:
            self.feedback.append({'tool': name, 'arguments': arguments,
                **{key: body[key] for key in ('ok', 'evidence_id', 'effect', 'error', 'runtime') if key in body}})
            self.feedback_count += 1
            self.action_seconds += body.get('runtime', {}).get('tool_seconds', 0.)

    def consult(self, progress):
        if self.observation is None:
            return {'ok': False, 'error': 'Initialize to obtain current RGB before consulting the planner.'}
        context = {'task': self.instruction, 'progress_from_actor': progress,
            'previous_plan': self.plan, 'observation': self.observation,
            'recent_action_feedback': list(self.feedback),
            'earlier_feedback_omitted': self.feedback_count - len(self.feedback),
            'actions_since_plan': self.feedback_count, 'action_seconds_since_plan': self.action_seconds}
        content = [{'type': 'input_text', 'text': json.dumps(context, ensure_ascii=False)}]
        for frame, part in zip(self.observation['images'], self.images):
            content += [{'type': 'input_text', 'text': f'RGB view={frame["view"]}, image_ref={frame["image_ref"]}'},
                        {'type': 'input_image', 'image_url': f'data:{part.mimeType};base64,{part.data}',
                         'detail': 'original'}]
        payload = {'model': self.model, 'instructions': PLANNER_INSTRUCTION + '\n\nRobot capabilities:\n' + self.capabilities,
                   'reasoning': {'effort': self.effort}, 'store': False,
                   'input': [{'role': 'user', 'content': content}],
                   'text': {'format': {'type': 'json_schema', 'name': 'remaining_work',
                                       'strict': True, 'schema': PLAN_SCHEMA}}}
        call_id = str(uuid.uuid4())
        started = time.monotonic()
        self.events.append({'at': now(), 'kind': 'planner_request', 'call_id': call_id,
            'model': self.model, 'effort': self.effort, 'context': context,
            'instructions': payload['instructions'], 'image_count': len(self.images),
            'image_refs': self.observation['images'],
            'request_sha256': hashlib.sha256(json.dumps(payload).encode()).hexdigest()})
        try:
            response = self.request(payload)
            # Retain only public output text and usage, never private reasoning.
            public = [item for item in response.get('output', []) if item.get('type') == 'message']
            self.events.append({'at': now(), 'kind': 'planner_response', 'call_id': call_id,
                'latency_seconds': time.monotonic() - started, 'usage': response.get('usage'),
                'status': response.get('status'), 'client_call': response.get('client_call'), 'output': public})
            self.plan = parse_plan(response)
            write_json(self.output / 'plan.json', self.plan)
        except (ValueError, KeyError, TypeError, RuntimeError, OSError) as exc:
            # Preserve the prior plan and evidence. A planner failure never blocks act/look.
            error = {'ok': False, 'error': 'Planner did not return a usable plan; continue from RGB or consult again.',
                     'error_type': type(exc).__name__}
            self.events.append({'at': now(), 'kind': 'planner_error', 'call_id': call_id, **error})
            return error
        self.feedback.clear()
        self.feedback_count, self.action_seconds = 0, 0.
        return {'ok': True, **self.plan}
