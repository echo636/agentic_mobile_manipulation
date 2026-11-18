from .base import register
from ..contracts import object_schema, INT

REFERENCE_REVIEW_DESCRIPTION = (
    "Read the current RGB snapshot without moving or rendering. Call with {} to start. "
    "To compare a previous view with the current views, pass its published reference_image_ref. "
    "The historical view is for comparison. Only current view pixels can target actions."
)


@register("initialize", "Receive the prepared episode's current four RGB views. Call once before acting; each look or act returns the next observation. Repeated calls return the current snapshot without resetting the episode or taking another capture.", object_schema({}))
def initialize(ctx, reference_image_ref=None):
    return ctx.initialize(reference_image_ref)


@register("observe", "Receive fresh RGB camera images. No object list, labels, distance, depth or state truth. Old image refs expire.", object_schema({}))
def observe(ctx):
    return {"observation": ctx.refresh()}

@register("look", "Turn the robot in place to inspect another direction; positive yaw turns left. Returns fresh RGB. This uses the ideal motor executor.", object_schema({
    "yaw_degrees": {"type": "number", "minimum": -90, "maximum": 90}, "revision": INT}))
def look(ctx, yaw_degrees, revision):
    return ctx.perform("look", None, revision, yaw_degrees=yaw_degrees)
