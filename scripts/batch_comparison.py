"""Dated, matched-task comparison; keep scoring separate from evidence checks."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import time

ARMS = ('original', 'motor', 'official')
LABELS = {'original': '原实现', 'motor': 'Motor · 代码控制', 'official': 'Official · 符号动作'}


def atomic(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def summarize_comparison(progress):
    task_sets = [{r['task'] for r in p['tasks']} for p in progress.values()]
    if any(tasks != task_sets[0] for tasks in task_sets):
        raise ValueError('Comparison arms must contain identical task sets')
    maps = {arm: {r['task']: r for r in p['tasks']} for arm, p in progress.items()}
    tasks = []
    for template in progress['original']['tasks']:
        rows = {arm: maps[arm][template['task']] for arm in ARMS}
        for key in ('instance', 'seed', 'instruction_sha256', 'bddl_sha256'):
            if len({r[key] for r in rows.values()}) != 1:
                raise ValueError('Unmatched task inputs: ' + template['task'] + ' / ' + key)
        tasks.append({'index': template['index'], 'task': template['task'], 'name': template['name'],
                      'instruction': template['instruction'], 'arms': rows})
    summaries = {}
    for arm in ARMS:
        rows = list(maps[arm].values())
        counts = dict(Counter(r['status'] for r in rows))
        summaries[arm] = {'total': len(rows), 'counts': counts,
            'ended': sum(counts.get(k, 0) for k in ('passed', 'failed', 'blocked')),
            'official_goal_successes': sum(r.get('task_success') is True for r in rows),
            'fully_validated_successes': counts.get('passed', 0),
            'scored': sum(r.get('task_success') is not None for r in rows),
            'unscored_ended': sum(r['status'] in ('passed', 'failed', 'blocked') and r.get('task_success') is None for r in rows),
            'infrastructure_retries': sum(len(r.get('previous_attempts', [])) for r in rows if r.get('retry_kind') == 'infrastructure'),
            'source': progress[arm].get('source'), 'updated_at': progress[arm].get('updated_at')}
    paired = [t for t in tasks if all(r['status'] in ('passed', 'failed', 'blocked') and r.get('task_success') is not None for r in t['arms'].values())]
    return {'updated_at': datetime.now(timezone.utc).isoformat(), 'arms': summaries, 'tasks': tasks,
            'paired_scored_tasks': len(paired),
            'paired_goal_successes': {arm: sum(t['arms'][arm]['task_success'] is True for t in paired) for arm in ARMS}}


def render_arm_dashboard(progress):
    esc = lambda x: html.escape(str(x))
    protocol = progress.get('protocol') or {}
    title = protocol.get('comparison_arm', protocol.get('agent_profile', 'original'))
    rows = []
    for r in progress['tasks']:
        link = '<a href="' + esc(r['replay_url']) + '">Replay ↗</a>' if r.get('replay_url') else 'Pending'
        rows.append('<tr><td>' + str(r['index'] + 1) + '</td><td>' + esc(r['name']) + '</td><td>' + esc(r['status']) + ' / ' + esc(r.get('stage', 'queued')) + '</td><td>' + esc(r.get('q_score', '—')) + '</td><td>' + link + '</td></tr>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="30"><title>Execution comparison</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:32px auto;padding:0 20px}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}a{color:#087866}</style><h1>' + esc(title) + '</h1><p><a href="../">Three-arm comparison</a></p><p>Updated ' + esc(progress['updated_at']) + '. Task success and complete evidence are separate fields.</p><p>' + esc(protocol.get('protocol', 'Four-camera RGB; see frozen manifest for execution protocol')) + '</p><table><tr><th>#</th><th>Task</th><th>Status</th><th>Q</th><th>Replay</th></tr>' + ''.join(rows) + '</table></html>'


PAGE = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>任务结果与回放</title><script>const q=new URLSearchParams(location.search);if(!q.has(\'batch\')&&!q.has(\'run\'))q.set(\'batch\',\'original100\');location.replace(\'/retest32_v7_20261001/replays.html?\'+q+location.hash)</script><a href="/retest32_v7_20261001/replays.html">打开任务结果与回放</a></html>'


def publish(root):
    config = json.loads((root / 'comparison_config.json').read_text())
    progress = {}
    for arm in ARMS:
        p = root / arm / 'progress.json'
        progress[arm] = json.loads(p.read_text()) if p.exists() else {'tasks': json.loads((root / arm / 'manifest.json').read_text())['tasks']}
    result = summarize_comparison(progress)
    atomic(root / 'comparison.json', result)
    report = Path(config['reports']); report.mkdir(parents=True, exist_ok=True)
    atomic(report / 'comparison.json', result)
    (report / 'index.html').write_text(PAGE)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--root', type=Path, required=True); p.add_argument('--watch', action='store_true')
    args = p.parse_args()
    while True:
        result = publish(args.root)
        if not args.watch or all(s['ended'] == s['total'] for s in result['arms'].values()):
            break
        time.sleep(10)
