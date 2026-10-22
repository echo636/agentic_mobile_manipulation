"""Finish native transition-object initialization before returning action RGB."""
import json

from ..contracts import SkillError


def finish_pending_objects(backend, remaining_steps):
    """Use normal counted control steps, never initialize or edit predicates.

    OG adds transition products at the end of a control step and initializes
    them on the next one. A target_changed exception can end an action between
    those phases. Returning then exposes objects whose relation states assert
    on the next grasp. Finish that lifecycle before the next observation.
    """
    sim = backend.og.sim
    completed = []
    for _ in range(min(8, max(0, remaining_steps))):
        pending = tuple(getattr(sim, '_objects_to_initialize', ()))
        if not pending:
            break
        backend.deadline.check(changed=True)
        before = backend.steps
        backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))
        completed.append({'objects': [obj.name for obj in pending],
                          'control_steps': backend.steps-before,
                          'initialized': all(obj.initialized for obj in pending
                                             if obj in backend.env.scene.objects)})
    if completed:
        backend.frames_revision = -1
        with (backend.output/'transition_lifecycle.jsonl').open('a') as stream:
            stream.write(json.dumps({'status':'native_initialization_steps',
                'env_step':backend.steps, 'steps':completed, 'physics_counted':True,
                'audience':'executor_private'})+'\n')
    if getattr(sim, '_objects_to_initialize', ()):
        raise SkillError('scene_initialization_pending',
            'New scene objects still need initialization; allow simulation to advance',
            changed=True, stage='transition_initialization')
