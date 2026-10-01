"""Opt-in motor profile; never added to the semantic workflow catalog."""
from .base import register
from ..contracts import object_schema, INT


@register('describe_controls', 'Read the static motor interface, units, joint order and limits. No current robot or scene state.', object_schema({}))
def describe_controls(ctx):
    return {'controls':ctx.backend.motor_contract()}


@register('execute_code', 'Execute a bounded Python run() composing base_velocity, joint_delta, gripper, hold and observe. Read describe_controls first. Every motor command consumes the action budget; fresh four-camera RGB is returned after the program, also on failure. No imports, attributes, filesystem, simulator objects or semantic act tools.', object_schema({
    'program':{'type':'string','minLength':1,'maxLength':12000}, 'revision':INT}))
def execute_code(ctx, program, revision):
    return ctx.execute_motor_program(program, revision)
