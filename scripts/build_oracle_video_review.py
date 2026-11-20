"""Build a local synchronized video/action view from immutable Oracle run evidence."""
import argparse
import html
import os
import subprocess
import json
from pathlib import Path


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def media_sources(root, env, video):
    """Viewing derivatives retain every source frame; never replace raw recordings."""
    full = env / ("playback.mp4" if (env / "playback.mp4").exists() else "episode.mp4")
    if video.get("status") == "passed" and full.name == "episode.mp4":
        temporary = env / "playback.remux.mp4"
        subprocess.run(["ffmpeg","-v","error","-nostdin","-n","-i",str(full),
                        "-c","copy","-movflags","+faststart",str(temporary)],check=True)
        full = env / "playback.mp4"
        temporary.replace(full)
    preview, poster = full, env / "video_poster.jpg"
    if video.get("status") == "passed" and "spectator" in video.get("views", []):
        from manipulation_agent.spectator_video import export_spectator_video
        record = export_spectator_video(env)
        preview = env / record["file"]
        poster = env / "spectator_poster.jpg"
        if not poster.exists():
            subprocess.run(["ffmpeg","-v","error","-nostdin","-n","-threads","2",
                "-i",str(preview),"-frames:v","1","-q:v","4",str(poster)],check=True)
    relative = lambda path: path.relative_to(root).as_posix()
    media = {"preview":{"file":relative(preview),"bytes":preview.stat().st_size,
                       "label":"旁观视角" if preview != full else "完整录像"},
            "full":{"file":relative(full),"bytes":full.stat().st_size,"label":"完整多视角"},
            "poster":relative(poster)}
    for view, label in (("left_wrist", "左腕视角"), ("right_wrist", "右腕视角")):
        if video.get("status") == "passed" and view in video.get("views", []):
            from manipulation_agent.spectator_video import export_camera_video
            record = export_camera_video(env, view)
            path = env / record["file"]
            media[view] = {"file":relative(path),"bytes":path.stat().st_size,"label":label}
    return media


def build(root):
    env = root / "env" if (root / "env").is_dir() else root
    run = read(env / "run.json", {})
    video = read(env / "video.json", {})
    calls = {}
    order = []
    events_path = env / "events.jsonl"
    for line in events_path.read_text().splitlines() if events_path.exists() else []:
        row = json.loads(line)
        request = row.get("request_id")
        if row.get("kind") == "tool_call":
            calls[request] = {"request_id":request,"name":row["name"],"arguments":row.get("arguments"),
                              "origin":row.get("origin"),"at":row.get("at"),"result":None}
            order.append(request)
        elif row.get("kind") == "tool_result" and request in calls:
            result = row["result"]
            calls[request]["result"] = {key:result[key] for key in
                ("ok","error","execution","closed","task_success") if key in result}
    markers = video.get("markers", [])
    for request, call in calls.items():
        matching = [row for row in markers if row.get("request_id")==request]
        call["seconds"] = matching[0]["seconds"] if matching else None
        call["phases"] = [row for row in matching if row.get("phase")]
    messages = []
    path = root / "controller" / "model_events.jsonl"
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line); item = event.get("item", {})
            except ValueError:
                continue
            if event.get("type")=="item.completed" and item.get("type")=="agent_message":
                messages.append(item.get("text",""))
    controller = read(root / "controller" / "controller.json", {})
    media = media_sources(root, env, video)
    scope = read(root / "trial_scope.json", None)
    diagnostic = read(root / "diagnostic_scope.json", None)
    sensor = read(root / "wrist_validation.json", {})
    sensor_summary = {key:sensor[key] for key in
        ("status","kind","checks","mount_errors","last_image_statistics","not_a_model_task_success_trial") if key in sensor}
    engineering = read(root / "engineering_validation.json", None)
    repair = read(env / "scene_geometry_repair.json", {})
    scene_preparation = {key:repair[key] for key in
        ("policy","scope","task_object_bindings_unchanged","original_assets_unmodified") if key in repair}
    if repair:
        scene_preparation["restored_fixed_floor_count"] = len(repair.get("added_floors", []))
    payload = {"media":media, "trial_scope":scope, "engineering_validation":engineering,
       "diagnostic_scope":diagnostic,"sensor_validation":sensor_summary,
       "scene_preparation":scene_preparation,
       "controller": {key:controller.get(key) for key in ("model","model_reasoning_effort","status")},
       "run_id":run.get("run_id"), "status":run.get("status"),
       "task_success":run.get("task_success"),"config":run.get("config",{}),
       "video":{key:video.get(key) for key in ("status","frame_count","duration_seconds","width","height","every_env_step_recorded",
            "synthesized_motion","fresh_camera_frames","repeated_camera_frames","execution_caption","views")},
       "calls":[calls[key] for key in order],"messages":messages,
       "validation_note":"Real simulation evidence. Model claims, motion reached, operation verification, and official task score are separate."}
    (root / "oracle_review.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    encoded=json.dumps(payload,ensure_ascii=False).replace("<","\\u003c")
    page=HTML.replace("__DATA__",encoded).replace("__POSTER__",media["poster"])
    (root/"oracle_review.html").write_text(page)
    return root/"oracle_review.html"


def build_index(runs, output):
    cards = []
    for root in runs:
        report = read(root / "oracle_review.json", {})
        config = report.get("config", {})
        controller = report.get("controller", {})
        model_label = str(controller.get("model") or "参见模型记录") + " / " + str(controller.get("model_reasoning_effort") or "未知")
        if report.get("diagnostic_scope"):
            model_label = "未调用模型（脚本化腕部相机验证）"
        calls = report.get("calls", [])
        operations = {"grasp", "place", "translate", "rotate", "press", "release"}
        attempts = [call for call in calls if call.get("name") in operations]
        verified = [call for call in attempts
                    if ((call.get("result") or {}).get("execution") or {}).get("verified") is True]
        official = {True: "成功", False: "未成功"}.get(report.get("task_success"), "未取得评分")
        href = html.escape(os.path.relpath(root / "oracle_review.html", output.parent), quote=True)
        if verified:
            step = calls.index(verified[0]) + 1
            href += "?step=" + str(step)
        duration = report.get("video", {}).get("duration_seconds")
        length = f"{duration:.2f} 秒" if isinstance(duration, (int, float)) else "参见录像元数据"
        detail = "、".join(call["name"] for call in verified) or "无"
        if verified and all(call["name"] == "release" for call in verified):
            detail += "（空手检查，未验证此前抓持）"
        scope = report.get("trial_scope")
        engineering = report.get("engineering_validation") or {}
        engineering_text = ""
        if report.get("diagnostic_scope"):
            result = (report.get("sensor_validation") or {}).get("status")
            engineering_text = '<p>脚本化腕部相机验收：'+("通过" if result=="passed" else "未通过")+'；未执行自主任务评测。</p>'
        if scope:
            outcome = {True:"成功",False:"未成功"}.get(engineering.get("success"),"未取得验证")
            engineering_text = (f'<p>本次工程子目标：{html.escape(scope["label"])} · {outcome}</p>'
                                '<p>原场景完整任务评分单列；本次未要求完成全部分类。</p>')
        cards.append(f'<article><h2>{html.escape(config.get("task", root.name))}</h2>'
                     f'{engineering_text}'
                     f'<p>模型：{html.escape(model_label)}</p>'
                     f'<p>IK：{html.escape(config.get("ik_backend", "custom_dls（历史）"))}</p>'
                     f'<p>独立完整任务评分：{official} · 录像：{length}</p>'
                     f'<p>操作调用 {len(attempts)} 次；动作验证返回 true：{html.escape(detail)}</p>'
                     f'<p class="id">{html.escape(root.name)}</p>'
                     f'<a href="{href}">打开视频与动作记录 →</a></article>')
    output.write_text("""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oracle 精细操作验证</title><style>
body{font:16px/1.7 system-ui;max-width:1050px;margin:auto;padding:28px;background:#111c27;color:#e2eaf2}
h1{font-size:28px}h2{font-size:21px}article{padding:22px;margin:22px 0;border:1px solid #41566b;border-radius:12px}
a{color:#85d9ff}.id{font:13px ui-monospace;overflow-wrap:anywhere;color:#a9bbce}
</style><h1>Oracle 3D → GPT 路点 → 原生操作：仿真记录</h1>
<p>导航复用原 Harness 的理想化底盘执行；操作为 GPT 给定路点、原生 IK／夹爪／接触。
默认 assisted 抓持。接口测试、动作验证与公开任务独立评分分开记录。</p>
<p>视频保留失败和重试；点击工具或阶段可跳转。相机帧保持按元数据记录，无合成中间运动。
门、抽屉及成功物体放置尚未完成物理验证。</p>
""" + "\n".join(cards) + "</html>")
    return output


HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>Oracle 精细操作 · 仿真回放</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark;font:15px system-ui;background:#101820;color:#e2eaf2}
body{max-width:1500px;margin:auto;padding:24px}h1{font-size:24px;margin:0 0 12px}
.note{color:#aabacb;line-height:1.7}.layout{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(320px,1fr);gap:20px;margin-top:20px}
video{width:100%;max-height:70vh;background:#000;border-radius:10px;aspect-ratio:1/1;object-fit:contain}
.media-controls{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}.media-controls button{width:auto;border:1px solid #54718d;border-radius:8px;padding:10px 14px}
.media-controls a{color:#8dd7ff;align-self:center}.media-status{color:#aabacb;margin:8px 0}
.panel{border:1px solid #354758;border-radius:12px;padding:16px;background:#172330}
.calls{max-height:38vh;overflow:auto}button{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid #354758;background:transparent;color:inherit;padding:12px;cursor:pointer}
button:hover,button.active{background:#254258}.good{color:#7addac}.bad{color:#f4ad95}.unknown{color:#e6ce84}
pre{white-space:pre-wrap;word-break:break-word;font:12px/1.6 ui-monospace;max-height:35vh;overflow:auto}
.meta{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px}.tag{border:1px solid #354758;border-radius:18px;padding:6px 12px}
details{margin-top:18px}.message{white-space:pre-wrap;border-top:1px solid #354758;padding:14px 0;line-height:1.7}
@media(max-width:850px){body{padding:12px}.layout{grid-template-columns:1fr}}
</style>
<h1>Oracle 精细操作 · 真实仿真回放</h1>
<div class="note" id="intro-note">导航沿用原有 Harness；操作使用模型给定路点、IK 与原生接触。默认先显示封面，点击播放加载旁观视角；完整多视角按需切换。右侧点击动作或阶段可定位视频。
模型文字、工具执行、动作验证与官方任务结果分别展示。相机为真实渲染，帧保持按录像元数据记录。</div>
<div id="meta" class="meta"></div><div id="scope-note" class="note"></div>
<div class="layout"><div><img id="video-cover" src="__POSTER__" alt="仿真录像封面" style="width:100%;max-height:70vh;object-fit:contain;border-radius:10px"><video id="video" hidden playsinline muted preload="none"></video>
<div class="media-controls"><button id="preview">播放旁观视角</button><button id="left-wrist" hidden>左腕视角</button><button id="right-wrist" hidden>右腕视角</button><button id="full">完整多视角</button><a id="full-link">打开完整视频 ↗</a></div>
<div class="media-status" id="media-status">点击播放，或选择右侧动作定位录像。</div>
<details><summary>录像信息</summary><pre id="recording"></pre></details></div>
<div class="panel"><b>工具调用与动作阶段</b><div id="calls" class="calls"></div>
<div id="phases"></div><pre id="detail">选择右侧动作以查看参数和物理验证结果。</pre></div></div>
<details><summary>模型陈述（不作为物理成功证据）</summary><div id="messages"></div></details>
<script id="data" type="application/json">__DATA__</script>
<script>
const data=JSON.parse(document.getElementById('data').textContent),v=document.getElementById('video');
const tag=(label,value)=>{const e=document.createElement('span');e.className='tag';e.textContent=label+'：'+value;document.getElementById('meta').append(e)};
tag('任务',data.config.task||'未知');tag('模型',data.diagnostic_scope?'未调用模型（相机诊断）':data.controller?.model||'参见模型记录');
tag('IK',data.config.ik_backend||'custom_dls（历史）');
tag('推理强度',data.controller?.model_reasoning_effort||'参见模型记录');tag('模式',data.config.grasping_mode||'参见运行配置');
tag('官方完整任务',data.task_success===true?'成功':data.task_success===false?'未成功':'未取得评分');
tag('运行状态',data.status||'进行中');
if(data.diagnostic_scope){
 tag('验证类型','脚本化腕部相机验证');
 tag('相机验收',data.sensor_validation?.status==='passed'?'通过':'参见验证记录');
 document.getElementById('intro-note').textContent='本页是脚本化相机专项验证：通过真实 MCP 移动手臂、开合夹爪，检查六路 RGB、腕部随动和同步采集。未调用模型，不计作自主抓取或原场景完整任务成功。可切换左右腕部、旁观和完整七视角录像。';
}
if(data.trial_scope){
 const check=data.engineering_validation;
 tag('本次工程子目标',data.trial_scope.label+' · '+(check?.success===true?'成功':check?.success===false?'未成功':'未取得验证'));
 const lift=check?.final_lift_m,proof=check?.stable_hold_evidence;
 let note='本次只验证单瓶抓取、抬升和稳持，未要求完成原场景的全部分类。工程子目标与官方完整任务评分分别计算。';
 if(typeof lift==='number')note+=' 最终抬升 '+(lift*100).toFixed(1)+' cm。';
 if(proof)note+=' 连续 '+proof.control_steps+' 个控制步内，瓶子相对夹爪的最大位移为 '+(proof.maximum_relative_position_drift_m*1000).toFixed(2)+' mm。';
 const upright=check?.upright_validation;
 if(upright){
  tag('直立验收',upright.success?'通过':'未通过');
  note+=' 最终上身倾斜 '+upright.final_upper_body_tilt_deg.toFixed(2)+'°，躯干高度较初始下降 '+(upright.final_height_drop_from_initial_m*100).toFixed(1)+' cm；末尾要求连续 '+upright.required_final_hold_control_steps+' 个控制步持瓶直立。';
 }
 document.getElementById('scope-note').textContent=note;
}
document.getElementById('recording').textContent='录像：'+JSON.stringify(data.video,null,2)+'\\n场景准备：'+JSON.stringify(data.scene_preparation||{},null,2);
let mediaMode=null,seekTarget=0,playWhenReady=false;
const status=document.getElementById('media-status');
function seekReady(){
 if(v.readyState<1)return;
 document.getElementById('video-cover').hidden=true;v.hidden=false;v.controls=true;
 v.currentTime=Math.max(0,Math.min(seekTarget,Math.max(0,v.duration-.001)));
 if(playWhenReady)v.play().catch(()=>{status.textContent='已加载，点击播放器播放。'});
}
function useMedia(mode,time=0,play=false){
 seekTarget=time;playWhenReady=play;
 const source=data.media[mode];
 status.textContent=source.label+' · '+(source.bytes/1024/1024).toFixed(2)+' MB';
 if(mediaMode!==mode){
  mediaMode=mode;v.style.aspectRatio=mode==='full'?(data.video.width||1024)+'/'+(data.video.height||1616):'1/1';
  v.preload='metadata';v.src=source.file;v.load();
 }else seekReady();
}
v.addEventListener('loadedmetadata',seekReady);
v.addEventListener('error',()=>{status.textContent='视频加载失败，请刷新或使用完整视频链接。'});
document.getElementById('preview').onclick=()=>useMedia('preview',v.currentTime||0,true);
document.getElementById('full').onclick=()=>useMedia('full',v.currentTime||0,mediaMode===null||!v.paused);
for(const view of ['left_wrist','right_wrist']){
 const b=document.getElementById(view.replace('_','-'));
 if(data.media[view]){b.hidden=false;b.onclick=()=>useMedia(view,v.currentTime||0,mediaMode===null||!v.paused)}
}
document.getElementById('full').textContent='完整 '+(data.video.views?.length||5)+' 视角';
document.getElementById('full-link').href=data.media.full.file;
data.calls.forEach((c,i)=>{const b=document.createElement('button'),r=c.result||{},ex=r.execution||{};
 const state=ex.verified===true?'物理结果已验证':ex.verified===false?'物理验证未通过':r.ok===false?'执行失败':r.ok===true?'调用已返回':'无返回记录';
 b.textContent=(i+1)+'. '+c.name+' · '+state;b.className=ex.verified===true?'good':r.ok===false?'bad':'unknown';
 b.onclick=()=>{document.querySelectorAll('#calls button').forEach(x=>x.classList.remove('active'));b.classList.add('active');
 if(c.seconds!==null)useMedia(mediaMode||'preview',c.seconds,!v.paused);
 document.getElementById('detail').textContent=JSON.stringify(c,null,2);
 const phases=document.getElementById('phases');phases.replaceChildren();
 c.phases.forEach(p=>{const x=document.createElement('button');x.textContent='↳ '+p.phase+' @ '+p.seconds.toFixed(2)+'s';x.onclick=()=>useMedia(mediaMode||'preview',p.seconds,!v.paused);phases.append(x)})};
 document.getElementById('calls').append(b)});
const query=new URLSearchParams(location.search),requestedView=query.get('view');
if(['preview','full','left_wrist','right_wrist'].includes(requestedView)&&data.media[requestedView])useMedia(requestedView,0,false);
const requested=Number(query.get('step'));
if(Number.isInteger(requested)&&requested>0&&requested<=data.calls.length){
 document.querySelectorAll('#calls button')[requested-1].click();
}
data.messages.forEach(m=>{const e=document.createElement('div');e.className='message';e.textContent=m;document.getElementById('messages').append(e)});
</script></html>"""


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",type=Path,action="append",required=True)
    parser.add_argument("--index",type=Path,help="Optional combined index for the selected runs")
    args=parser.parse_args()
    roots=[path.resolve() for path in args.run]
    for root in roots:
        print(build(root))
    if args.index:
        print(build_index(roots,args.index.resolve()))
