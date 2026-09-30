from .base import register
from ..contracts import object_schema, INT

@register("observe", "Receive fresh RGB camera images. No object list, labels, distance, depth or state truth. Old image refs expire.", object_schema({}))
def observe(ctx):
    return {"observation": ctx.refresh()}

@register("look", "Turn the robot in place to inspect another direction; positive yaw turns left. Returns fresh RGB. This uses the ideal motor executor.", object_schema({
    "yaw_degrees": {"type": "number", "minimum": -90, "maximum": 90}, "revision": INT}))
def look(ctx, yaw_degrees, revision):
    return ctx.perform("look", None, revision, yaw_degrees=yaw_degrees)
