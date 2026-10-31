import copy
import importlib.util
from pathlib import Path
import pytest


def load_builder():
    path = Path(__file__).parents[1] / 'tasks/carry_in_and_put_away_groceries/v2_design/build_scene_overlay.py'
    spec = importlib.util.spec_from_file_location('grocery_overlay', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cargo_translation_preserves_relative_poses_and_other_objects():
    m = load_builder()
    states = {key: {'root_link': {'pos': [i, i * 2, 1], 'ori': [0, 0, 0, 1]}}
              for i, key in enumerate((*m.CARGO_KEYS, 'car'))}
    before = copy.deepcopy(states)
    m.translate_cargo(states, m.CARGO_KEYS, (.08, -.03))
    for key in m.CARGO_KEYS:
        assert states[key]['root_link']['pos'] == pytest.approx([
            before[key]['root_link']['pos'][0] + .08,
            before[key]['root_link']['pos'][1] - .03, 1])
        assert states[key]['root_link']['ori'] == before[key]['root_link']['ori']
    assert states['car'] == before['car']


@pytest.mark.parametrize('offset', [(float('nan'), 0), (float('inf'), 0), (.151, 0), (0,)])
def test_invalid_variant_rejected_before_output_creation(tmp_path, offset):
    m = load_builder()
    with pytest.raises(ValueError, match='cargo offset'):
        m.build(tmp_path / 'missing_dataset', tmp_path / 'output', cargo_offset_xy=offset)
    assert not (tmp_path / 'output').exists()
