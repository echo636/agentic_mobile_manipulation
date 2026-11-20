from .base import register
from ..contracts import object_schema

JOB_ID = {'type': 'string', 'minLength': 1, 'maxLength': 64}


@register('start_observation', 'Queue an asynchronous RGB observation from four fixed cameras: front/back/left/right. Returns a job ID immediately. No robot turn or physics step. Other tools remain available while capture is pending.', object_schema({}))
def start(ctx):
    return ctx.surround.start()


@register('get_observation', 'Get an observation job without waiting. A passed job returns four actual RGB images from one simulation state, with a common capture timestamp. Check job.stale: only current image refs are valid act targets. Use a new start_observation when stale.', object_schema({'job_id': JOB_ID}))
def get(ctx, job_id):
    return ctx.surround.get(job_id)


@register('cancel_observation', 'Request cancellation of a pending read-only observation job. Query until terminal; already completed observations remain unchanged. Cancellation never moves the robot.', object_schema({'job_id': JOB_ID}))
def cancel(ctx, job_id):
    return ctx.surround.cancel(job_id)
