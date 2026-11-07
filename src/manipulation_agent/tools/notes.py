import json

from .base import register
from ..contracts import object_schema, SkillError
from ..agent.transport_planning import transport_advice


@register('task_notes', 'Store concise working beliefs in the format taught by your planning skill. read returns notes and recent action receipts; write replaces the document. update merges goals and execution.operators by id; execution.remove_operators removes obsolete capabilities. Supplied planning.state, processes and lists replace their values; omitted fields persist, except active. In graph-optimization mode, write/update generates the operation graph and selects its next work. select overrides that choice using plan_id, operation_id, steps and next_check after progress is reconciled. Use the current expected_version for changes. Writing notes does not execute robot actions.', object_schema({
    'operation': {'type': 'string', 'enum': ['read', 'write', 'update', 'select']},
    'content': {'type': ['string', 'null'], 'maxLength': 12000},
    'expected_version': {'type': ['integer', 'null'], 'minimum': 0},
}))
def task_notes(ctx, operation, content, expected_version):
    if operation == 'select' and ctx.harness.goal_skill != 'hierarchical':
        raise SkillError('planning_disabled',
            'Program planning is disabled in this episode. Record your own choice with write or update.')
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
    advice = transport_advice(notes['content']) if ctx.harness.goal_skill == 'hierarchical' else None
    if advice is not None:
        result['transport_advice'] = advice
    return result
