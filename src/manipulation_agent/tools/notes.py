import json

from .base import register
from ..contracts import object_schema, SkillError
from ..agent.transport_planning import transport_advice


@register('task_notes', 'Store working beliefs, not environment truth. read returns notes and recent public receipts; write replaces all text. update merges goal and execution.operations fields by id and planning fields; omitted nodes persist, active clears. execution.remove_operations removes obsolete IDs. Nested state/processes and supplied lists otherwise replace their values. After comparison, select takes JSON plan_id, operation_id, steps, next_check and saves the chosen operation graph; legacy goal methods use method_id. Reconcile pending action feedback with update before selecting again. Use current expected_version for changes. Replies return metadata without echoing content. No physics or action step is consumed.', object_schema({
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
        if operation == 'select' or notes.get('automatic_selection'):
            content = json.loads(notes['content'])
            result['selected'] = content['active']
            if content.get('execution', {}).get('last_changes'):
                # Preserve the exact materialized graph in the event stream;
                # replay must not rerun a possibly newer graph optimizer.
                result['selected_execution'] = content['execution']
    advice = transport_advice(notes['content'])
    if advice is not None:
        result['transport_advice'] = advice
    return result
