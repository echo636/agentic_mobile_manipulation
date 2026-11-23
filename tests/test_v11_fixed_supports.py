"""v11 benchmark side: task-declared fixed supports cannot be grasped and get a large mass (no simulator needed)."""
from __future__ import annotations

import json
import types

import pytest

from manipulation_agent.executors.custom_task import CustomTaskBackend
from manipulation_agent.executors.omnigibson_rgb import RGBBackend


class _Link:
    def __init__(self, mass):
        self.mass = mass


def _obj(name, fixed=False):
    return types.SimpleNamespace(name=name, fixed_base=fixed, states={}, links={'base_link': _Link(12.0)})


def _backend(tmp_path, objects, held=None):
    b = object.__new__(CustomTaskBackend)
    b.custom_objects = {o.name: o for o in objects}
    b.output = tmp_path
    b._get_held = lambda: held
    b.robot = object()
    return b


def test_declared_support_is_heavy_and_refuses_grasp(tmp_path):
    table, cup = _obj('coffee_table_x_0'), _obj('cup_0')
    b = _backend(tmp_path, [table, cup])
    b._apply_fixed_supports({'roles': {'table': 'coffee_table_x_0', 'cup': 'cup_0'}, 'fixed_supports': ['table']})
    assert table.links['base_link'].mass == CustomTaskBackend.FIXED_SUPPORT_MASS_KG
    assert cup.links['base_link'].mass == 12.0
    assert b._fixed_for_grasp(table) and not b._fixed_for_grasp(cup)
    assert not b._snap_suits('grasp', table) and b._snap_suits('grasp', cup)
    with pytest.raises(Exception) as err:
        b._manipulation_preconditions('grasp', table)
    assert getattr(err.value, 'code', None) == 'fixed_object'
    rec = json.loads((tmp_path / 'fixed_supports.json').read_text())
    assert rec['supports'][0]['links']['base_link'] == [12.0, 1000.0]


def test_no_declaration_keeps_behaviour(tmp_path):
    table = _obj('coffee_table_x_0')
    b = _backend(tmp_path, [table])
    b._apply_fixed_supports({'roles': {'table': 'coffee_table_x_0'}})
    assert b.fixed_support_names == frozenset() and not b._fixed_for_grasp(table)
    assert table.links['base_link'].mass == 12.0 and not (tmp_path / 'fixed_supports.json').exists()
    assert RGBBackend._fixed_for_grasp(b, _obj('counter', fixed=True))


def test_unknown_support_role_fails_loudly(tmp_path):
    b = _backend(tmp_path, [])
    with pytest.raises(KeyError):
        b._apply_fixed_supports({'roles': {}, 'fixed_supports': ['table']})


def _box_obj(name, category, size, *, fixed=False):
    hx, hy, hz = size
    return types.SimpleNamespace(
        name=name,
        category=category,
        fixed_base=fixed,
        states={},
        links={'base_link': _Link(12.0)},
        aabb=([0.0, 0.0, 0.0], [hx, hy, hz]),
    )


def test_global_furniture_categories_and_size_cap_block_grasp_without_leak(tmp_path):
    """B3 (rc2 P0): tables, chairs, sofas, cabinets, appliances, and bulky atypical fixtures
    are blocked even when fixed_base=False and omitted from fixed_supports, returning public
    `nothing_lifted` without leaking private categories."""
    from manipulation_agent.observations.boundary import factual_execution_error

    ungraspable = [
        _box_obj('breakfast_table_rhjoby_0', 'breakfast_table', (3.10, 2.43, 0.81)),
        _box_obj('straight_chair_jgoolz_5', 'straight_chair', (0.50, 0.50, 0.90)),
        _box_obj('sofa_abc_0', 'sofa', (2.10, 0.95, 0.85)),
        _box_obj('bottom_cabinet_fancyy_0', 'bottom_cabinet', (0.80, 0.60, 1.80)),
        _box_obj('wardrobe_x_0', 'wardrobe', (1.10, 0.60, 2.00)),
        _box_obj('microwave_abzvij_0', 'microwave', (0.55, 0.42, 0.34)),
        _box_obj('custom_island_0', 'unlisted_fixture', (1.40, 0.60, 0.90)),     # max_dim > 1.15 & mid > 0.35
        _box_obj('custom_square_table_0', 'unlisted_fixture', (0.90, 0.85, 0.75)),  # area > 0.70 & hz > 0.35
    ]
    b = _backend(tmp_path, ungraspable)
    b.fixed_support_names = frozenset()

    for obj in ungraspable:
        assert b._fixed_for_grasp(obj) is True, obj.name
        assert b._snap_suits('grasp', obj) is False, obj.name
        with pytest.raises(Exception) as err:
            b._manipulation_preconditions('grasp', obj)
        assert getattr(err.value, 'code', None) == 'fixed_object'
        receipt = factual_execution_error(err.value, 'grasp', 'empty')
        assert receipt['code'] == 'nothing_lifted'
        assert obj.category not in receipt['message']
        assert obj.name not in receipt['message']


def test_legitimate_graspable_items_pass_global_guard(tmp_path):
    """B3 (rc2 P0): large trays, vacuums, slender brooms/mops, lamps, cookware, and tableware
    must all remain graspable."""
    graspable = [
        _box_obj('mhhoga_tray_15', 'tray', (0.69, 0.47, 0.034)),
        _box_obj('usbe_vacuum_amnmmq', 'vacuum', (0.34, 0.48, 0.42)),
        _box_obj('vacuum_bdmsbr', 'vacuum', (0.19, 0.30, 1.01)),
        _box_obj('broom_0', 'broom', (0.25, 0.06, 1.35)),
        _box_obj('mop_0', 'mop', (0.22, 0.08, 1.30)),
        _box_obj('table_lamp_uupwiq_0', 'table_lamp', (0.28, 0.28, 0.55)),
        _box_obj('c1_pan', 'griddle_pan', (0.45, 0.30, 0.08)),
        _box_obj('casserole_0', 'casserole', (0.32, 0.26, 0.16)),
        _box_obj('hl2_plate1', 'plate', (0.26, 0.26, 0.03)),
        _box_obj('hl2_fork1', 'fork', (0.18, 0.04, 0.02)),
        _box_obj('chopping_board_211', 'chopping_board', (0.42, 0.28, 0.03)),
    ]
    b = _backend(tmp_path, graspable)
    b.fixed_support_names = frozenset()

    for obj in graspable:
        assert b._fixed_for_grasp(obj) is False, obj.name
        assert b._snap_suits('grasp', obj) is True, obj.name
