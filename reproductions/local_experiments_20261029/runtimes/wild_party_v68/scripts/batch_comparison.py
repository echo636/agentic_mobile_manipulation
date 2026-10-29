"""Dated, matched-task comparison; keep scoring separate from evidence checks."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import time
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def outcome(row):
    """Keep timeout/failure terminal even when native shutdown lost its score."""
    from manipulation_agent.batch_lifecycle import classify_episode_outcome
    return classify_episode_outcome(row)

ARMS = ('original', 'motor', 'official')
LABELS = {'original': '原实现', 'motor': 'Motor · 代码控制', 'official': 'Official · 符号动作'}


def atomic(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def summarize_comparison(progress):
    arms=tuple(arm for arm in ARMS if arm in progress)
    if 'original' not in arms or len(arms)<2 or set(progress)-set(ARMS):
        raise ValueError('Comparison requires original and at least one known paired arm')
    task_sets = [{r['task'] for r in p['tasks']} for p in progress.values()]
    if any(tasks != task_sets[0] for tasks in task_sets):
        raise ValueError('Comparison arms must contain identical task sets')
    # Emit the same canonical classification used by aggregate counts. Rendering
    # must not independently reinterpret timeouts, finish latches or media errors.
    maps = {arm: {r['task']: {**r, 'episode_outcome': outcome(r)} for r in p['tasks']}
            for arm, p in progress.items()}
    tasks = []
    for template in progress['original']['tasks']:
        rows = {arm: maps[arm][template['task']] for arm in arms}
        for key in ('instance', 'seed', 'instruction_sha256', 'bddl_sha256'):
            if len({r[key] for r in rows.values()}) != 1:
                raise ValueError('Unmatched task inputs: ' + template['task'] + ' / ' + key)
        tasks.append({'index': template['index'], 'task': template['task'], 'name': template['name'],
                      'instruction': template['instruction'], 'arms': rows})
    summaries = {}
    for arm in arms:
        rows = list(maps[arm].values())
        counts = dict(Counter(r['status'] for r in rows))
        outcomes = dict(Counter(outcome(r) for r in rows if outcome(r) is not None))
        summaries[arm] = {'total': len(rows), 'counts': counts,
            'outcome_counts': outcomes,
            'worker_states': progress[arm].get('worker_states', {}),
            'active_executions': sum(r['status']=='running' and r.get('stage') not in
                {'waiting_for_resources','waiting_for_lease','archiving','finalizing'} for r in rows),
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
            'paired_goal_successes': {arm: sum(t['arms'][arm]['task_success'] is True for t in paired) for arm in arms}}


def render_arm_dashboard(progress):
    esc = lambda x: html.escape(str(x))
    protocol = progress.get('protocol') or {}
    standalone = protocol.get('standalone_batch') is True
    title = (protocol.get('batch_title') if standalone else None) or protocol.get('comparison_arm', protocol.get('agent_profile', 'original'))
    page_title = title if standalone else 'Execution comparison'
    navigation = '' if standalone else '<p><a href="../">Three-arm comparison</a></p>'
    if protocol.get('episode_budget_policy') == 'execution_deadline_only':
        navigation += ('<p>Execution budget: ' + esc(protocol.get('model_timeout_seconds', 1800)) +
                       ' seconds after initialization and MCP readiness. Action counts, tool calls and simulator steps are recorded without separate episode cutoffs.</p>')
    summary = ''
    extra_style = ''
    if standalone:
        counts = Counter(outcome(r) or ('queued' if r['status'] in ('planned', 'queued') else r['status']) for r in progress['tasks'])
        summary = '<p id="batch-counts">' + ' · '.join(
            label + ' ' + str(counts.get(key, 0)) for key, label in (
                ('success', 'Success'), ('failure', 'Failed'), ('timeout', 'Timeout'),
                ('running', 'Running'), ('queued', 'Queued'))) + '</p>'
        summary += '<p><a href="behavior100/progress.json">Progress JSON</a> · <a href="behavior100/manifest.json">Frozen manifest</a></p>'
        extra_style = 'h1,td,th{overflow-wrap:anywhere}table{table-layout:fixed}td,th{padding:8px}th:nth-child(1){width:7%}th:nth-child(2){width:35%}th:nth-child(3){width:28%}th:nth-child(4){width:7%}th:nth-child(5){width:23%}'
    rows = []
    for r in progress['tasks']:
        link = '<a href="' + esc(r['replay_url']) + '">Replay ↗</a>' if r.get('replay_url') else 'Pending'
        rows.append('<tr><td>' + str(r['index'] + 1) + '</td><td>' + esc(r['name']) + '</td><td>' + esc(outcome(r) or r['status']) + ' / ' + esc(r.get('stage', 'queued')) + '</td><td>' + esc(r.get('q_score', '—')) + '</td><td>' + link + '</td></tr>')
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="30"><title>' + esc(page_title) + '</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:32px auto;padding:0 20px}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #ddd}a{color:#087866}' + extra_style + '</style><h1>' + esc(title) + '</h1>' + navigation + summary + '<p>Updated ' + esc(progress['updated_at']) + '. Task success and complete evidence are separate fields.</p><p>' + esc(protocol.get('protocol', 'Four-camera RGB; see frozen manifest for execution protocol')) + '</p><table><tr><th>#</th><th>Task</th><th>Status</th><th>Q</th><th>Replay</th></tr>' + ''.join(rows) + '</table></html>'


DASHBOARD_VERSION = 2


def render_comparison_page(arms=ARMS):
    """Render the maintained template; no JavaScript fragment rewriting."""
    arms=tuple(arms)
    if not arms or set(arms)-set(ARMS):raise ValueError('Unknown comparison arms')
    template=Path(__file__).with_name('comparison_dashboard.html').read_text()
    return template.replace('__ARMS_JSON__',json.dumps(arms)).replace(
        '__NAMES_JSON__',json.dumps([LABELS[arm] for arm in arms],ensure_ascii=False))


# Compatibility for older monitor entry points; new publishers call the renderer.
PAGE = render_comparison_page()


def publish(root):
    config = json.loads((root / 'comparison_config.json').read_text())
    progress = {}
    arms=config.get('active_arms',ARMS)
    for arm in arms:
        batch=Path(config.get('arm_roots',{}).get(arm,root/arm))
        p = batch / 'progress.json'
        progress[arm] = json.loads(p.read_text()) if p.exists() else {'tasks': json.loads((batch / 'manifest.json').read_text())['tasks']}
        annotations=batch/'outcome_annotations.json'
        if annotations.exists():
            overlays=json.loads(annotations.read_text()).get('runs',{})
            allowed={'episode_outcome','termination_reason','episode_timed_out','controller_timeout','timeout',
                     'execution_finished_at_unix','deadline_expired_at_finish'}
            for row in progress[arm]['tasks']:
                for key,value in overlays.get(row.get('run_id'),{}).items():
                    if key in allowed and row.get(key) is None:row[key]=value
    result = summarize_comparison(progress)
    atomic(root / 'comparison.json', result)
    report = Path(config['reports']); report.mkdir(parents=True, exist_ok=True)
    atomic(report / 'comparison.json', result)
    (report / 'index.html').write_text(render_comparison_page(arms))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--root', type=Path, required=True); p.add_argument('--watch', action='store_true')
    args = p.parse_args()
    while True:
        result = publish(args.root)
        if not args.watch or all(s['ended'] == s['total'] for s in result['arms'].values()):
            break
        time.sleep(10)
