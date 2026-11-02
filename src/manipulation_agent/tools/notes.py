import json

from .base import register
from ..contracts import object_schema, SkillError
from ..agent.transport_planning import transport_advice


@register('task_notes', 'Store your own working beliefs, not environment truth. read returns notes and recent public receipts. write replaces all text. update reconciles goal-tree-v2 JSON: merge changed goal fields by id (new IDs add nodes), merge planning fields, replace other supplied top-level fields; omitted branches persist and active defaults to null. Nested planning state/processes and lists replace whole values. write/update start a new receipt checkpoint. After comparison, select records JSON method_id, steps, next_check; it cannot reconcile pending feedback. Use the current expected_version for changes; replies return metadata without echoing content. No physics or action step is consumed.', object_schema({
    'operation': {'type': 'string', 'enum': ['read', 'write', 'update', 'select']},
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
        update = getattr(ctx.harness.task_notes, operation)
        notes = update(content, expected_version, request_id=ctx.request_id,
            revision=ctx.revision, agent_step=ctx.harness.agent_steps)
        result = {'notes': {key: value for key, value in notes.items() if key != 'content'}}
        if operation == 'select':
            result['selected'] = json.loads(notes['content'])['active']
    advice = transport_advice(notes['content'])
    if advice is not None:
        result['transport_advice'] = advice
    return result
