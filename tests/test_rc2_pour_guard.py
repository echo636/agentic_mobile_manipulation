"""rc2 B8 (2026-11-23): pour must reject side-opening / closed-top enclosures before release and never
silently succeed when rigid items poured into a container all land outside/on top (inside_objects == 0)."""
import sys
import types
from unittest.mock import Mock, patch

import pytest

from manipulation_agent.contracts import SkillError
from manipulation_agent.executors.material_transfer import (
    CheckedMaterialTransfer,
    NON_POURABLE_ENCLOSURE_CATEGORIES,
    is_non_pourable_enclosure,
)
from manipulation_agent.observations.boundary import factual_execution_error, public_execution_error


def _patch_og_states(monkeypatch):
    states = types.ModuleType('omnigibson.object_states')
    inside = type('Inside', (), {})
    on_top = type('OnTop', (), {})
    open_state = type('Open', (), {})
    contained = type('ContainedParticles', (), {})
    states.Inside = inside
    states.OnTop = on_top
    states.Open = open_state
    states.ContainedParticles = contained
    monkeypatch.setitem(sys.modules, 'omnigibson', types.ModuleType('omnigibson'))
    monkeypatch.setitem(sys.modules, 'omnigibson.object_states', states)
    return inside, on_top, open_state, contained


@pytest.mark.parametrize('category', [
    'cabinet', 'bottom_cabinet', 'top_cabinet', 'china_cabinet',
    'fridge', 'microwave', 'oven', 'washer', 'dryer', 'dishwasher', 'wardrobe',
])
def test_pour_rejects_closed_top_and_side_opening_enclosures_before_release(monkeypatch, category):
    _, _, open_state, contained = _patch_og_states(monkeypatch)
    source = types.SimpleNamespace(name='cdkr_tray', category='tray', states={})
    destination = types.SimpleNamespace(
        name=f'target_{category}',
        category=category,
        states={
            open_state: types.SimpleNamespace(get_value=lambda: True),
            contained: types.SimpleNamespace(link=object()),
        },
        aabb=([4.78, 3.88, -0.03], [5.64, 4.38, 1.71]),
    )
    backend = CheckedMaterialTransfer()
    backend._get_held = lambda: source
    backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[source, destination]))

    assert is_non_pourable_enclosure(destination) is True

    with pytest.raises(SkillError) as exc_info:
        backend._transfer_preconditions('pour', destination)
    assert exc_info.value.code == 'unsupported_relation'
    assert exc_info.value.changed is False

    # Boundary mapping must stay on existing mapped public/factual codes without leaking private errors
    pub = public_execution_error(exc_info.value)
    assert pub['code'] == 'unsupported_relation'
    assert pub['world_may_have_changed'] is False
    factual = factual_execution_error(exc_info.value, 'pour', 'holding_item')
    assert factual['code'] == 'no_visible_change'
    assert factual['world_may_have_changed'] is False

    # Direct call into _pour_rigid_objects is also blocked before moving any item to destination.aabb[1][2]
    moved_item = types.SimpleNamespace(name='cdkr_juice_1', set_position_orientation=Mock())
    with pytest.raises(SkillError) as rigid_exc:
        backend._pour_rigid_objects(source, destination, [moved_item], 10)
    assert rigid_exc.value.code == 'unsupported_relation'
    assert rigid_exc.value.changed is False
    moved_item.set_position_orientation.assert_not_called()


def test_pour_rejects_unknown_category_with_side_opening_door_and_allows_top_lid(monkeypatch):
    _, _, open_state, contained = _patch_og_states(monkeypatch)
    source = types.SimpleNamespace(name='tray', category='tray', states={})
    base_link = types.SimpleNamespace(aabb=([0.0, 0.0, 0.0], [0.6, 0.5, 1.7]))
    side_unit = types.SimpleNamespace(
        name='custom_storage_unit',
        category='custom_storage_unit',
        links={'base_link': base_link},
        root_link_name='base_link',
        aabb=([0.0, 0.0, 0.0], [0.6, 0.5, 1.7]),
        states={
            open_state: types.SimpleNamespace(get_value=lambda: True),
            contained: types.SimpleNamespace(link=object()),
        },
    )
    lidded_pot = types.SimpleNamespace(
        name='lidded_pot',
        category='stockpot',
        links={'base_link': types.SimpleNamespace(aabb=([0.0, 0.0, 0.8], [0.3, 0.3, 1.0]))},
        root_link_name='base_link',
        aabb=([0.0, 0.0, 0.8], [0.3, 0.3, 1.0]),
        states={
            open_state: types.SimpleNamespace(get_value=lambda: True),
            contained: types.SimpleNamespace(link=object()),
        },
    )
    backend = CheckedMaterialTransfer()
    backend._get_held = lambda: source
    backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[source, side_unit, lidded_pot]))

    with patch('manipulation_agent.executors.opening_stance.opening_info',
               return_value={'status': 'ok', 'normal': [0.0, -1.0]}):
        assert is_non_pourable_enclosure(side_unit) is True
        with pytest.raises(SkillError) as exc_info:
            backend._transfer_preconditions('pour', side_unit)
        assert exc_info.value.code == 'unsupported_relation'

    with patch('manipulation_agent.executors.opening_stance.opening_info',
               return_value={'status': 'no_side_opening', 'basis': 'lid_horizontal_hinge'}):
        assert is_non_pourable_enclosure(lidded_pot) is False
        assert backend._transfer_preconditions('pour', lidded_pot) == (source, lidded_pot)


@pytest.mark.parametrize('category', [
    'bowl', 'cup', 'mug', 'stockpot', 'frying_pan', 'sink', 'drop_in_sink',
    'kettle', 'pitcher', 'bucket', 'tray', 'plate', 'cutting_board', 'countertop',
])
def test_pour_allows_open_vessels_and_surfaces(monkeypatch, category):
    _, _, _, contained = _patch_og_states(monkeypatch)
    source = types.SimpleNamespace(name='kettle', category='kettle', states={})
    destination = types.SimpleNamespace(
        name=f'dest_{category}',
        category=category,
        states={contained: types.SimpleNamespace(link=object())} if category not in {'cutting_board', 'countertop'} else {},
    )
    backend = CheckedMaterialTransfer()
    backend._get_held = lambda: source
    backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(objects=[source, destination]))

    assert category not in NON_POURABLE_ENCLOSURE_CATEGORIES
    assert is_non_pourable_enclosure(destination) is False
    assert backend._transfer_preconditions('pour', destination) == (source, destination)


def test_pour_container_rigid_invariant_rejects_zero_inside_even_when_all_on_top(monkeypatch):
    inside, on_top, _, contained = _patch_og_states(monkeypatch)
    source = types.SimpleNamespace(name='cdkr_tray', category='tray', states={})
    container = types.SimpleNamespace(name='mystery_box', category='box', states={contained: object()})
    surface = types.SimpleNamespace(name='cutting_board', category='cutting_board', states={})

    def make_items(*, inside_flags, on_top_flags):
        items = []
        for idx, (ins, top) in enumerate(zip(inside_flags, on_top_flags)):
            items.append(types.SimpleNamespace(
                name=f'item_{idx}',
                states={
                    inside: types.SimpleNamespace(get_value=lambda _dest, v=ins: v),
                    on_top: types.SimpleNamespace(get_value=lambda _dest, v=top: v),
                },
            ))
        return items

    def run_pour(destination, items, *, materials=()):
        backend = CheckedMaterialTransfer()
        backend.env = types.SimpleNamespace(scene=types.SimpleNamespace(
            object_registry=lambda key, name: next((o for o in items if o.name == name), None)
        ))
        backend._transfer_preconditions = lambda *args: (source, destination)
        backend._pour_objects = lambda obj: items if obj is source else []
        backend._pour_rigid_objects = lambda *args: items
        backend._grasp_dependencies = lambda obj: []
        backend._placement_record = lambda row: None
        backend._transfer_material_portion = lambda *args, **kwargs: {
            'materials': list(materials),
            'source_has_more_material': False,
        }
        return backend._checked_transfer('pour', destination, 6)

    # 1) B8 invariant: 3 items poured into a container all land on top of its roof (inside_objects=0, on_top_objects=3)
    roof_items = make_items(inside_flags=[False, False, False], on_top_flags=[True, True, True])
    with pytest.raises(SkillError) as exc_info:
        run_pour(container, roof_items)
    assert exc_info.value.code == 'postcondition_error'
    assert exc_info.value.changed is True
    factual = factual_execution_error(exc_info.value, 'pour', 'holding_item')
    assert factual['code'] == 'result_unconfirmed'
    assert factual['world_may_have_changed'] is True

    # 2) Valid pour into a container: at least one rigid item enters Inside -> succeeds
    entered_items = make_items(inside_flags=[True, True, False], on_top_flags=[False, False, True])
    ok_res = run_pour(container, entered_items)
    assert ok_res['receiver_kind'] == 'container'
    assert ok_res['rigid_transfer']['inside_objects'] == 2
    assert ok_res['rigid_transfer']['on_top_objects'] == 1

    # 3) Valid pour onto an open surface (receiver_kind == 'surface'): on_top_objects=3 succeeds
    surf_res = run_pour(surface, roof_items)
    assert surf_res['receiver_kind'] == 'surface'
    assert surf_res['rigid_transfer']['on_top_objects'] == 3
    assert surf_res['rigid_transfer']['retained_objects'] == 3
