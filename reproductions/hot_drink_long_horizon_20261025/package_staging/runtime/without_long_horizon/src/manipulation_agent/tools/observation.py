from .base import register
from ..contracts import object_schema, INT

@register("initialize", "Receive the prepared episode's current four RGB views. Call once before acting; each look or act returns the next observation. Repeated calls return the current snapshot without resetting the episode or taking another capture.", object_schema({}))
def initialize(ctx):
    return ctx.initialize()

@register("observe", "Receive fresh RGB camera images. No object list, labels, distance, depth or state truth. Old image refs expire.", object_schema({}))
def observe(ctx):
    return {"observation": ctx.refresh()}

@register("look", "Turn the robot in place to inspect another direction; positive yaw turns left. Returns fresh RGB. This uses the ideal motor executor.", object_schema({
    "yaw_degrees": {"type": "number", "minimum": -90, "maximum": 90}, "revision": INT}))
def look(ctx, yaw_degrees, revision):
    return ctx.perform("look", None, revision, yaw_degrees=yaw_degrees)
