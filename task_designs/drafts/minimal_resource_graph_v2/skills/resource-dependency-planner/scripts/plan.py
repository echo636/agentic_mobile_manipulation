"""Public processing CLI with frozen R3 methods and last-use macro DAGs.

Experiment packaging names this file plan_processing_delivery.py and the common
validation/replay interface processing_method_common.py. No oracle is available.
"""
import argparse
import json
from pathlib import Path

import processing_method_common as common
import compile_resource_dependency_graph as methods

validate, initial, choices, goal, replay = common.validate, common.initial, common.choices, common.goal, common.replay
core, model_api = common.core, common.model_api
FIELDS, MAX_ACTIONS = common.FIELDS, common.MAX_ACTIONS
MAX_CANDIDATES = 1560


def plan(model, proposal=None):
    compiled = methods.compile_candidates(validate(model))
    result = common.plan(model, proposal, method_compiler=lambda _: compiled,
                         candidate_limit=MAX_CANDIDATES)
    result['scheduling'] = {k: compiled[k] for k in ('graph_count','graph_node_max','expanded_labels')}
    result['scope'] = 'Frozen R3 alternatives plus topological scheduling of declared macro graphs; not arbitrary atomic search or universal coverage'
    if result.get('best_origin') == 'frozen_method':
        match = next(c for c in compiled['candidates'] if c['success'] and c['actions'] == result['best_actions'])
        if 'graph' in match:
            result['best_macro_graph'] = match['graph']
            result['best_macro_order'] = match['order']
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    try:
        request=json.loads(a.input.read_text())
        if set(request)!={'public_model','proposal'}:
            raise ValueError('Expected public_model and proposal')
        result,code=plan(request['public_model'],request['proposal']),0
    except (ValueError,TypeError,KeyError) as exc:
        result,code=dict(status='unsupported_input',error=str(exc)),2
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return code


if __name__=='__main__': raise SystemExit(main())
