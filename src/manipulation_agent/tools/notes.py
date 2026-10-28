from .base import register
from ..contracts import object_schema, SkillError


@register('task_notes', 'Read or replace your episode-local working notes. Content is your own text, not environment truth. Reads return the full notes and recent public action receipts since the last write, with an omitted count if the window overflowed. Writes use the current version, return metadata without echoing content, and start a new receipt checkpoint. No physics or action step is consumed.', object_schema({
    'operation': {'type': 'string', 'enum': ['read', 'write']},
    'content': {'type': ['string', 'null'], 'maxLength': 12000},
    'expected_version': {'type': ['integer', 'null'], 'minimum': 0},
}))
def task_notes(ctx, operation, content, expected_version):
    if operation == 'read':
        if content is not None or expected_version is not None:
            raise SkillError('invalid_arguments', 'For read, use content=null and expected_version=null.')
        return {'notes': ctx.harness.task_notes.read()}
    notes = ctx.harness.task_notes.write(content, expected_version, request_id=ctx.request_id,
        revision=ctx.revision, agent_step=ctx.harness.agent_steps)
    return {'notes': {key: value for key, value in notes.items() if key != 'content'}}
