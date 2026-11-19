"""v12 benchmark side: a declared fixed support is blocked in the navigation map like a task-added fixture."""
from __future__ import annotations

import json

from test_v11_fixed_supports import _backend, _obj


def test_declared_support_joins_navigation_fixtures(tmp_path):
    table = _obj('coffee_table_x_0')
    b = _backend(tmp_path, [table])
    b._native_map_added_fixtures = ['task_counter_0']
    b._native_fixture_map_cache = [('stale', 0, None)]
    b._apply_fixed_supports({'roles': {'table': 'coffee_table_x_0'}, 'fixed_supports': ['table']})
    assert b._native_map_added_fixtures == ['task_counter_0', 'coffee_table_x_0']
    assert b._native_fixture_map_cache is None
    assert json.loads((tmp_path / 'fixed_supports.json').read_text())['supports'][0]['navigation_map_blocked'] is True


def test_no_declaration_leaves_navigation_fixtures(tmp_path):
    b = _backend(tmp_path, [_obj('coffee_table_x_0')])
    b._native_map_added_fixtures = ['task_counter_0']
    b._apply_fixed_supports({'roles': {'table': 'coffee_table_x_0'}})
    assert b._native_map_added_fixtures == ['task_counter_0']
