#!/usr/bin/env python3
"""Make a two-part, five-view review page from preserved simulator evidence."""
import argparse
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
    return {'task': task, 'instance': instance, 'seed': seed,
            'execution': '模型驱动' if model != '未记录' else '执行方式未记录',
            'model': model, 'reasoning_effort': effort if effort is not None else '未记录'}


def h(value):
    return html.escape(str(value), quote=True)


def j(value):
    return h(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--controller-dir", type=Path)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    run = args.run_dir.resolve()
    data = json.loads((run / "replay.json").read_text())
    run_record = json.loads((run / "run.json").read_text())
    config = run_record.get('config') or {}
    controller_path = args.controller_dir / 'controller.json' if args.controller_dir else None
    controller_record = json.loads(controller_path.read_text()) if controller_path and controller_path.exists() else None
    metadata = review_metadata(data, run_record, controller_record)
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
    elif data.get("reasoning_availability") == "provider_returned_summary":
        reasoning_note = "每个工具步骤内展示关联的模型公开消息和服务商返回的简短推理摘要；完整内部推理没有提供。"
    else:
        reasoning_note = "每个工具步骤内展示已保存的模型公开消息；这次运行没有服务商返回的推理摘要。"
    point_actor = '人工' if config.get('selection') == 'manual RGB pixel, public act interface' else '模型'
    video = data.get("video") or {}
    if video.get("status") != "passed":
        raise SystemExit("A completed, validated video is required")
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
        target = (step.get("arguments") or {}).get("target") or {}
        selected_html = ""
        point = target.get("point")
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
        motor = step.get("arguments", {}).get("primitive")
        badges = f'<span class="pill">{h(step.get("status", "unknown"))}</span> <span class="pill">env.step {h(env_step)}</span>'
        if not step.get("new_observation"):
            badges += ' <span class="pill warn">沿用上次观测</span>'
        cards.append(f'''<article id="step-{step['index']}" class="step">
          <div class="stephead"><h3>{step['index']:02d} · {h(step['tool'])}{' / ' + h(motor) if motor else ''}</h3><div>{badges}</div></div>
          <div class="step-trace"><h4>关联本步的模型记录</h4><div class="trace">{trace_html}</div></div>
          {selected_html}
          <h4>工具返回后的四路 RGB 与第三视角</h4>
          <div class="views">{''.join(images)}</div>
          <details open><summary>工具调用参数与结果</summary><div class="detailsgrid"><div><h4>参数</h4><pre>{j(step.get('arguments') or {})}</pre></div><div><h4>结果摘要</h4><pre>{j(result_summary)}</pre></div></div></details>
          <details><summary>完整工具结果</summary><pre>{j(result)}</pre></details>
        </article>''')
    evaluation = run_record.get("evaluation") or {}
    score = ((evaluation.get("official_metrics") or {}).get("q_score") or {}).get("final", "未评分")
    success = evaluation.get("official_task_success", run_record.get("task_success"))
    html_doc = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>机器人实验审阅 · {h(data.get('run_id', ''))}</title><style>
    :root{{--bg:#0b1320;--panel:#142235;--line:#2c4058;--text:#e8f0f8;--muted:#a8bacb;--accent:#73d6ca}}
    *{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,sans-serif}}
    header{{position:sticky;top:0;z-index:5;background:#0b1320ee;border-bottom:1px solid var(--line);padding:12px max(20px,calc((100vw - 1500px)/2))}}
    header a{{color:var(--accent);margin-right:24px;text-decoration:none}}main{{max-width:1500px;margin:auto;padding:24px}}
    h1{{font-size:clamp(25px,3vw,42px);margin:12px 0}}h2{{font-size:28px;margin-top:45px}}h3{{margin:0;font-size:21px}}h4{{margin:0 0 8px}}
    .meta{{color:var(--muted)}}.step{{margin:28px 0;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}}
    .run-meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:18px 0}}.run-meta div{{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 13px}}.run-meta small{{display:block;color:var(--muted)}}
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
    </style></head><body><header><a href="#video">① 连续视频</a><a href="#steps">② 逐次工具调用</a></header><main>
    <h1>机器人实验审阅</h1><p class="meta">{h(args.label)} · {len(cards)} 次工具调用 · {h(data.get('status'))} · 独立任务成功 {h(success)} · 本地官方指标 Q {h(score)}</p>
    <div class="run-meta"><div><small>任务</small>{h(metadata['task'])}</div><div><small>实例 / 种子</small>{h(metadata['instance'])} / {h(metadata['seed'])}</div><div><small>执行方式</small>{h(metadata['execution'])}</div><div><small>模型</small>{h(metadata['model'])}</div><div><small>推理强度</small>{h(metadata['reasoning_effort'])}</div></div>
    <p class="meta">任务指令：{h(data.get('instruction') or '未记录')}</p>
    <p class="meta">四路 RGB 是执行器录制的机器人观测；第三视角仅供审阅。视频按模拟控制步连续记录，等待时间不计入片长。{h(reasoning_note)}</p>
    <section id="video"><h2>① 连续视频</h2><div class="video-wrap"><video controls preload="metadata" poster="video_poster.jpg" src="episode_browser.mp4"></video>
    <p class="meta">原始视频：<a href="episode.mp4">episode.mp4</a> · <a href="video.json">录像证据</a> · <a href="run.json">模拟器记录</a> · <a href="replay.json">审阅数据</a></p></div></section>
    <section id="steps"><h2>② 逐次工具调用</h2>{''.join(cards)}</section>
    </main></body></html>'''
    target = run / "review_5view.html"
    target.write_text(html_doc)
    print(json.dumps({"page": str(target), "steps": len(cards), "video": str(browser_video)}))


if __name__ == "__main__":
    main()
