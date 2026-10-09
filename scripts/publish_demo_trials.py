"""Publish reviewed demo attempts without exposing raw controller storage."""
import argparse
from html import escape
import json
from pathlib import Path
import shutil


PUBLIC_FILES = ('review_5view.html', 'episode.mp4', 'episode_browser.mp4',
                'video_poster.jpg', 'video.json', 'run.json', 'replay.json',
                'replay_audit.json')
PUBLIC_DIRS = ('frames', 'review_stills')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=Path, required=True)
    parser.add_argument('--site-root', type=Path, required=True)
    parser.add_argument('--index-name', default='demo_motion_index.html')
    args = parser.parse_args()
    site = args.site_root.resolve()
    target = site / args.trials.resolve().name
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    reviewed = (path for path in sorted(args.trials.resolve().iterdir())
                if path.is_dir() and ((path / 'demo_audit.json').exists()
                                      or (path / 'summary.json').exists()))
    for number, trial in enumerate(reviewed, 1):
        name = trial.name
        audit_path = trial / 'demo_audit.json'
        audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
        summary = json.loads((trial / 'summary.json').read_text())
        run = trial / 'run'
        destination = target / name
        destination.mkdir(exist_ok=True)
        for filename in PUBLIC_FILES:
            source = run / filename
            if source.exists():
                shutil.copy2(source, destination / filename)
        for dirname in PUBLIC_DIRS:
            source = run / dirname
            if source.exists():
                shutil.copytree(source, destination / dirname, dirs_exist_ok=True)
        shutil.copy2(trial / 'summary.json', destination / 'summary.json')
        if audit:
            shutil.copy2(audit_path, destination / 'demo_audit.json')
        status = ('通过' if audit and audit['passed'] else
                  '未通过 · 服务限流 429' if audit and audit.get('failure_category') == 'provider_rate_limit_429'
                  else '预检或运行中断' if not audit else '未通过')
        stages = '、'.join(audit['model_act_sequence']) if audit else '模型未启动'
        review = (f'<a href="{escape(target.name)}/{name}/review_5view.html">五视角审查与连续视频</a> · '
                  if (run / 'review_5view.html').exists() else '')
        detail = (f'<a href="{escape(target.name)}/{name}/demo_audit.json">审查 JSON</a>' if audit
                  else f'<a href="{escape(target.name)}/{name}/summary.json">运行摘要</a>')
        rows.append(f'<tr><td>{number}</td><td>{escape(name)}<br>{escape(summary["task"])}</td>'
                    f'<td>{escape(summary["model"])} · {escape(summary["reasoning_effort"])}</td>'
                    f'<td>{status}</td><td>{escape(stages)}</td>'
                    f'<td>{review}{detail}</td></tr>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>可见操纵 · 模型自主实验</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:1400px;margin:30px auto;padding:0 18px;background:#f4f7fa;color:#183041}section{background:white;padding:18px 24px;margin:18px 0;border:1px solid #d7e1e8;border-radius:10px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:10px;border:1px solid #d7e1e8;text-align:left;vertical-align:top}a{color:#086b95}</style>
<h1>可见操纵 · 模型自主实验</h1><section><p>模型只接收四路 RGB，通过 MCP act 自主选动作与像素。机器人伸手、抬起、运输与工具划动由真实模拟控制步录制；物体接触和最终状态仍由理想化执行器处理。第三视角只供审阅，模型不可见。每次尝试均保留，不把脚本诊断计入模型成功。</p></section>
<table><thead><tr><th>尝试</th><th>任务</th><th>模型与推理强度</th><th>审查</th><th>模型 act 序列</th><th>证据</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table>
<section><p>通过标准：模型正式 finish、调用序列对齐、模型选中且成功执行目标动作、官方整项任务成功、连续五视角录像通过、机器人运动被记录。各尝试的具体失败检查见审查 JSON。</p></section></html>'''
    (site / args.index_name).write_text(page)
    old_index = site / 'autonomous_model_index.html'
    marker = '<!-- demo-motion-index-link -->'
    if old_index.exists() and args.index_name == 'demo_motion_index.html':
        original = old_index.read_text()
        if marker not in original:
            insertion = (marker + '<section><p>新阶段：<a href="demo_motion_index.html">'
                         '可见操纵的模型自主实验、失败尝试与五视角录像</a>。'
                         '机器人动作按模拟控制步呈现，接触仍为理想化执行。</p></section>')
            original = original.replace('</h1>', '</h1>' + insertion, 1)
            old_index.write_text(original)
    print(json.dumps({'index': str(site / args.index_name),
                      'attempts': len(rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
