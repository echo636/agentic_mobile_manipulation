import json

from .base import register
from ..contracts import object_schema, SkillError
from ..agent.transport_planning import transport_advice


@register('task_notes', 'Read or replace your episode-local working notes. Content is your own text, not environment truth. Reads include recent public action receipts. Write reconciles beliefs and starts a new receipt checkpoint. After a planning comparison, select records only the chosen method: content is JSON with method_id, steps, next_check. Use the current expected_version for write/select; replies return the new version without echoing content. Select cannot replace reconciliation of pending action feedback. No physics or action step is consumed.', object_schema({
    'operation': {'type': 'string', 'enum': ['read', 'write', 'select']},
    'content': {'type': ['string', 'null'], 'maxLength': 12000},
    'expected_version': {'type': ['integer', 'null'], 'minimum': 0},
}))
def task_notes(ctx, operation, content, expected_version):
    if operation == 'read':
        if content is not None or expected_version is not None:
            raise SkillError('invalid_arguments', 'For read, use content=null and expected_version=null.')
        notes = ctx.harness.task_notes.read()
        result = {'notes': notes}
    else:
        update = ctx.harness.task_notes.select if operation == 'select' else ctx.harness.task_notes.write
        notes = update(content, expected_version, request_id=ctx.request_id,
            revision=ctx.revision, agent_step=ctx.harness.agent_steps)
        result = {'notes': {key: value for key, value in notes.items() if key != 'content'}}
        if operation == 'select':
            result['selected'] = json.loads(notes['content'])['active']
    advice = transport_advice(notes['content'])
    if advice is not None:
        result['transport_advice'] = advice
    return result
