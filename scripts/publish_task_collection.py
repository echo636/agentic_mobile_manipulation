"""Publish experimental-arm task replays from an existing episode inventory."""
import argparse
import json
from pathlib import Path


def render_collection(episodes):
    groups = {}
    for episode in episodes:
        index = episode['index']
        group = groups.setdefault(index, {'index': index, 'task': episode['task'],
            'instruction': episode['instruction'], 'arms': {}})
        group['arms'][episode['arm']] = {key: episode.get(key) for key in
            ('task_success', 'q_score', 'agent_steps', 'replay_url')}
    data = {'tasks': [groups[index] for index in sorted(groups)]}
    template = Path(__file__).with_name('task_collection.html').read_text()
    return template.replace('@@DATA@@', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    episodes = [episode for episode in json.loads(args.input.read_text())
                if episode['arm'] == 'hierarchical']
    page = render_collection(episodes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.html.tmp')
    temporary.write_text(page)
    temporary.replace(args.output)
    print(json.dumps({'status': 'passed', 'arm': 'hierarchical',
                      'tasks': len({r['index'] for r in episodes}),
                      'episodes': len(episodes), 'output': str(args.output),
                      'validation_level': 'presentation generation; browser checks separate'}))


if __name__ == '__main__':
    main()
