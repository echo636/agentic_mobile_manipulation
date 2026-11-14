from copy import deepcopy
from types import SimpleNamespace
import pytest
from manipulation_agent.tasks.lineage_goals import (
    check_initial_lineages,evaluate_relational_goals,validate_relational_goals,
)


def spec():
    return dict(kind='custom_bddl_sampled_instance',lineage_containment_goals=[
        dict(parent=parent,destination=box,children=[dict(name=f'half_{parent}_{i}',category='half_log',model='half') for i in range(2)])
        for parent,box in [('raw_a','box_a'),('raw_b','box_b')]],
        return_region_goals=[dict(object='box_a',center_xy=[0,0],radius_m=.3)])


def objects(initial=False):
    result={n:SimpleNamespace(name=n,category='carton',model='box',get_position_orientation=lambda:([0,0,0],[0,0,0,1])) for n in ['box_a','box_b']}
    for parent in ['raw_a','raw_b']:
        if initial:
            result[parent]=SimpleNamespace(metadata=dict(object_parts={str(i):dict(category='half_log',model='half') for i in range(2)}))
        else:
            for i in range(2):
                name=f'half_{parent}_{i}';result[name]=SimpleNamespace(name=name,category='half_log',model='half')
    return result


def test_parent_identity_not_cut_order_and_missing_outputs_fail():
    world=objects()
    inside=lambda obj,box: ('raw_a' in obj.name)==(box.name=='box_a')
    assert all(evaluate_relational_goals(spec(),world.get,inside).values())
    swapped=lambda obj,box: not inside(obj,box)
    assert not all(evaluate_relational_goals(spec(),world.get,swapped).values())
    del world['half_raw_a_0']
    assert not all(evaluate_relational_goals(spec(),world.get,inside).values())


def test_initial_metadata_is_checked_without_modification():
    world=objects(initial=True)
    check_initial_lineages(spec(),world.get)
    bad=spec();bad['lineage_containment_goals'][0]['children'][0]['model']='wrong'
    with pytest.raises(ValueError):check_initial_lineages(bad,world.get)
    world['half_raw_a_0']=object()
    with pytest.raises(ValueError):check_initial_lineages(spec(),world.get)


def test_malformed_regions_and_shared_output_names_rejected():
    for value in [float('nan'),-1,True]:
        bad=spec();bad['return_region_goals'][0]['radius_m']=value
        with pytest.raises(ValueError):validate_relational_goals(bad)
    bad=spec();bad['lineage_containment_goals'][1]['children'][0]['name']='half_raw_a_0'
    with pytest.raises(ValueError):validate_relational_goals(bad)


def test_return_position_and_wrong_output_assets_fail():
    world=objects()
    world['box_a'].get_position_orientation=lambda:([1,0,0],[0,0,0,1])
    world['half_raw_a_0'].model='wrong'
    checks=evaluate_relational_goals(spec(),world.get,lambda obj,box:True)
    assert not checks['box_a:return_region']
    assert not checks['half_raw_a_0:correct_order_inside']


def test_no_relational_goals_preserves_existing_specs():
    check_initial_lineages({},lambda name:None)
    assert evaluate_relational_goals({},lambda name:None,lambda a,b:False)=={}
    with pytest.raises(ValueError):validate_relational_goals({**spec(),'kind':'behavior1k_sampled_instance'})
