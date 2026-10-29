#!/usr/bin/env python3
"""Make a two-part, five-view review page from preserved simulator evidence."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess


def review_metadata(data, run_record, controller_record=None):
    """Report recorded run identity; never infer a model setting from a name."""
    config = run_record.get('config') or {}
    controller_record = controller_record or {}
    task = config.get('task') or data.get('config', {}).get('task') or '未记录'
    instance = config.get('instance', '未记录')
    seed = config.get('seed', '未记录')
    if config.get('model_used') is False:
        manual_rgb = config.get('selection') == 'manual RGB pixel, public act interface'
        return {'task': task, 'instance': instance, 'seed': seed,
                'execution': ('人工 RGB 选点，经公开 act 接口' if manual_rgb else
                              '执行器脚本选择目标（未调用大脑模型）'),
                'model': '未使用模型', 'reasoning_effort': '不适用'}
    model = (controller_record.get('model') or (run_record.get('native_loop') or {}).get('model')
             or data.get('model') or config.get('model') or '未记录')
    effort = (controller_record.get('model_reasoning_effort') or controller_record.get('reasoning_effort')
              or (controller_record.get('config') or {}).get('model_reasoning_effort')
              or (run_record.get('native_loop') or {}).get('reasoning_effort')
              or config.get('model_reasoning_effort') or config.get('reasoning_effort'))
    if effort is None:
        command = controller_record.get('command') or []
        for index, token in enumerate(command[:-1]):
            if token != '-c':
                continue
            key, separator, value = str(command[index + 1]).partition('=')
            if separator and key.strip() == 'model_reasoning_effort':
                try:
                    effort = json.loads(value)
                except json.JSONDecodeError:
                    effort = value.strip().strip('"')
                break
    startup_failed = controller_record.get('failure_stage') in {'prepare_project', 'mcp_handshake', 'before_model_start'}
    semantic = config.get('observation_mode') == 'rgb_semantic_magic'
    return {'task': task, 'instance': instance, 'seed': seed,
            'execution': ('模型未启动' if startup_failed else
                          '模型驱动 · 按名称选择魔法动作' if semantic and model != '未记录' else
                          '模型驱动 · RGB 选点' if model != '未记录' else '执行方式未记录'),
            'model': model, 'reasoning_effort': effort if effort is not None else '未记录'}


def h(value):
    return html.escape(str(value), quote=True)


def j(value):
    return h(json.dumps(value, ensure_ascii=False, indent=2))


def review_configuration(run, run_record, controller_record, events, controller_dir=None):
    """Use archived evidence only; never substitute today's profile or prompt."""
    def read(path):
        return json.loads(path.read_text()) if path.exists() else {}

    controller = controller_record or {}
    summary = read(run.parent / 'summary.json')
    manifest = read(run / 'skill_manifest.json')
    controller_dir = controller_dir or run.parent / 'controller'
    preflight = read(controller_dir / 'mcp_preflight.json')
    config = run_record.get('config') or {}
    backend = run_record.get('backend') or {}
    prompt = None
    for token in controller.get('command') or []:
        if isinstance(token, str) and token.startswith('developer_instructions='):
            value = token.partition('=')[2]
            try:
                prompt = json.loads(value)
            except json.JSONDecodeError:
                prompt = value
    skills = [s['name'] for s in manifest.get('skills', [])]
    tools = preflight.get('tool_names')
    planning_tools = {'update_plan', 'remember', 'recall'}
    group = '未记录：无法确认长程 skill 与配套工具配置'
    if 'skills' in manifest and tools is not None:
        if 'long-horizon-planning' in skills and planning_tools <= set(tools):
            group = '有长程 skill＋三个计划/记忆工具'
        elif 'long-horizon-planning' not in skills and not planning_tools.intersection(tools):
            group = '无长程 skill＋无三个计划/记忆工具'
        else:
            group = '其他/历史配置：不符合当前两组组合'
    reads = [e.get('arguments') or {} for e in events
             if e.get('kind') == 'tool_call' and e.get('name') == 'read_skill']
    calls = [e for e in events if e.get('kind') == 'tool_call']
    return {
        'evidence_note': '来自本轮保存的记录；未记录不等于关闭。配置展示不代表独立审计通过。',
        'comparison_group': group,
        'agent_profile': config.get('agent_profile'),
        'model': review_metadata({}, run_record, controller),
        'provider': {k: summary.get(k) for k in ('provider_profile', 'provider_host')},
        'client_version': controller.get('client_version'),
        'task': {k: config.get(k) for k in ('task', 'instance', 'seed')},
        'task_instance': backend.get('task_instance'),
        'task_definition_origin': backend.get('task_definition_origin'),
        'research_instance_configuration': {key: (summary.get('research_native_instance') or {}).get(key)
            for key in ('spec_sha256', 'scene_variant', 'construction_binding_selection',
                        'construction_model_sizes', 'construction_open_cabinet', 'construction_sampler',
                        'construction_reachability_point', 'particle_compatibility', 'tensor_pipeline_device',
                        'cloth_compatibility', 'cloth_topology', 'contact_warmup_guard', 'material_condition_compatibility',
                        'solvent_condition_compatibility', 'physics_solver_type',
                        'robot_base_frame',
                        'physics_edit_barrier', 'camera_initialization_barrier', 'articulation_edit_barrier',
                        'human_reference_metrics_available')},
        'input_hashes': backend.get('input_hashes'),
        'skills': {'available': skills if 'skills' in manifest else None,
                   'resources': manifest.get('skills'),
                   'bundle_sha256': run_record.get('skill_bundle_sha256'),
                   'read_requests': [{'name': r.get('name'), 'resource': r.get('resource')} for r in reads]},
        'tools': {'enabled': tools, 'schema_sha256': preflight.get('schema_sha256'),
                  'recorded_schemas': preflight.get('tool_schemas'),
                  'decision_required': ({name: 'decision' in schema.get('required', [])
                                         for name, schema in preflight['tool_schemas'].items()}
                                        if 'tool_schemas' in preflight else None),
                  'decision_fields': ({name: schema.get('properties', {}).get('decision', {}).get('required')
                                       for name, schema in preflight['tool_schemas'].items()}
                                      if 'tool_schemas' in preflight else None),
                  'decision_protocol': run_record.get('public_decision_contract'),
                  'decision_present_calls': sum('decision' in (e.get('arguments') or {}) for e in calls),
                  'recorded_calls': len(calls)},
        'prompt': {'custom_developer_text': prompt,
                   'sha256': hashlib.sha256(prompt.encode()).hexdigest() if isinstance(prompt, str) else None,
                   'scope': '本轮自定义 developer prompt；客户端基础指令不包含在此文本中',
                   'task_instruction': controller.get('instruction') or config.get('instruction')},
        'observation': {k: backend.get(k) for k in
                        ('robot_camera_views', 'image_size', 'camera_rig', 'video_capture_policy', 'model_visible_truth', 'action_feedback_contract')},
        'execution': {k: backend.get(k) for k in
                      ('executor', 'dynamics_mode', 'tensor_pipeline_device', 'particle_compatibility', 'physics_solver_type', 'physics_edit_barrier', 'camera_initialization_barrier', 'articulation_edit_barrier',
                       'demo_motion', 'visual_navigation_contract', 'placement',
                       'high_level_environment_open_close_protocol')},
        'evaluation_protocol': (run_record.get('evaluation') or {}).get('protocol'),
        'scoring': run_record.get('scoring'),
        'budget': run_record.get('budget'),
        'execution_budget_seconds': run_record.get('execution_budget_seconds'),
        'requested_timeout_seconds': controller.get('requested_timeout_seconds'),
        'termination_contract': run_record.get('termination_contract'),
        'unfinished_recovery': controller.get('unfinished_recovery'),
        'wire_protocol': summary.get('wire_protocol') or config.get('wire_protocol'),
        'provenance': {k: run_record.get(k) for k in ('source', 'gpu_uuid', 'interpreter')},
        'sources': ['run.json', 'skill_manifest.json', 'events.jsonl',
                    'controller/controller.json', 'controller/mcp_preflight.json', 'summary.json'],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--controller-dir", type=Path)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    run = args.run_dir.resolve()
    data = json.loads((run / "replay.json").read_text())
    run_record = json.loads((run / "run.json").read_text())
    executor_events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    navigation_by_request = {}
    current_request = None
    for event in executor_events:
        if event.get('kind') == 'tool_call':
            current_request = event.get('request_id')
        elif event.get('kind') == 'private_executor_result' and current_request:
            details = event.get('details') or {}
            steps = details.get('navigation_steps')
            if steps is None and details.get('nav_status') == 'reached':
                steps = details.get('steps')
            if isinstance(steps, int):
                navigation_by_request[current_request] = steps
        elif event.get('kind') == 'tool_result':
            current_request = None
    navigation_plans = ([json.loads(line) for line in (run / 'navigation_plans.jsonl').read_text().splitlines()]
                        if (run / 'navigation_plans.jsonl').exists() else [])
    base_motion_rows = ([json.loads(line) for line in (run / 'base_motion.jsonl').read_text().splitlines()]
                        if (run / 'base_motion.jsonl').exists() else [])
    base_motion_count = len(base_motion_rows)
    base_heights = [float(row['actual_position'][2]) for row in base_motion_rows]
    max_height_deviation = (max(abs(z - base_heights[0]) for z in base_heights)
                            if base_heights else 0)
    height_warning = (f'视觉证据异常：机器人底盘相对初始高度偏移最高 {max_height_deviation:.2f} 米，'
                      '部分画面可能在地板下；该录像不能作为正常室内行走证据。'
                      if max_height_deviation > .10 else '')
    action_intervals = {}
    for event in executor_events:
        request_id = event.get('request_id')
        if event.get('kind') == 'tool_call' and request_id:
            action_intervals[request_id] = {'start': event['at'], 'arguments': event.get('arguments') or {}}
        elif event.get('kind') == 'tool_result' and request_id in action_intervals:
            action_intervals[request_id]['end'] = event['at']
    step_by_request = {step.get('request_id'): step['index'] for step in data.get('steps') or []}
    navigation_rows = []
    for number, plan in enumerate(navigation_plans, 1):
        request_id = next((key for key, interval in action_intervals.items()
                           if interval['start'] <= plan['at'] <= interval.get('end', '9999')), None)
        interval = action_intervals.get(request_id, {})
        arguments = interval.get('arguments') or {}
        step_index = step_by_request.get(request_id)
        action_label = (f'<a href="#step-{step_index}">第 {step_index} 步</a>'
                        if step_index is not None else '未配对')
        destination = arguments.get('target')
        if isinstance(destination, dict):
            destination = f"{destination.get('image_ref')} {destination.get('point')}"
        navigation_rows.append(
            f'<tr><td>{number}</td><td>{action_label}</td>'
            f'<td>{h(arguments.get("primitive", ""))} → {h(destination or "")}</td>'
            f'<td>{float(plan["plan"]["geodesic_m"]):.1f} m</td>'
            f'<td>{navigation_by_request.get(request_id, 0)}</td></tr>')
    config = run_record.get('config') or {}
    named_protocol = config.get('agent_profile') == 'semantic'
    controller_path = args.controller_dir / 'controller.json' if args.controller_dir else None
    controller_record = json.loads(controller_path.read_text()) if controller_path and controller_path.exists() else None
    summary_path = run.parent / 'summary.json'
    if controller_record is not None and summary_path.exists():
        summary = json.loads(summary_path.read_text())
        controller_record.setdefault('model_reasoning_effort', summary.get('reasoning_effort'))
    metadata = review_metadata(data, run_record, controller_record)
    configuration = review_configuration(run, run_record, controller_record, executor_events,
                                         args.controller_dir)
    (run / 'review_configuration.json').write_text(
        json.dumps(configuration, ensure_ascii=False, indent=2) + '\n')
    configuration_html = (
        '<section id="configuration"><h2>实验配置</h2>'
        f'<p><b>{h(configuration["comparison_group"])}</b> · profile: '
        f'{h(configuration["agent_profile"] or "未记录")}</p>'
        '<p>下方 null 表示未记录，不能解释为关闭；skill 可用与实际读取分别列出。</p>'
        '<p><a href="review_configuration.json">下载配置 JSON</a></p>'
        + ''.join(f'<details{" open" if key in {"skills", "tools", "budget", "termination_contract"} else ""}>'
                  f'<summary>{h(key)}</summary><pre>{j(value)}</pre></details>'
                  for key, value in configuration.items()) + '</section>')
    transcript = data.get("model_transcript") or []
    trace_by_step = {}
    final_step = data["steps"][-1]["index"]
    for entry in transcript:
        kind = entry.get("kind")
        if kind not in ("provider_summary", "assistant"):
            continue
        step = entry.get("step") if isinstance(entry.get("step"), int) else final_step
        label = ("服务商返回的推理摘要" if kind == "provider_summary" else
                 "工具结束后的模型公开消息" if entry.get("step") is None else "模型公开消息")
        trace_by_step.setdefault(step, []).append(f'<div class="trace-event"><div class="trace-label">{label}</div><pre>{h(entry.get("text") or "")}</pre></div>')
    if metadata['model'] == '未使用模型':
        reasoning_note = ('本次由人工根据机器人 RGB 画面选点，经公开 act 接口调用动作；没有模型推理或模型选点记录。'
                          if config.get('selection') == 'manual RGB pixel, public act interface' else
                          '本次由执行器脚本选择模拟器物体并调用动作；没有模型推理或模型选点记录。')
    elif config.get('agent_profile') == 'planning':
        reasoning_note = ('每次工具调用的公开决策按原始参数展示当前状态、子目标、选择理由和预期结果；'
                          '这是模型输出的简短说明，不是未公开的内部推理。')
    elif data.get("reasoning_availability") == "provider_returned_summary":
        reasoning_note = "每个工具步骤内展示关联的模型公开消息和服务商返回的简短推理摘要；完整内部推理没有提供。"
    else:
        reasoning_note = "每个工具步骤内展示已保存的模型公开消息；这次运行没有服务商返回的推理摘要。"
    point_actor = '人工' if config.get('selection') == 'manual RGB pixel, public act interface' else '模型'
    video = data.get("video") or {}
    if video.get("status") != "passed":
        raise SystemExit("A completed, validated video is required")
    motion_log = run / 'demo_motion.jsonl'
    motion_count = (sum(json.loads(line).get('status') in {'shown', 'partial_reach'}
                        for line in motion_log.read_text().splitlines()) if motion_log.exists() else 0)
    demo_mode = ((f'启用 · {motion_count} 段关节动作按模拟控制步录制；接触与最终状态仍由理想化执行器完成'
                  if motion_count else '已启用；本次没有机械臂动作')
                 if video.get('demo_motion') is True else
                 '关闭 · 使用原有执行器' if video.get('demo_motion') is False else '未记录')
    ffmpeg = shutil.which("ffmpeg") or (video.get("encoder_command") or [None])[0]
    if not ffmpeg:
        raise SystemExit("ffmpeg is required")
    source_video = run / video["file"]
    browser_video = run / "episode_browser.mp4"
    if not browser_video.exists() or browser_video.stat().st_mtime < source_video.stat().st_mtime:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source_video),
                        "-c", "copy", "-movflags", "+faststart", str(browser_video)], check=True)
    frames = [json.loads(line) for line in (run / "video_frames.jsonl").read_text().splitlines()]
    boundaries = {}
    for frame in frames:
        step = frame.get("env_step")
        if step is not None and (step not in boundaries or frame.get("kind") == "observation_boundary"):
            boundaries[step] = frame
    spectator = video["views"].index("spectator")
    tile = video["width"] // 2
    crop_x, crop_y = spectator % 2 * tile, spectator // 2 * tile
    still_dir = run / "review_stills"
    still_dir.mkdir(exist_ok=True)
    cards = []
    for step in data["steps"]:
        obs = step.get("after") or step.get("before") or {}
        capture = obs.get("capture") or {}
        env_step = capture.get("sim_step")
        frame = boundaries.get(env_step)
        if frame is None and env_step is not None:
            frame = min(frames, key=lambda f: abs(f.get("env_step", 0) - env_step))
        spectator_html = '<div class="missing">无第三视角帧</div>'
        if frame:
            name = f"step_{step['index']:03d}_spectator.jpg"
            still = still_dir / name
            if not still.exists():
                subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                                "-ss", str(frame["video_seconds"]), "-i", str(source_video),
                                "-frames:v", "1", "-vf", f"crop={tile}:{tile}:{crop_x}:{crop_y}",
                                str(still)], check=True)
            spectator_html = f'<img loading="lazy" src="review_stills/{h(name)}" alt="第三视角">'
        images = []
        for view in ("front", "back", "left", "right"):
            item = next((im for im in obs.get("images", []) if im.get("view") == view), None)
            if item and item.get("file") and (run / item["file"]).exists():
                images.append(f'<figure><img loading="lazy" src="{h(item["file"])}" alt="{view} RGB"><figcaption>{view} · {h(item.get("image_ref", ""))}</figcaption></figure>')
            else:
                images.append(f'<figure><div class="missing">无 {view} RGB</div><figcaption>{view}</figcaption></figure>')
        images.append(f'<figure>{spectator_html}<figcaption>第三视角 · 仅供审阅，模型未看到</figcaption></figure>')
        target = (step.get("arguments") or {}).get("target")
        selected_html = ""
        point = target.get("point") if isinstance(target, dict) else None
        if isinstance(target, str):
            selected_html = f'<p class="meta">模型选择的物品类别：<strong>{h(target)}</strong></p>'
        if point is not None:
            selected = next((im for im in (step.get("before") or {}).get("images", [])
                             if im.get("image_ref") == target.get("image_ref")), None)
            if (selected and selected.get("file") and (run / selected["file"]).exists()
                    and len(point) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in point)):
                x, y = point
                selected_html = f'''<div class="selected"><h4>{point_actor}选点时的画面</h4>
                  <figure><div class="target-frame"><img loading="lazy" src="{h(selected['file'])}" alt="选点前 {h(selected['view'])} RGB">
                  <span class="target-marker" style="left:{x * 100:.4f}%;top:{y * 100:.4f}%" aria-label="{point_actor}选点"></span></div>
                  <figcaption>{h(selected['view'])} · {h(selected['image_ref'])} · ({x:.2f}, {y:.2f})</figcaption></figure></div>'''
            else:
                selected_html = '<p class="warn">选点坐标与动作前图像无法配对，请核对原始回放。</p>'
        result = step.get("result") or {}
        result_summary = {key: result[key] for key in ("ok", "effect", "error", "reason", "task_success", "actions_used") if key in result}
        step_trace = trace_by_step.get(step["index"], [])
        trace_html = ''.join(step_trace) if step_trace else '<p class="meta">本步没有返回可展示的模型文字或推理摘要。</p>'
        decision = step.get('decision')
        decision_html = ''
        if isinstance(decision, dict):
            labels = {'state': '当前状态', 'active_subgoal': '当前子目标',
                      'reason': '选择理由', 'expected': '预期结果'}
            fields = ''.join(f'<div class="trace-event"><div class="trace-label">{label}</div><p>{h(decision.get(key, ""))}</p></div>'
                             for key, label in labels.items() if key in decision)
            decision_html = f'<div class="step-trace"><h4>本步公开决策</h4><div class="trace">{fields}</div></div>'
        motor = step.get("arguments", {}).get("primitive")
        badges = f'<span class="pill">{h(step.get("status", "unknown"))}</span> <span class="pill">env.step {h(env_step)}</span>'
        navigation_steps = navigation_by_request.get(step.get('request_id'), 0)
        if navigation_steps:
            badges += f' <span class="pill">行走 {navigation_steps} 控制步</span>'
        if not step.get("new_observation"):
            badges += ' <span class="pill warn">沿用上次观测</span>'
        cards.append(f'''<article id="step-{step['index']}" class="step">
          <div class="stephead"><h3>{step['index']:02d} · {h(step['tool'])}{' / ' + h(motor) if motor else ''}</h3><div>{badges}</div></div>
          {decision_html}
          <div class="step-trace"><h4>关联本步的模型记录</h4><div class="trace">{trace_html}</div></div>
          {selected_html}
          <h4>工具返回后的四路 RGB 与第三视角</h4>
          <div class="views">{''.join(images)}</div>
          <details open><summary>工具调用参数与结果</summary><div class="detailsgrid"><div><h4>参数</h4><pre>{j(step.get('arguments') or {})}</pre></div><div><h4>结果摘要</h4><pre>{j(result_summary)}</pre></div></div></details>
          <details><summary>完整工具结果</summary><pre>{j(result)}</pre></details>
        </article>''')
    evaluation = run_record.get("evaluation") or {}
    semantic_audit_path = run / 'posthoc_semantic_audit.json'
    semantic_audit = json.loads(semantic_audit_path.read_text()) if semantic_audit_path.exists() else None
    if semantic_audit:
        semantic_result = semantic_audit['evaluation']
        semantic_goals = semantic_result['semantic_goals']
        score_label = f"高层任务 {sum(semantic_goals.values())}/{len(semantic_goals)} 项达成"
        official_result = evaluation.get('official_task_success')
        official_label = ('未评估' if official_result is None else
                          '通过' if official_result is True else '未通过')
        success_label = f"高层语义评分 {semantic_result['task_success']}；OG 官方物理评分{official_label}"
        protocol_note = ('本轮使用高层语义动作与状态评分：场景、机器人行走和录像由 OmniGibson 提供，'
                         '液体、加热及摆放关系由高层状态机判定。'
                         + ('模型自行选择动作、目标名称及顺序；执行器按名称定位物体、自动规划行走并执行魔法操作，'
                            '因此这不是 RGB 自主选点实验。' if named_protocol else
                            '模型通过四路 RGB 自行选点、选择动作与顺序，并显式调用 navigate_to；'
                            '高层状态机仍代替真实接触、液体和加热物理。'))
        if run_record.get('task_success') is not True and semantic_result['task_success'] is True:
            protocol_note += '原始 run.json 中的失败是重复评分入口误调了旧版物理评分；'
        if controller_record and any('To carry something' in str(token)
                                     for token in controller_record.get('command') or []):
            protocol_note += ('该旧回合的系统提示还解释了抓取、倒水和加热所需的操作依赖，'
                              '因此不能作为完全无任务提示的规划成绩；')
        protocol_note += ('本页按成功动作日志独立重算，高层状态日志逐项核对。'
                          '此结果不代表 BEHAVIOR 官方 BDDL 通过。')
    elif not evaluation:
        score_label = '终态评分缺失'
        success_label = '独立任务完成情况：未知'
        protocol_note = ('回合中断，记录中没有终态 evaluation。'
                         '不根据运行失败状态推断任务得分，不将缺失评估标为官方失败。')
    elif evaluation.get('protocol') == 'original_native_predicates_research_instance_v1':
        options = evaluation.get('goal_options') or []
        best = max(options, key=lambda row: sum(row) / len(row) if row else 0, default=[])
        score_label = f"原生研究实例 {sum(best)}/{len(best)} 项达成"
        success_label = f"独立原生任务成功 {evaluation.get('native_research_task_success')}"
        protocol_note = ('目标保留原始 BDDL，场景为项目生成的研究实例，由仿真状态中的原生谓词独立判定。'
                         '不属于公开挑战实例；没有人类参考数据，未计算官方 Q 或人类归一化成绩。')
    elif str(evaluation.get('protocol', '')).startswith('custom_native_bddl'):
        score_label = f"自定义 BDDL 原生指标 Q {((evaluation.get('official_metrics') or {}).get('q_score') or {}).get('final', '未评分')}"
        success_label = f"自定义任务成功 {evaluation.get('custom_task_success')}"
        protocol_note = '场景和谓词评分使用 OmniGibson；目标来自本次 BDDL overlay，不代表公开挑战任务成绩。'
    else:
        score_label = f"本地官方指标 Q {((evaluation.get('official_metrics') or {}).get('q_score') or {}).get('final', '未评分')}"
        success_label = f"独立任务成功 {evaluation.get('official_task_success', run_record.get('task_success'))}"
        protocol_note = ''
    replay_steps = len(data.get("steps") or [])
    action_tools = {'act', 'act_semantic'}
    model_actions = sum(step.get("tool") in action_tools for step in data.get("steps") or [])
    successful_model_actions = sum(
        step.get("tool") in action_tools and (step.get("result") or {}).get("ok") is True
        for step in data.get("steps") or []
    )
    action_budget = config.get("max_actions")
    sim_steps = run_record.get("sim_steps")
    video_frames = video.get("frame_count")
    last_act = next((step for step in reversed(data.get("steps") or [])
                     if step.get("tool") in action_tools), None)
    remaining_after_last_act = ((last_act or {}).get("result") or {}).get("actions_remaining")
    count_note = (
        f"回放记录 {replay_steps} 条（包含初始化、技能读取、模型动作和结束调用）；"
        f"模型动作 {model_actions} 次，其中成功 {successful_model_actions} 次"
        + (f"，预算 {action_budget} 次" if action_budget is not None else "")
        + (f"，最后一次动作后还剩 {remaining_after_last_act} 次" if remaining_after_last_act is not None else "")
        + (f"；仿真控制步 {sim_steps} 步" if sim_steps is not None else "")
        + (f"；视频帧 {video_frames} 帧" if video_frames is not None else "")
        + "。"
    )
    termination_note = (
        f"结束理由是模型提交的公开文案；最后一次动作的回执仍显示剩余 {remaining_after_last_act} 次，"
        "所以本次是模型提前结束，回放记录数不等于动作数。"
        if remaining_after_last_act is not None and remaining_after_last_act > 0 else ""
    )
    if action_budget is not None and action_budget != 80:
        termination_note += f" 本页面记录的是诊断用 {action_budget} 次预算；正式长程实验要求 80 次。"
    protocol_note_html = (f'<p class="meta warn">{h(protocol_note)}'
                          + (' <a href="posthoc_semantic_audit.json">查看重算证据</a>'
                             if semantic_audit else '') + '</p>') if protocol_note else ''
    html_doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>机器人实验审阅 · {h(data.get('run_id', ''))}</title><style>
    :root{{--bg:#0b1320;--panel:#142235;--line:#2c4058;--text:#e8f0f8;--muted:#a8bacb;--accent:#73d6ca}}
    *{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,sans-serif}}
    header{{position:sticky;top:0;z-index:5;background:#0b1320ee;border-bottom:1px solid var(--line);padding:12px max(20px,calc((100vw - 1500px)/2))}}
    header a{{color:var(--accent);margin-right:24px;text-decoration:none}}main{{max-width:1500px;margin:auto;padding:24px}}
    h1{{font-size:clamp(25px,3vw,42px);margin:12px 0}}h2{{font-size:28px;margin-top:45px}}h3{{margin:0;font-size:21px}}h4{{margin:0 0 8px}}
    .meta{{color:var(--muted)}}.step{{margin:28px 0;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}}
    .run-meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:18px 0}}.run-meta div{{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 13px}}.run-meta small{{display:block;color:var(--muted)}}
    table{{width:100%;border-collapse:collapse}}th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line)}}
    .stephead{{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-bottom:14px;flex-wrap:wrap}}
    .pill{{display:inline-block;border:1px solid #466c75;color:#c1faf3;border-radius:999px;padding:2px 10px;margin-left:5px;font-size:13px}}.warn{{color:#ffd994;border-color:#a38345}}
    .views{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}}figure{{margin:0;background:#0b1726;border-radius:8px;overflow:hidden}}
    figure img{{display:block;width:100%;aspect-ratio:1;object-fit:cover}}figcaption{{padding:6px 8px;color:var(--muted);font-size:13px}}.missing{{height:100%;min-height:160px;display:grid;place-items:center}}
    .selected{{margin:0 0 18px}}.selected figure{{width:min(100%,480px)}}.selected h4{{margin-bottom:8px}}
    .target-frame{{position:relative}}.target-frame img{{width:100%}}.target-marker{{position:absolute;display:block;width:22px;height:22px;transform:translate(-50%,-50%);border:3px solid #ffcf33;border-radius:50%;box-shadow:0 0 0 2px #151515,0 0 12px #000;background:#f33a}}
    .target-marker:before,.target-marker:after{{content:"";position:absolute;background:#ffcf33}}.target-marker:before{{width:2px;height:32px;left:7px;top:-8px}}.target-marker:after{{height:2px;width:32px;top:7px;left:-8px}}
    .step-trace{{margin:0 0 18px}}.trace{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px}}.trace-event{{background:#0b1726;border:1px solid var(--line);border-radius:9px;padding:12px}}
    .trace-label{{color:var(--accent);font-size:13px}}.trace-event pre{{margin:7px 0 0;max-height:220px}}
    details{{border-top:1px solid var(--line);padding-top:8px;margin-top:15px}}summary{{cursor:pointer;color:var(--accent)}}.detailsgrid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
    pre{{background:#0b1726;border-radius:8px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;max-height:400px;overflow:auto;font-size:13px}}
    video{{display:block;width:100%;background:black}}.video-wrap{{width:min(100%,640px);background:var(--panel);padding:12px;border-radius:14px}}
    a{{color:var(--accent)}}@media(max-width:1100px){{.views{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:650px){{.views{{grid-template-columns:repeat(2,1fr)}}.detailsgrid{{grid-template-columns:1fr}}}}
    </style></head><body><header><a href="#video">① 连续视频</a><a href="#navigation">② 导航记录</a><a href="#steps">③ 逐次工具调用</a></header><main>
    <h1>机器人实验审阅</h1><p class="meta">{h(args.label)} · {replay_steps} 条回放记录 · {h(success_label)} · {h(score_label)}</p>
    {f'<p class="meta warn" style="font-weight:bold">{h(height_warning)}</p>' if height_warning else ''}
    {protocol_note_html}
    <div class="run-meta"><div><small>任务</small>{h(metadata['task'])}</div><div><small>实例 / 种子</small>{h(metadata['instance'])} / {h(metadata['seed'])}</div><div><small>执行方式</small>{h(metadata['execution'])}</div><div><small>观察协议</small>{h(config.get('observation_mode', '未记录'))}</div><div><small>模型</small>{h(metadata['model'])}</div><div><small>推理强度</small>{h(metadata['reasoning_effort'])}</div><div><small>动作呈现</small>{h(demo_mode)}</div><div><small>计数口径</small>{h(count_note)}</div></div>
    {configuration_html}
    <p class="meta">任务指令：{h(data.get('instruction') or '未记录')}</p>
    {f'<p class="meta">导航记录：{len(navigation_plans)} 段路径、{base_motion_count} 个实际底盘控制步、约 {run_record.get("evaluation", {}).get("ideal_navigation_distance_m", "未记录")} 米。{("导航由按名称操作在内部触发，模型没有单独调用 navigate_to。" if named_protocol else "每段路径对应模型通过 RGB 选点发出的 act(navigate_to)。")}对应视频展示行走过程。</p>' if navigation_plans else ''}
    <p class="meta">{h(count_note)}</p>
    {f'<p class="meta warn">{h(termination_note)}</p>' if termination_note else ''}
    <p class="meta">四路 RGB 是执行器录制的机器人观测；第三视角仅供审阅。视频按模拟控制步连续记录，等待时间不计入片长。{h(reasoning_note)}</p>
    <section id="video"><h2>① 连续视频</h2><div class="video-wrap"><video controls preload="metadata" poster="video_poster.jpg" src="episode_browser.mp4"></video>
    <p class="meta">原始视频：<a href="episode.mp4">episode.mp4</a> · <a href="video.json">录像证据</a> · <a href="run.json">模拟器记录</a> · <a href="replay.json">审阅数据</a></p></div></section>
    {'<section><h2>场景渲染对照</h2><p class="meta">同一机器人机位的诊断画面：左侧为旧回合，右侧加载相邻房间后的新场景。此图是场景诊断，不是模型动作证据。</p><img src="scene_render_comparison.jpg" alt="场景渲染修复前后对照" style="display:block;max-width:100%;height:auto;border-radius:10px"></section>' if (run / 'scene_render_comparison.jpg').exists() else ''}
    {f'<section id="navigation"><h2>② 导航记录</h2><p class="meta">{("模型没有单独调用 navigate_to；下表每段行走由按名称动作触发。" if named_protocol else "模型逐次选点调用 navigate_to；执行器规划并连续执行所选目标的路径。")}距离是计划路径长度，控制步数含到达后的稳定步骤。</p><table><thead><tr><th>序号</th><th>模型动作</th><th>动作及目标</th><th>计划距离</th><th>底盘控制步</th></tr></thead><tbody>{"".join(navigation_rows)}</tbody></table></section>' if navigation_rows else ''}
    <section id="steps"><h2>③ 逐次工具调用</h2>{''.join(cards)}</section>
    </main></body></html>'''
    target = run / "review_5view.html"
    target.write_text(html_doc)
    print(json.dumps({"page": str(target), "steps": len(cards), "video": str(browser_video)}))


if __name__ == "__main__":
    main()
