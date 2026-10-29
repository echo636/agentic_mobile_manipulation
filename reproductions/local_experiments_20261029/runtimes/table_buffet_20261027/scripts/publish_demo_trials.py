"""Publish reviewed demo attempts without exposing raw controller storage."""
import argparse
from html import escape
import json
from pathlib import Path
import shutil


PUBLIC_FILES = ('review_5view.html', 'episode.mp4', 'episode_browser.mp4',
                'review_configuration.json',
                'video_poster.jpg', 'video.json', 'run.json', 'replay.json',
                'replay_audit.json', 'posthoc_semantic_audit.json',
                'scene_render_comparison.jpg')
PUBLIC_DIRS = ('frames', 'review_stills')


def copy_if_changed(source, destination):
    if destination.exists():
        before, after = source.stat(), destination.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            return
    shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=Path, required=True)
    parser.add_argument('--site-root', type=Path, required=True)
    parser.add_argument('--index-name', help='Defaults to an index name for the recorded motion mode')
    parser.add_argument('--cohort-title', help='Use a task-specific title and coverage summary')
    args = parser.parse_args()
    site = args.site_root.resolve()
    target = site / args.trials.resolve().name
    target.mkdir(parents=True, exist_ok=True)
    rows = []
    task_status = {}
    demo_motion = None
    high_level_present = False
    custom_native_present = False
    reviewed = (path for path in sorted(args.trials.resolve().iterdir())
                if path.is_dir() and ((path / 'demo_audit.json').exists()
                                      or (path / 'summary.json').exists()))
    for number, trial in enumerate(reviewed, 1):
        name = trial.name
        audit_path = trial / 'demo_audit.json'
        audit = json.loads(audit_path.read_text()) if audit_path.exists() else None
        summary = json.loads((trial / 'summary.json').read_text())
        high_level = summary.get('high_level_harness') is True
        custom_native = summary.get('task_definition_origin') == 'custom_bddl_overlay' and not high_level
        high_level_present |= high_level
        custom_native_present |= custom_native
        controller_path = trial / 'controller' / 'controller.json'
        controller = json.loads(controller_path.read_text()) if controller_path.exists() else {}
        task_hint_in_prompt = any('To carry something' in str(token)
                                  for token in controller.get('command') or [])
        trial_mode = summary.get('demo_motion') is True
        if demo_motion is not None and trial_mode is not demo_motion:
            raise ValueError('Cannot publish demo and no-demo trials in one index')
        demo_motion = trial_mode
        task_key = ((summary.get('task_variant') or summary['task']) + ' [custom BDDL]'
                    if custom_native else summary['task'])
        task_entry = task_status.setdefault(task_key, {'attempts': 0, 'passed': False})
        task_entry['attempts'] += 1
        task_entry['passed'] |= bool(audit and audit.get('passed'))
        run = trial / 'run'
        destination = target / name
        destination.mkdir(exist_ok=True)
        for filename in PUBLIC_FILES:
            source = run / filename
            if source.exists():
                copy_if_changed(source, destination / filename)
        for dirname in PUBLIC_DIRS:
            source = run / dirname
            if source.exists() and not (destination / dirname).exists():
                shutil.copytree(source, destination / dirname, dirs_exist_ok=True)
        copy_if_changed(trial / 'summary.json', destination / 'summary.json')
        if audit:
            copy_if_changed(audit_path, destination / 'demo_audit.json')
        protocol_label = ('RGB 选点；导航路径审计见 JSON' if custom_native else
                          'RGB 选点、显式导航' if high_level and not summary.get('semantic_magic')
                          else '按名称、内部导航' if high_level else '')
        status = ('高层语义通过 · 含任务提示（非无提示规划）'
                  if audit and audit['passed'] and high_level and task_hint_in_prompt else
                  '高层语义通过（非官方）' if audit and audit['passed'] and high_level else
                  '自定义 BDDL 通过（非公开成绩）' if audit and audit['passed'] and custom_native else
                  '通过' if audit and audit['passed'] else
                  '未通过 · 服务限流 429' if audit and audit.get('failure_category') == 'provider_rate_limit_429'
                  else '预检或运行中断' if not audit else '未通过')
        if protocol_label:
            status += ' · ' + protocol_label
        if args.cohort_title and audit and not audit.get('passed'):
            reason = ('控制器超时' if summary.get('controller_exit_code') == 241 else
                      audit.get('failure_category') or '未满足通过标准')
            status += ' · ' + escape(reason)
        stages = ('、'.join('缺少 primitive' if primitive is None else str(primitive)
                           for primitive in audit['model_act_sequence'])
                  if audit else '模型未启动')
        review = (f'<a href="{escape(target.name)}/{name}/review_5view.html">五视角审查与连续视频</a> · '
                  if (run / 'review_5view.html').exists() else '')
        detail = (f'<a href="{escape(target.name)}/{name}/demo_audit.json">审查 JSON</a>' if audit
                  else f'<a href="{escape(target.name)}/{name}/summary.json">运行摘要</a>')
        configuration_path = run / 'review_configuration.json'
        if configuration_path.exists():
            configuration = json.loads(configuration_path.read_text())
            config_brief = {k: configuration.get(k) for k in
                            ('comparison_group', 'agent_profile', 'budget',
                             'execution_budget_seconds', 'evaluation_protocol', 'termination_contract')}
            config_brief['skill_bundle'] = (configuration.get('skills') or {}).get('bundle_sha256')
            config_brief['tools'] = (configuration.get('tools') or {}).get('enabled')
            config_html = ('<details><summary>' + escape(configuration.get('comparison_group') or '未记录')
                           + '</summary><pre>' + escape(json.dumps(config_brief, ensure_ascii=False, indent=2))
                           + '</pre></details>'
                           + f'<a href="{escape(target.name)}/{name}/review_configuration.json">完整配置</a>')
        else:
            config_html = '配置未核验：缺少配置导出'
        rows.append(f'<tr><td>{number}</td><td>{escape(name)}<br>{escape(task_key)}</td>'
                    f'<td>{escape(summary["model"])} · {escape(summary["reasoning_effort"])}</td>'
                    f'<td>{escape(summary.get("observation_mode") or "未记录")}</td>'
                    f'<td>{escape(summary.get("provider_profile") or "旧记录：未记录")} · '
                    f'{escape(summary.get("provider_host") or "旧记录：未记录")}</td>'
                    f'<td>{config_html}</td><td>{status}</td><td>{escape(stages)}</td>'
                    f'<td>{review}{detail}</td></tr>')
    catalog = json.loads((Path(__file__).resolve().parents[1] /
                          'src/manipulation_agent/tasks.json').read_text())['tasks']
    first_ten = list(catalog)[:10]
    passed_count = sum(task_status.get(task, {}).get('passed', False) for task in first_ten)
    coverage_rows = ''.join(
        f'<tr><td>{index}</td><td>{escape(task)}</td>'
        f'<td>{"通过" if task_status.get(task, {}).get("passed") else "待通过"}</td>'
        f'<td>{task_status.get(task, {}).get("attempts", 0)}</td></tr>'
        for index, task in enumerate(first_ten, 1))
    index_name = args.index_name or ('no_demo_motion_index.html' if demo_motion is False
                                      else 'demo_motion_index.html')
    title = escape(args.cohort_title) if args.cohort_title else ('可见操纵 · 模型自主实验' if demo_motion is not False else '省略操纵演示 · 模型自主实验')
    motion_description = ('机器人伸手、抬起、运输与工具划动由真实模拟控制步录制；'
                          if demo_motion is not False else
                          '未启用额外的机械臂伸手、抬起与工具划动演示步骤；')
    motion_gate = '、机器人运动被记录' if demo_motion is not False else ''
    if args.cohort_title:
        passed_trials = sum(bool(json.loads((trial / 'demo_audit.json').read_text()).get('passed'))
                            for trial in args.trials.resolve().iterdir()
                            if trial.is_dir() and (trial / 'demo_audit.json').exists())
        coverage = (f'<section><h2>已审查尝试：{len(rows)} 次；通过：{passed_trials} 次</h2>'
                    '<p>下表列出每次尝试。点击五视角审查可查看连续录像与逐步回放；'
                    '具体失败检查见对应审查 JSON。</p></section>')
        context = ''
    else:
        coverage = ('<section><h2>前十项任务：通过 '+str(passed_count)+' / 10</h2><table><thead><tr><th>序号</th><th>任务</th><th>官方整任务审查</th><th>已记录尝试</th></tr></thead><tbody>'+coverage_rows+'</tbody></table></section>'
                    '<section><p>相机诊断：旧版中心相机把机器人头部拍进前向 RGB。相同机器人位置下，前移 0.35 米并下俯 35 度可移除这块固定遮挡，同时保留近处垃圾桶。以下是无模型、同场景的相机对照，不计入任务成功。</p><a href="demo_camera_radius_comparison.jpg"><img src="demo_camera_radius_comparison.jpg" alt="相机位置对比" style="max-width:100%"></a><a href="demo_camera_pitch_comparison.jpg"><img src="demo_camera_pitch_comparison.jpg" alt="相机俯视角对比" style="max-width:100%"></a></section>')
        context = '<p>早期 first-ten 尝试误用了桌面 ccswitch 线路，429 属于该线路。新尝试使用仓库外的独立实验凭据和 api.gpt.ge；表中只显示服务域名，不显示密钥。</p>'
    if high_level_present:
        context += ('<p>标为“高层语义通过”的回合按动作日志重算高层状态关系；'
                    'OmniGibson 提供场景、行走和录像。它不表示 BEHAVIOR 官方 BDDL 物理任务通过。'
                    '各回合的原始评分与独立重算证据可在审查页面核对。</p>')
    passing_rule = ('模型正式 finish、调用序列对齐、模型 RGB 选点、自定义 BDDL 整任务成功、'
                    '导航路径来自模型显式调用、连续五视角录像通过；不计为公开挑战成绩'
                    if custom_native_present else
                    '模型正式 finish、调用序列对齐、模型选中且成功执行目标动作、'
                    '高层语义目标或官方物理任务按对应协议通过、连续五视角录像通过'
                    if high_level_present else
                    '模型正式 finish、调用序列对齐、模型选中且成功执行目标动作、官方整项任务成功、连续五视角录像通过')
    effect_description = ('本组使用自定义原生 BDDL，由仿真状态的原生谓词判定；不属于公开任务成绩。'
                          if custom_native_present else
                          '不同回合分别由物理接触或高层状态关系判定，详情见审查记录。'
                          if high_level_present else '物体接触和最终状态由执行器及模拟器检查。')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>'''+title+'''</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:1400px;margin:30px auto;padding:0 18px;background:#f4f7fa;color:#183041}section{background:white;padding:18px 24px;margin:18px 0;border:1px solid #d7e1e8;border-radius:10px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:10px;border:1px solid #d7e1e8;text-align:left;vertical-align:top}a{color:#086b95}</style>
<h1>'''+title+'''</h1><section><p>每条尝试标注观察协议：rgb_only 由模型在四路 RGB 上选像素；rgb_semantic_magic 还向模型提供物品类别名称，由模型按名称选择魔法动作。两种协议分开判断。'''+motion_description+effect_description+'''第三视角只供审阅，模型不可见。每次尝试均保留，不把脚本诊断计入模型成功。</p>'''+context+'''</section>
'''+coverage+'''
<table><thead><tr><th>尝试</th><th>任务</th><th>模型与推理强度</th><th>观察协议</th><th>模型服务线路</th><th>实验配置</th><th>审查</th><th>模型动作序列</th><th>证据</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table>
<section><p>通过标准：'''+passing_rule+motion_gate+'''。各尝试的具体失败检查见审查 JSON。</p></section></html>'''
    (site / index_name).write_text(page)
    old_index = site / 'autonomous_model_index.html'
    marker = '<!-- demo-motion-index-link -->'
    if old_index.exists() and index_name == 'demo_motion_index.html':
        original = old_index.read_text()
        if marker not in original:
            insertion = (marker + '<section><p>新阶段：<a href="demo_motion_index.html">'
                         '可见操纵的模型自主实验、失败尝试与五视角录像</a>。'
                         '机器人动作按模拟控制步呈现，接触仍为理想化执行。</p></section>')
            original = original.replace('</h1>', '</h1>' + insertion, 1)
            old_index.write_text(original)
    print(json.dumps({'index': str(site / index_name),
                      'attempts': len(rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
