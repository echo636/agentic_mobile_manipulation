"""Curate persistent task/action skills after trials; this never launches simulation."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from manipulation_agent.experience_skills import ExperienceStore, configured_root
from manipulation_agent.records import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=configured_root())
    sub = p.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list'); listing.add_argument('--output', type=Path)
    publish = sub.add_parser('publish'); publish.add_argument('--proposal', type=Path, required=True)
    archive = sub.add_parser('archive'); archive.add_argument('--run', type=Path, required=True)
    for name in ('show', 'rollback'):
        command = sub.add_parser(name)
        command.add_argument('--kind', choices=['task','action'], required=True)
        command.add_argument('--key', required=True)
        command.add_argument('--protocol', choices=['rgb_only','oracle_waypoints'], required=True)
        if name == 'rollback':
            command.add_argument('--revision', type=int, required=True)
            command.add_argument('--expected-revision', type=int, required=True)
    args = p.parse_args()
    if args.root is None: p.error('Supply --root or MAS_EXPERIENCE_ROOT; no home-directory default')
    store = ExperienceStore(args.root)
    try:
        if args.command == 'publish':
            proposal = json.loads(args.proposal.read_text())
            result = store.publish(**proposal)
        elif args.command == 'archive':
            result = {'candidate':str(store.archive(args.run)), 'automatically_promoted':False}
        elif args.command == 'show':
            entry = store.current(args.kind,args.key,args.protocol)
            if entry is None: p.error('No reviewed experience for this task/action and protocol')
            print((entry['directory']/'SKILL.md').read_text()); return
        elif args.command == 'rollback':
            result = store.activate(args.kind,args.key,args.protocol,revision=args.revision,
                                    expected_revision=args.expected_revision)
        else:
            tasks=json.loads((Path(__file__).resolve().parents[1]/'src/manipulation_agent/tasks.json').read_text())['tasks']
            result={'tasks':store.inventory(tasks), 'actions':{
                protocol:[{k:e[k] for k in ['name','key','revision']} for e in store.select(None,protocol)]
                for protocol in ['rgb_only','oracle_waypoints']}}
            if args.output:
                write_json(args.output,result)
                print(json.dumps({'output':str(args.output),'tasks':len(result['tasks']),
                    'task_skills':sum(v is not None for t in result['tasks'] for v in t['protocols'].values()),
                    'action_skills':sum(len(v) for v in result['actions'].values())})); return
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError) as exc:
        p.error(str(exc))

if __name__ == '__main__': main()
