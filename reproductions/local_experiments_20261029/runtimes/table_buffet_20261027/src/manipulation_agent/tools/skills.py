from .base import register
from ..contracts import object_schema, STRING

@register("list_skills", "List the frozen agent workflow skill documents available in this run.", object_schema({}))
def list_skills(ctx):
    return {"skills":ctx.skills.catalog(),"bundle_sha256":ctx.skills.digest}

@register("read_skill", "Read one allowed workflow skill resource from this episode's immutable skill snapshot. resource is SKILL.md or a listed reference. This is not an arbitrary file reader.", object_schema({"name":STRING,"resource":STRING}))
def read_skill(ctx, name, resource):
    return ctx.skills.read(name, resource)
