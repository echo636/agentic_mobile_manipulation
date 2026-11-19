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
