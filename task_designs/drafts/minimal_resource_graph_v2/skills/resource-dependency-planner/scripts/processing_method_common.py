"""CLI for frozen finite processing methods; preserves a better supplied plan.

Dependencies are the unchanged modules from method_coverage_r2. Search entry
points are disabled: the CLI only expands methods and replays their actions.
"""
import argparse
import json
from pathlib import Path

import compile_mixed_resource_methods as methods
import explore_conserved_capacity as model_api

core = methods.base.core
FIELDS = ('count', 'capacity', 'travel_cost', 'tool_start', 'box_start', 'tool_goal', 'box_goal')
MAX_ACTIONS = 100
MAX_CANDIDATES = 188


def forbidden(*args, **kwargs):
    raise RuntimeError('Exact search is forbidden in the candidate tool')


for module in [core, model_api, model_api.affordance, model_api.affordance.cut_relaxation]:
    module.solve = forbidden


def validate(model):
    if not isinstance(model, dict) or set(model) != set(FIELDS):
        raise ValueError('Expected exactly the seven public task fields')
    if any(type(model[k]) is not int for k in FIELDS):
        raise ValueError('Parameters must be integers, not booleans')
    if model['count'] not in (1, 2) or not 1 <= model['capacity'] <= 7:
        raise ValueError('Supported scope: one or two raw items; capacity 1–7')
    if not 1 <= model['travel_cost'] <= 20:
        raise ValueError('Supported move cost: 1–20')
    if any(model[k] not in (0, 1) for k in FIELDS[3:]):
        raise ValueError('Resource start and goal locations must be 0 or 1')
    return core.Task(name='public_task', **model)


def initial(model):
    return core.initial(validate(model))


def choices(state, model):
    return model_api.choices(state, validate(model))


def goal(state, model):
    return core.goal(state, validate(model))


def replay(model, actions):
    task = validate(model)
    state, cost, history, error = core.initial(task), 0, [], None
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        error = {'reason': 'Expected at most 100 action strings'}
    else:
        for index, action in enumerate(actions, 1):
            available = model_api.choices(state, task)
            if not isinstance(action, str) or action not in available:
                error = {'step': index, 'action': action,
                         'reason': 'Action unknown or its public preconditions do not hold'}
                break
            before = state
            state, weight = available[action]
            cost += weight
            history.append(dict(step=index, action=action, before=list(before), after=list(state), cost=cost))
    complete = error is None and core.goal(state, task)
    return dict(valid=error is None, complete=complete, cost=cost if complete else None,
                executed_cost=cost, error=error, final_state=list(state), history=history)


def plan(model, proposal=None, *, method_compiler=methods.compile_candidates,
         candidate_limit=MAX_CANDIDATES):
    task = validate(model)
    compiled = method_compiler(task)
    assert not compiled['oracle_used'] and len(compiled['candidates']) <= candidate_limit
    candidates = []
    proposal_check = replay(model, proposal)
    # Earlier insertion wins cost ties, retaining an equally good existing plan.
    if proposal_check['complete']:
        candidates.append(dict(cost=proposal_check['cost'], actions=proposal, origin='model_proposal', method=None))
    for candidate in compiled['candidates']:
        if candidate['success']:
            checked = replay(model, candidate['actions'])
            assert checked['complete'] and checked['cost'] == candidate['cost']
            candidates.append(dict(cost=checked['cost'], actions=candidate['actions'],
                                   origin='frozen_method', method=candidate['method']))
    unique, seen = [], set()
    for candidate in sorted(candidates, key=lambda x: x['cost']):
        key = tuple(candidate['actions'])
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    if not unique:
        return dict(status='no_complete_candidate', candidate_count=len(compiled['candidates']))
    return dict(status='ok', best_cost=unique[0]['cost'], best_actions=unique[0]['actions'],
                best_origin=unique[0]['origin'], raw_method_best=compiled['selected_cost'],
                candidate_count=len(compiled['candidates']), unique_complete_candidates=len(unique),
                alternatives=unique[:3], proposal_preserved_if_better=True, exact_search_used=False,
                optimality_proven=False, scope='finite processing/transport/return methods with symbolic retention, pouring and unlimited surfaces')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        request = json.loads(a.input.read_text())
        if set(request) != {'public_model', 'proposal'}:
            raise ValueError('Expected public_model and proposal')
        result = plan(request['public_model'], request['proposal'])
        code = 0
    except (ValueError, TypeError, KeyError) as exc:
        result, code = dict(status='unsupported_input', error=str(exc)), 2
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
