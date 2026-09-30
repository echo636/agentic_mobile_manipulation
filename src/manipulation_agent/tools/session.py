from .base import register
from ..contracts import object_schema, STRING

@register("finish", "Formally close this episode with achieved/blocked/aborted and your visual evidence or limitation. Private independent evaluation is not returned to you.", object_schema({
    "outcome":{"type":"string","enum":["achieved","blocked","aborted"]},"reason":STRING}))
def finish(ctx, outcome, reason):
    return ctx.finish(outcome, reason)
