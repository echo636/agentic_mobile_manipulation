from .base import register
from ..contracts import object_schema, STRING, NULL_STRING

@register("update_plan", "Replace your revisable task plan. Use visual hypotheses; done claims need a successful act/look evidence ID. Plan claims are not simulator truth.", object_schema({
    "reason":STRING,"subgoals":{"type":"array","maxItems":40,"items":object_schema({
        "id":STRING,"description":STRING,"dependencies":{"type":"array","items":STRING},
        "status":{"type":"string","enum":["pending","active","done","blocked"]},"evidence":NULL_STRING})}}))
def update_plan(ctx, reason, subgoals):
    return ctx.update_plan(reason, subgoals)
