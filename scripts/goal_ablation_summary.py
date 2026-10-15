"""Matched goal-skill comparison; a score is independent of planning and cleanup claims."""
import argparse
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from manipulation_agent.records import write_json

ARMS=('hierarchical','unguided')
MATCH=('task','instance','seed','instruction_sha256','bddl_sha256','custom_task_spec_sha256','custom_instance_sha256')


def summarize(progress):
    maps={arm:{r['index']:r for r in progress[arm]['tasks']} for arm in ARMS}
    if set(maps[ARMS[0]])!=set(maps[ARMS[1]]): raise ValueError('Unmatched task sets')
    tasks=[]
    for i in sorted(maps[ARMS[0]]):
        rows={a:maps[a][i] for a in ARMS}
        if any(rows['hierarchical'].get(k)!=rows['unguided'].get(k) for k in MATCH):
            raise ValueError('Unmatched input at index '+str(i))
        tasks.append({'index':i,'task':rows['hierarchical']['task'],
            'instruction_source':rows['hierarchical'].get('instruction_source'),
            'arms':{a:{k:r.get(k) for k in ('status','stage','task_success','q_score','custom_score','agent_steps','replay_url',
                'planning_metrics','termination_reason','controller_status','controller_cleanup','model_usage')} for a,r in rows.items()}})
    def stats(rows):
        return {'total':len(rows),'completed':sum(r.get('status') in {'passed','failed','blocked'} for r in rows),
            'scored':sum(type(r.get('task_success')) is bool for r in rows),
            'successes':sum(r.get('task_success') is True for r in rows),
            'unknown_score':sum(r.get('status') in {'passed','failed','blocked'} and r.get('task_success') is None for r in rows)}
    cohorts={'all':list(maps['hierarchical']),
        'detailed_first50':[i for i in maps['hierarchical'] if i<50],
        'short_phrase_last50':[i for i in maps['hierarchical'] if i>=50]}
    custom=any(r.get('custom_task_spec') for r in maps['hierarchical'].values())
    if custom:cohorts={'all':list(maps['hierarchical'])}
    paired=[t for t in tasks if all(type(t['arms'][a]['task_success']) is bool for a in ARMS)]
    return {'updated_at':datetime.now(timezone.utc).isoformat(),
        'custom_tasks':custom,'task_count':len(tasks),
        'title':'自定义长程任务 · 分层目标规划对照' if custom else '分层目标规划 · 配对对照实验',
        'max_agent_steps':progress['hierarchical'].get('protocol',progress['hierarchical']).get('max_agent_steps'),
        'cohorts':{c:{a:stats([maps[a][i] for i in ids]) for a in ARMS} for c,ids in cohorts.items()},
        'paired':{'scored':len(paired),'both_success':sum(all(t['arms'][a]['task_success'] for a in ARMS) for t in paired),
            'hierarchical_only':sum(t['arms']['hierarchical']['task_success'] and not t['arms']['unguided']['task_success'] for t in paired),
            'unguided_only':sum(t['arms']['unguided']['task_success'] and not t['arms']['hierarchical']['task_success'] for t in paired)},
        'tasks':tasks,'limitations':'One matched attempt per task. Missing scores remain unknown; tree done labels are model claims.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--batch-root',type=Path,required=True);p.add_argument('--reports',type=Path,required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
    a.reports.mkdir(parents=True,exist_ok=True)
    (a.reports/'index.html').write_text(Path(__file__).with_name('goal_ablation_dashboard.html').read_text())
    while True:
        progress={}
        for arm in ARMS:
            b=a.batch_root/arm;f=b/'progress.json'
            progress[arm]=json.loads((f if f.exists() else b/'manifest.json').read_text())
        result=summarize(progress);write_json(a.reports/'comparison.json',result);write_json(a.batch_root/'comparison.json',result)
        if not a.watch or all(x['completed']==x['total'] for x in result['cohorts']['all'].values()):break
        time.sleep(20)


if __name__=='__main__':main()
