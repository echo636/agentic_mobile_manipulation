"""Postprocessing only. Never restart a model episode or modify raw evidence."""
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT/'plan.json').read_text())
SOURCE = Path(PLAN['source'])


def read(path):
    try: return json.loads(path.read_text())
    except (FileNotFoundError,json.JSONDecodeError): return {}


def write(path, value):
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)


def expose_transport_catalog(trial):
    trace = [json.loads(s) for s in (trial/'transport.jsonl').read_text().splitlines() if s.strip()]
    requests = [r for r in trace if r.get('kind') == 'request']
    variants = {}
    for request in requests:
        schemas = request.get('forwarded_tool_schemas')
        if schemas is not None:
            variants[json.dumps(schemas,sort_keys=True)] = schemas
    evidence = {'source':'same-trial transport trace; recorded metadata only',
        'note':'MCP robot catalog and client builtin API tools are distinct. Client builtin tools are also listed; do not interpret six/nine robot tools as the entire API catalog.',
        'api_request_count':len(requests),'forwarded_catalog_variants':list(variants.values())}
    config = read(trial/'run/review_configuration.json')
    config['actual_api_tool_exposure'] = evidence
    config['protocol_configuration_audit'] = read(trial/'configuration_audit.json')
    costs = next((c for c in read(ROOT/'comparison_final.json').get('trials', []) if c['trial'] == trial.name), {})
    config['successful_rejected_and_unanswered_skill_reads'] = costs.get('skill_read_timeline')
    config['first_finish_and_rejections'] = costs.get('termination_review')
    config['frozen_experiment_contract'] = {k:PLAN[k] for k in (
        'protocol_version','scene_version','bddl_sha256','model','reasoning_effort',
        'instance','seed','max_actions','max_sim_steps','wall_timeout_seconds',
        'startup_watchdog_seconds','closure_watchdog_seconds','same_thread_unfinished_recoveries',
        'wire_protocol','order','order_randomized','decision_fields','stop_policy')}
    config['source_manifest_sha256'] = hashlib.sha256((ROOT/'source_manifest.json').read_bytes()).hexdigest()
    config['upstream_manifest_sha256'] = hashlib.sha256((ROOT/'upstream_manifest.json').read_bytes()).hexdigest()
    config['actual_gpu'] = (read(trial/'run/run.json').get('backend') or {}).get('gpu_uuid')
    config['execution_runtime_records'] = {
        'contact_cache_mode':read(trial/'run/contact_cache_mode.json'),
        'render_settings':read(trial/'run/render_settings.json'),
        'note':'Hot-drink particles initialize on GPU, then rigid dynamics switch to CPU. The backend dynamics_mode field based on MAS_CPU_DYNAMICS alone describes the requested initial mode and is insufficient to describe this switch.'}
    write(trial/'run/review_configuration.json',config)
    page = trial/'run/review_5view.html'
    if page.exists():
        extra = {k:config[k] for k in ('actual_api_tool_exposure','protocol_configuration_audit',
            'successful_rejected_and_unanswered_skill_reads','first_finish_and_rejections',
            'frozen_experiment_contract','source_manifest_sha256','upstream_manifest_sha256','actual_gpu',
            'execution_runtime_records')}
        block = '<section id="actual-api-catalog"><h2>实际线上工具与实验配置核验</h2><p>MCP 机器人工具与客户端内置工具分开列出；skill读取和结束记录来自本回合原始日志。</p><details><summary>展开本回合协议与证据</summary><pre>'+html.escape(json.dumps(extra,ensure_ascii=False,indent=2))+'</pre></details></section>'
        page.write_text(page.read_text().replace('<section id="configuration">',block+'<section id="configuration">',1))


def main():
    archived = False
    while True:
        state = read(ROOT/'status.json')
        completed = read(ROOT/'completed_cells.json')
        # The analyzer intentionally refuses to overwrite earlier reports.
        # Archive its completed first-arm report before the second-arm call.
        if not archived and len(completed) == 1 and (ROOT/'comparison.json').exists() and (ROOT/'cost_analysis_status.json').exists():
            (ROOT/'comparison.json').rename(ROOT/'comparison_after_first_arm.json')
            archived = True
        if state.get('status') == 'failed_preserve_and_diagnose':
            write(ROOT/'completion_review_status.json',{'status':'infrastructure_failure_preserved','source_status':state})
            return
        if state.get('status') == 'pair_complete_review_required': break
        time.sleep(15)
    env = dict(os.environ,PYTHONPATH=str(SOURCE/'src'),PYTHONDONTWRITEBYTECODE='1')
    comparison = ROOT/'comparison_final.json'
    with (ROOT/'final_analysis.log').open('x') as stream:
        process = subprocess.run([sys.executable,str(SOURCE/'scripts/analyze_workflow_efficiency.py'),
            *[c['trial'] for c in completed],'--output',str(comparison)],cwd=SOURCE,env=env,stdout=stream,stderr=subprocess.STDOUT)
    if process.returncode:
        write(ROOT/'completion_review_status.json',{'status':'analysis_failure_preserved','returncode':process.returncode});return
    costs = read(comparison)['trials']
    checks = {'both_configuration_audits_passed':all(c.get('configuration_verified') for c in completed),
              'both_videos_decode':all(c.get('video_decode_passed') for c in completed),
              'both_formal_finishes':all(c.get('formal_finish_observed') for c in completed)}
    a,b = [ROOT/c['arm']/'run/skill_snapshot' for c in completed]
    shared = ['visual-exploration','visual-manipulation','pick-and-place','failure-recovery']
    checks['base_skill_bytes_identical'] = all(
        {str(p.relative_to(a/name)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (a/name).rglob('*') if p.is_file()} ==
        {str(p.relative_to(b/name)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (b/name).rglob('*') if p.is_file()}
        for name in shared)
    checks['model_simulator_calls_align'] = all((read(ROOT/c['arm']/'demo_audit.json').get('checks') or {}).get('model_simulator_calls_align') is True for c in completed)
    checks['independent_semantic_trace_matches'] = all((read(ROOT/c['arm']/'demo_audit.json').get('checks') or {}).get('semantic_state_log_matches') is True for c in completed)
    write(ROOT/'pair_configuration_audit.json',{'checks':checks,'passed':all(checks.values()),'sample_size_per_arm':1,
        'scope':'Long-horizon skill plus three tools bundle; high-level hot-drink development instance; no population-level causal claim.'})
    rows = []
    for c, cost in zip(completed,costs):
        rows.append({k:cost.get(k) for k in ['trial','task_success','goal_satisfaction_fraction','actions','failed_actions',
            'failure_counts','wall_seconds','motor_tool_wall_seconds','sim_steps','primitive_counts','opposite_door_action_candidates','recovery_review_candidates']})
    write(ROOT/'paired_results.json',{'protocol':PLAN['protocol_version'],'configuration_verified':all(checks.values()),'results':rows,
        'caution':'One seed per group; recovery intervals and door opposites are candidates requiring RGB/target review, not measured avoidable cost.'})
    for cell in completed:
        expose_transport_catalog(Path(cell['trial']))
    with (ROOT/'final_publication.log').open('x') as stream:
        publication = subprocess.run([sys.executable,str(SOURCE/'scripts/publish_demo_trials.py'),
            '--trials',str(ROOT/'hot_drink_lh_repaired_pair_20261026'),
            '--site-root',str(ROOT.parent.parent/'review_sites/atomic_actions_20261008'),
            '--index-name','hot_drink_lh_repaired_pair_20261026_index.html',
            '--cohort-title','热饮：长程 skill＋计划记忆工具 vs 四个基础 skill，分组意图协议'],
            cwd=SOURCE,env=env,stdout=stream,stderr=subprocess.STDOUT)
    write(ROOT/'final_publication.json',{'returncode':publication.returncode})
    report = ['# 热饮长程对照：执行器修复后的新协议','',
        '自动汇总自两场冻结实验。每组仅一个 seed0 开发实例，比较长程 skill 与三个计划/记忆工具的整体效果。',
        '',f"配置与证据核验：{'通过' if all(checks.values()) else '未完全通过，见 pair_configuration_audit.json'}。",'',
        '| 组别 | 整任务成功 | 目标完成比例 | 动作 | 失败动作 | 执行墙钟秒 |','|---|---|---|---|---|---|']
    for row in rows:
        report.append('| '+ ' | '.join(str(row.get(k)) for k in ['trial','task_success','goal_satisfaction_fraction','actions','failed_actions','wall_seconds'])+' |')
    report += ['', '模型 gpt-6-astra low；实例301/seed0；120动作/20000模拟步；无整场墙钟截止；同GPU顺序执行，先无长程。',
        '两组共用冻结代码、四个基础 skill、complete_static_shell_v2 场景和高层热饮评分，blocked直接接受但未完成计失败；无长程不要求当前子目标。模型与effort为请求及传输报告值，服务商内部模型身份无法独立证明；旧sol模型协议单列，不作修复的单因素对照。',
        '冗余动作与恢复成本请结合 comparison_final.json 的候选区间、目标与RGB人工复核，不能把同primitive下一次成功当作同对象恢复。',
        '启动前GPU3锁被其他会话持有，尚无回合开始时记录调度变更至GPU0，两组保持同一GPU；不按模型成败重采样。',
        '审查页与连续录像保留在各组 run/ 下，配置JSON位于每个视频之前；publication.json记录发布路径。', '']
    (ROOT/'REPORT.md').write_text('\n'.join(report))
    write(ROOT/'completion_review_status.json',{'status':'paired_results_ready','configuration_verified':all(checks.values()),
        'report':str(ROOT/'REPORT.md'),'manual_redundancy_and_recovery_review_pending':True})


if __name__ == '__main__': main()
