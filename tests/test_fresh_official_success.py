"""Final scoring must use BEHAVIOR's current predicate, not a cached env-step flag."""
from types import SimpleNamespace

from manipulation_agent.omnigibson_backend import OmniGibsonBackend


def test_fresh_official_goal_after_internal_physics_ticks():
    calls = []
    predicate = SimpleNamespace(_check_goal_fn=lambda: (calls.append('official') or True, {}))
    task = SimpleNamespace(success=False, _termination_conditions={'predicate': predicate})
    scene = object()
    backend = SimpleNamespace(
        env=SimpleNamespace(task=task, scene=scene),
        _goal_options=lambda: [[True, True]], initial_goals=[[False, False]],
        task_metric=SimpleNamespace(state={scene: {}}, _compute_episode_metrics=lambda env, state: {
            'q_score': {'final': 1.0}, 'time': {'normalized_time': 1.0}}),
        steps=1, navigation_distance=0.0, inside_placement='official_volume',
        sampling_physics_steps=0, task_metadata={},
    )
    result = OmniGibsonBackend.evaluate(backend)
    assert calls == ['official']
    assert result['task_success'] is True
    assert result['official_task_success'] is True
    assert result['last_env_step_task_success'] is False
