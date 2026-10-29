"""Extract accepted whole-plan replacements for offline human review.

An ID disappearing is a review candidate, not proof of lost work: nodes can be
renamed, merged or resolved in the update reason. Never feed this private audit
back into an ongoing episode or use it as a task-success evaluator.
"""
import argparse
import json
from pathlib import Path


def audit(events):
    results = {e.get('request_id'): e for e in events if e.get('kind') == 'tool_result' and e.get('name') == 'update_plan'}
    previous = {}
    transitions = []
    for event in events:
        if event.get('kind') != 'tool_call' or event.get('name') != 'update_plan':
            continue
        reply = results.get(event.get('request_id'), {}).get('result', {})
        if reply.get('ok') is not True:
            continue
        args = event.get('arguments', {})
        current = {node['id']: node for node in args.get('subgoals', [])}
        missing = [node for key, node in previous.items() if node.get('status') in ('pending', 'active') and key not in current]
        done = [{'before': previous[key], 'after': node} for key, node in current.items() if key in previous and previous[key].get('status') in ('pending', 'active') and node.get('status') == 'done']
        transitions.append({'event': event['id'], 'at': event.get('at'), 'reason': args.get('reason'), 'decision': args.get('decision'), 'unresolved_ids_removed': missing, 'added_nodes': [node for key,node in current.items() if key not in previous], 'newly_done': done, 'rewritten_unresolved_nodes': [{'before': previous[key], 'after': node} for key,node in current.items() if key in previous and previous[key].get('status') in ('pending', 'active') and previous[key].get('description') != node.get('description')], 'current_nodes': list(current.values())})
        previous = current
    return {'scope': 'Accepted plan transitions only; ID-change candidates require semantic and action-evidence review. Not a task score.', 'accepted_updates': len(transitions), 'removal_candidate_updates': sum(bool(t['unresolved_ids_removed']) for t in transitions), 'transitions': transitions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    events = [json.loads(line) for line in args.events.read_text().splitlines() if line.strip()]
    report = audit(events)
    with args.output.open('x') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({key: report[key] for key in ('accepted_updates','removal_candidate_updates')}))


if __name__ == '__main__':
    main()
