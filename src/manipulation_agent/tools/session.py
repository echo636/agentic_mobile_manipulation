from .base import register
from ..contracts import object_schema, STRING

@register("finish", "Formally close this episode with achieved/blocked/aborted and your visual evidence or limitation. For the long hot-drink task, blocked is accepted only after the action/call/step budget is exhausted or a nonrecoverable simulator error occurs; recoverable pixel or placement failures require continued attempts. Aborted is reserved for the supervisor. Achieved is accepted only when the task is complete, without returning goal details.", object_schema({
    "outcome":{"type":"string","enum":["achieved","blocked","aborted"]},"reason":STRING}))
def finish(ctx, outcome, reason):
    return ctx.finish(outcome, reason)
