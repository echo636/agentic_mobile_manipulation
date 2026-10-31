"""Replay private planner evidence on CPU; never reconstructs simulator physics.

Exit 0: recorded navigation failure reproduced exactly.
Exit 1: planner succeeded or produced a different failure.
Missing visibility decisions are an error rather than assumed visibility.
"""
from __future__ import annotations
import argparse
import gzip
import json
from manipulation_agent.executors.gt_navigation import GridMap, NavigationError, plan_navigation


def replay(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:payload=json.load(stream)
    if payload['schema_version']!=1 or payload['audience']!='executor_private':
        raise ValueError('Unsupported private navigation snapshot')
    data=payload['grid']
    grid=GridMap(data['width'],data['height'],data['resolution'],tuple(data['origin']),bytes.fromhex(data['free_hex']))
    d=payload['diagnostics']
    decisions={(row,col):value for row,col,value in d.get('candidate_filter_results',[])}
    def candidate_filter(xy):
        cell=grid.cell(xy)
        if cell not in decisions:
            raise ValueError('Replay needs an unrecorded visibility decision; simulator re-evaluation required')
        return decisions[cell]
    options={k:d[k] for k in ['standoff','max_snap','horizon','max_expansions','max_target_distance']}
    observed=None
    try:
        plan_navigation(grid,d['current'],d['hint'],candidate_filter=candidate_filter if d['filter_present'] else None,**options)
    except NavigationError as error:
        observed={'code':error.code,'message':str(error)}
    return {'scope':'CPU planner replay; not autonomous success or physics replay',
            'expected':payload['error'],'observed':observed,'reproduced':observed==payload['error']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot')
    args=parser.parse_args()
    result=replay(args.snapshot)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['reproduced'] else 1


if __name__=='__main__':raise SystemExit(main())
