from .base import register
from ..contracts import object_schema, STRING, INT

@register("remember", "Store your visual observation, uncertainty or failure note in this episode. Cite the current revision; text is your belief, not ground truth.", object_schema({"key":STRING,"text":STRING,"revision":INT}))
def remember(ctx, key, text, revision):
    return ctx.remember(key, text, revision)

@register("recall", "Read your episode-local visual notes and plan. No simulator lookup is performed.", object_schema({}))
def recall(ctx):
    return ctx.recall()
