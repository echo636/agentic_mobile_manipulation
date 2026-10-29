"""Check the water-to-cooked-water transition without running a robot policy.

This is a fixture/primitive diagnostic, not an experimental episode or a model
result. It directly supplies water and heat to the task's kettle, then checks
the standard OmniGibson states after simulation steps.
"""

import argparse
import json
import os
from pathlib import Path

from manipulation_agent.omnigibson_backend import OmniGibsonBackend


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--instance', type=int, default=301)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    if os.environ.get('OG_CLEAR_WATER_PARTICLES'):
        parser.error('OG_CLEAR_WATER_PARTICLES would invalidate the source water')
    if not os.environ.get('OG_SWITCH_TO_CPU_AFTER_WATER'):
        parser.error('Set OG_SWITCH_TO_CPU_AFTER_WATER=1 for this diagnostic')
    args.output.mkdir(parents=True, exist_ok=True)

    backend = OmniGibsonBackend('prepare_and_serve_hot_drink', args.instance,
                                args.output, seed=args.seed)
    try:
        from omnigibson.object_states import Contains, Filled, Heated

        scene = backend.env.scene
        kettle = backend.env.task.object_scope['kettle.n.01_1']
        water = scene.get_system('water', force_init=False)
        if Filled not in kettle.states or Heated not in kettle.states:
            raise RuntimeError('Kettle does not support filling and heating')
        if not kettle.states[Filled].set_value(water, True):
            raise RuntimeError('Could not fill kettle with ordinary water')
        if not kettle.states[Heated].set_value(True):
            raise RuntimeError('Could not heat kettle')
        for _ in range(20):
            backend._step(backend.robot.q_to_action(backend.robot.get_joint_positions()))

        cooked = scene.get_system('cooked__water', force_init=False)
        result = {
            'diagnostic_only': True,
            'cooked_initialized': bool(cooked.initialized),
            'cooked_particles': int(cooked.n_particles) if cooked.initialized else 0,
            'kettle_contains_cooked': bool(kettle.states[Contains].get_value(cooked))
            if cooked.initialized else False,
            'kettle_filled_cooked': bool(kettle.states[Filled].get_value(cooked))
            if cooked.initialized else False,
            'kettle_filled_water': bool(kettle.states[Filled].get_value(water)),
        }
        result['passed'] = (result['kettle_filled_cooked']
                            and not result['kettle_filled_water'])
        (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
        return 0 if result['passed'] else 1
    finally:
        backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
