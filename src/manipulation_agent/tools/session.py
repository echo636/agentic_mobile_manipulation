from .base import register
from ..contracts import object_schema, STRING

@register("finish", "End the task. Use achieved when the requested results are complete, blocked when you cannot make further progress, or aborted when stopping without completion. Briefly describe the result or remaining work. Wait for closed=true before your final message.", object_schema({
    "outcome":{"type":"string","enum":["achieved","blocked","aborted"]},"reason":STRING}))
def finish(ctx, outcome, reason):
    return ctx.finish(outcome, reason)
