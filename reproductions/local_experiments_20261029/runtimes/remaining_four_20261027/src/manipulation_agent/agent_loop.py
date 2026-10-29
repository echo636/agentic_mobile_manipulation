"""Native visual tool loop, independent of model transport and simulator APIs."""
import json
import time
from dataclasses import asdict, dataclass

from .contracts import SkillError
from .image_history import ImageHistory
from .records import now, write_json


@dataclass(frozen=True)
class LoopConfig:
    max_turns: int = 100
    max_tokens: int = 150000
    max_output_tokens: int = 4000
    image_history_captures: int = 0

    def __post_init__(self):
        for name in ('max_turns', 'max_tokens', 'max_output_tokens'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(name + ' must be a positive integer')
        if isinstance(self.image_history_captures, bool) or not isinstance(self.image_history_captures, int) or self.image_history_captures < 0:
            raise ValueError('image_history_captures must be a nonnegative integer')


def public_model_output(output):
    """Record returned assistant messages and summaries, never opaque reasoning."""
    result = []
    for item in output:
        if item.get('type') in {'message', 'function_call'}:
            result.append(item)
        elif item.get('type') == 'reasoning':
            summaries = [part.get('text', '') for part in item.get('summary', [])
                         if part.get('type') == 'summary_text' and part.get('text')]
            if summaries:
                result.append({'type': 'reasoning_summary', 'summary': summaries})
    return result


class VisualToolLoop:
    def __init__(self, *, model, instructions, request, config=LoopConfig()):
        self.model, self.instructions, self.request, self.config = model, instructions, request, config

    def run(self, harness, instruction):
        config = self.config
        specs = [{'type': 'function', 'name': tool['name'], 'description': tool['description'],
                  'parameters': tool['inputSchema'], 'strict': True} for tool in harness.tool_specs()]
        images = ImageHistory(config.image_history_captures)
        history = [{'role': 'user', 'content': instruction}]
        tokens = 0
        reason = 'model_turn_budget_exhausted'
        turn = None
        def canonical_event(kind, *, at=None, **fields):
            event = {'at': at or now(), 'kind': kind, 'client': 'responses', 'turn': turn,
                     'source': 'native_rgb_loop', **fields}
            with (harness.recorder.output / 'canonical_events.jsonl').open('a') as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')

        def replay_event(state, item):
            # This is an explicitly normalized view, not a fabricated raw MCP
            # response. The authoritative native output remains in events.jsonl.
            event = {'type': 'item.' + state, 'at': now(), 'source': 'native_rgb_loop', 'item': item}
            with (harness.recorder.output / 'model_events.compat.jsonl').open('a') as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
            if item['type'] == 'agent_message':
                canonical_event('assistant_text', at=event['at'], content=item['text'])
            elif item['type'] == 'reasoning':
                canonical_event('reasoning_summary', at=event['at'], reasoning_summary=item['text'])
                summary = {'at': event['at'], 'text': item['text'], 'turn': turn,
                           'source': 'provider_returned_reasoning_summary', 'verbatim': True,
                           'translated': False, 'source_id': item['id']}
                with (harness.recorder.output / 'model_reasoning_summaries.jsonl').open('a') as stream:
                    stream.write(json.dumps(summary, ensure_ascii=False) + '\n')
            elif item['type'] == 'mcp_tool_call':
                details = {'call_id': item['id'], 'server': item['server'], 'tool': item['tool']}
                if state == 'started':
                    canonical_event('tool_call', at=event['at'], arguments=item['arguments'], **details)
                else:
                    canonical_event('tool_result', at=event['at'], result=item.get('result'), **details)
        harness.recorder.run['native_loop'] = {'model': self.model, **asdict(config),
                                               'image_retention_changes_raw_records': False}
        write_json(harness.recorder.output / 'run.json', harness.recorder.run)
        for turn in range(config.max_turns):
            if harness.closed:
                return
            if harness.deadline.expired:
                reason = 'episode_deadline_exceeded'
                break
            if tokens >= config.max_tokens:
                reason = 'model_token_budget_exhausted'
                break
            if not harness.deadline.managed and time.monotonic() - harness.started >= harness.budget.wall_seconds:
                reason = 'episode_deadline_exceeded'
                break
            outbound, captures, omitted = images.outbound(history)
            harness.recorder.event('model_request', {
                'turn': turn, 'model': self.model, 'history_messages': len(outbound),
                'input_captures': captures, 'historical_captures_omitted': omitted,
                'cumulative_tokens': tokens,
            })
            started = time.monotonic()
            try:
                response = self.request(harness, {
                    'model': self.model, 'instructions': self.instructions, 'input': outbound,
                    'tools': specs, 'parallel_tool_calls': False, 'store': False,
                    'max_output_tokens': min(config.max_output_tokens, config.max_tokens - tokens),
                })
            except SkillError as exc:
                if exc.code != 'episode_timeout':
                    raise
                reason = 'episode_deadline_exceeded'
                break
            output = response.get('output', [])
            usage = response.get('usage') or {}
            tokens += usage.get('total_tokens', 0)
            harness.recorder.event('model_response', {
                'turn': turn, 'model': self.model, 'output': public_model_output(output),
                'usage': usage, 'cumulative_tokens': tokens,
                'latency_seconds': time.monotonic() - started,
            })
            if usage:
                canonical_event('usage', usage=usage)
            for index, item in enumerate(public_model_output(output)):
                ident = item.get('id') or f'native-turn-{turn}-item-{index}'
                if item.get('type') == 'message':
                    for part_index, part in enumerate(item.get('content', [])):
                        if isinstance(part.get('text'), str):
                            replay_event('completed', {'id': f'{ident}-{part_index}',
                                         'type': 'agent_message', 'text': part['text']})
                elif item.get('type') == 'reasoning_summary':
                    for part_index, text in enumerate(item['summary']):
                        replay_event('completed', {'id': f'{ident}-{part_index}',
                                     'type': 'reasoning', 'text': text})
            history.extend(output)
            calls = [item for item in output if item.get('type') == 'function_call']
            if not calls:
                history.append({'role': 'user', 'content': 'Continue through RGB tools and formally call finish.'})
                continue
            for call in calls:
                call_id = call.get('call_id')
                if not isinstance(call_id, str) or not call_id:
                    raise RuntimeError('Model function call has no usable call_id')
                try:
                    args = json.loads(call['arguments'])
                except (ValueError, KeyError, TypeError):
                    result = {'ok': False, 'error': {'code': 'invalid_json'}}
                    args = call.get('arguments')
                    replay_event('started', {'id': call_id, 'type': 'mcp_tool_call',
                                 'server': 'manipulation', 'tool': call.get('name', ''), 'arguments': args})
                else:
                    replay_event('started', {'id': call_id, 'type': 'mcp_tool_call',
                                 'server': 'manipulation', 'tool': call.get('name', ''), 'arguments': args})
                    result = harness.call(call.get('name', ''), args, call_id)
                content = [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}]
                # References point to the exact bytes verified and attached by
                # ImageHistory. Replays do not duplicate base64 into their JSON.
                content.extend({'type': 'image', 'image_ref': f['image_ref'], 'mimeType': f['mime_type']}
                               for f in result.get('observation', {}).get('images', []))
                replay_event('completed', {'id': call_id, 'type': 'mcp_tool_call',
                             'server': 'manipulation', 'tool': call.get('name', ''), 'arguments': args,
                             'result': {'content': content}, 'status': 'completed'})
                history.append({'type': 'function_call_output', 'call_id': call_id,
                                'output': json.dumps(result, ensure_ascii=False)})
                images.append(history, result.get('observation', {}), harness.image_bytes)
                if harness.closed:
                    canonical_event('run_finished', content={'closed': True})
                    return
        canonical_event('run_finished', content={'reason': reason, 'origin': 'harness_budget'})
        harness.recorder.event('model_loop_stopped', {'reason': reason, 'cumulative_tokens': tokens})
        if not harness.closed:
            harness.call('finish', {'outcome': 'aborted', 'reason': reason}, 'native-loop-budget-stop')
