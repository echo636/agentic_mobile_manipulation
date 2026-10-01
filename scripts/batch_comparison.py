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


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三种执行方式 · 100 任务对比</title>
<style>body{font:16px/1.65 system-ui;margin:32px auto;max-width:1320px;padding:0 20px;background:#f5f7fa;color:#20313c}h1{font-size:28px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{background:white;border:1px solid #dbe2e8;border-radius:12px;padding:20px}.card strong{font-size:32px}.muted,small{color:#627282}small{display:block}a{color:#067768}input,select{font:inherit;padding:9px;margin:16px 8px 16px 0;border:1px solid #c9d4de;border-radius:6px}table{width:100%;border-collapse:collapse;background:white}td,th{padding:14px;text-align:left;border-bottom:1px solid #e0e6eb;vertical-align:top}td:first-child{max-width:280px}td:not(:first-child){min-width:180px}.scroll{overflow-x:auto}.passed{color:#08784f}.failed,.blocked{color:#a44032}.running{color:#155cab}.planned{color:#77838e}details{margin:14px 0}summary{cursor:pointer}@media(max-width:750px){.cards{grid-template-columns:1fr}h1{font-size:23px}}</style>
<h1>原实现 / Motor / Official</h1><p>同一批 100 个任务 · GPT-6 Astra · public instance 301 · seed 0</p><p class="muted" id="updated">读取进度…</p><div class="cards" id="cards"></div>
<details><summary>对比条件与统计口径</summary><p>三组全新运行，共 300 条；相同指令、初始实例、四路 RGB 和预算：80 次执行器命令、240 次工具调用、20,000 仿真步、模型 30 分钟。任务指令沿用原清单：50 条官方原文，50 条原有静态 BDDL 任务描述，没有新增提示。</p><p>原实现使用项目导航和操作执行器；Motor 由大脑写代码组合底盘、关节及夹爪控制；Official 直接执行官方符号动作，可能直接修改状态或位置。三组的命令粒度不同，动作数不能视为相同物理工作量。机器和 GPU 逐次记录，跨机器耗时不是严格的硬件性能比较。</p><p>“目标成功”来自独立官方评分；“完整通过”还要求控制器结束、观测、视频与轨迹校验通过。无评分故障保留在 100 条总数中。尚未完成的任务不当作失败。回放中的大脑内容来自实际模型输出及提供方返回的摘要，不补写内部思考。</p></details>
<p id="paired" class="muted"></p><input id="query" placeholder="搜索任务"><select id="filter"><option value="all">全部任务</option><option value="running">有任务正在运行</option><option value="ended">三组都已结束</option><option value="different">已评分结果不同</option></select><div class="scroll"><table><thead><tr><th>任务</th><th>原实现</th><th>Motor</th><th>Official</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>let data=null;const arms=['original','motor','official'],names=['原实现','Motor · 代码控制','Official · 符号动作'];const labels={planned:'待开始',running:'运行中',passed:'完整通过',failed:'未完整通过',blocked:'未启动 / 阻塞'};function node(tag,text,cls){let n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}function render(){if(!data)return;document.querySelector('#updated').textContent='更新 '+new Date(data.updated_at).toLocaleString()+' · 每 10 秒刷新';let cards=document.querySelector('#cards');cards.replaceChildren();for(let i=0;i<3;i++){let a=arms[i],s=data.arms[a],c=node('div','', 'card');c.append(node('div',names[i]),node('strong',s.ended+' / '+s.total),node('div','已结束 · '+(s.counts.running||0)+' 运行中'),node('div','目标成功 '+s.official_goal_successes+' · 完整通过 '+s.fully_validated_successes),node('small','已评分 '+s.scored+' · 无评分故障 '+s.unscored_ended));cards.append(c)}document.querySelector('#paired').textContent='三组均有最终评分的配对任务：'+data.paired_scored_tasks+'；其中目标成功：原实现 '+data.paired_goal_successes.original+' / Motor '+data.paired_goal_successes.motor+' / Official '+data.paired_goal_successes.official;let body=document.querySelector('#rows');body.replaceChildren();let q=document.querySelector('#query').value.toLowerCase(),f=document.querySelector('#filter').value;for(let t of data.tasks){let rs=arms.map(a=>t.arms[a]);if(q&&!(t.name+' '+t.task+' '+t.instruction).toLowerCase().includes(q))continue;if(f==='running'&&!rs.some(r=>r.status==='running'))continue;if(f==='ended'&&!rs.every(r=>['passed','failed','blocked'].includes(r.status)))continue;if(f==='different'&&!(rs.every(r=>typeof r.task_success==='boolean')&&new Set(rs.map(r=>r.task_success)).size>1))continue;let tr=document.createElement('tr'),title=node('td',(t.index+1)+'. '+t.name);title.title=t.instruction;title.append(node('small',t.task));tr.append(title);for(let a of arms){let r=t.arms[a],td=node('td','');td.append(node('div',labels[r.status]||r.status,r.status),node('small',r.task_success===true?'目标成功 · Q='+r.q_score:r.task_success===false?'目标未完成 · Q='+r.q_score:['planned','running'].includes(r.status)?(r.stage||'queued'):'无最终评分'));if(r.worker_id)td.append(node('small',r.worker_id));if(r.model_duration_seconds!=null)td.append(node('small','模型 '+Math.round(r.model_duration_seconds)+' s'));if(r.replay_url){let link=node('a','打开回放 ↗');link.href=a+'/'+r.replay_url;link.target='_blank';link.rel='noopener';td.append(link)}tr.append(td)}body.append(tr)}}async function update(){try{let r=await fetch('comparison.json?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw Error(r.status);data=await r.json();render()}catch(e){document.querySelector('#updated').textContent='读取进度失败，将自动重试'}}document.querySelector('#query').oninput=render;document.querySelector('#filter').onchange=render;update();setInterval(update,10000)</script></html>'''


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
