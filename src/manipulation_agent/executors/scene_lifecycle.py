"""Finish native transition-object initialization before returning action RGB."""
import json

from ..contracts import SkillError
from ..records import now


def finish_pending_objects(backend, remaining_steps):
    """Use normal counted control steps, never initialize or edit predicates.

    OG adds transition products at the end of a control step and initializes
    them on the next one. A target_changed exception can end an action between
    those phases. Returning then exposes objects whose relation states assert
    on the next grasp. Finish that lifecycle before the next observation.
    """
    sim = backend.og.sim
    for _ in range(min(8, max(0, remaining_steps))):
        pending = tuple(getattr(sim, '_objects_to_initialize', ()))
        if not pending:
            break
        backend.deadline.check(changed=True)
        before = backend.steps
        status = 'failed'
        try:
            backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
            status = 'passed'
        finally:
            backend.frames_revision = -1
            with (backend.output/'transition_initialization.jsonl').open('a') as stream:
                stream.write(json.dumps({'at':now(), 'status':status,
                    'start_step':before, 'end_step':backend.steps,
                    'objects':[{'name':obj.name, 'initialized':bool(obj.initialized)} for obj in pending],
                    'physics_counted':True, 'audience':'executor_private'})+'\n')
    if getattr(sim, '_objects_to_initialize', ()):
        raise SkillError('scene_initialization_pending',
            'New scene objects still need initialization; allow simulation to advance',
            changed=True, stage='transition_initialization')
