from .base import register
from ..contracts import object_schema, INT
from ..executors.primitives import PRIMITIVES

VISUAL_TARGET = object_schema({"image_ref": {"type":"string"}, "point": {
    "type":"array", "minItems":2, "maxItems":2, "items":{"type":"number","minimum":0,"maximum":1}}})
VISUAL_TARGET["type"] = ["object", "null"]

@register("act", "Execute one robot primitive on a point YOU select in the latest RGB. x: left=0/right=1, y: top=0/bottom=1. No object IDs or names accepted. release/wait use target=null. Tool completion is not task success; inspect returned RGB.", object_schema({
    "primitive":{"type":"string","enum":list(PRIMITIVES)}, "target":VISUAL_TARGET, "revision":INT}))
def act(ctx, primitive, target, revision):
    return ctx.perform(primitive, target, revision)
