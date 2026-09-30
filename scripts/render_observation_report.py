"""Publish recorded four-camera batches and asynchronous tool evidence as HTML."""
import argparse
from datetime import datetime,timezone
from html import escape
import json
from pathlib import Path
import shutil


def main():
    p=argparse.ArgumentParser();p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    cards=[];payload=[]
    for folder in sorted((a.batch/'runs').glob('mas_*')):
        if not (folder/'run.json').exists():continue
        run=json.loads((folder/'run.json').read_text());rid=run['run_id'];base='manipulation_runs/'+rid
        shutil.copytree(folder,a.output/base,dirs_exist_ok=True)
        captures=[json.loads(x) for x in (folder/'captures.jsonl').read_text().splitlines()] if (folder/'captures.jsonl').exists() else []
        events=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
        results={e['request_id']:e for e in events if e['kind']=='tool_result'}
        rows=[]
        for e in events:
            if e['kind']=='tool_call':
                result=results.get(e['request_id']);body=result.get('result',{}) if result else {}
                rows.append({'at':e['at'],'tool':e['name'],'arguments':e['arguments'],'result':body})
        validation=json.loads((folder/'observation_validation.json').read_text()) if (folder/'observation_validation.json').exists() else {'status':'running'}
        level='真实 LLM 任务' if str(run['config'].get('controller','')).startswith('codex:') else '脚本观测接口验证（不声称完成任务）'
        video=json.loads((folder/'video.json').read_text()) if (folder/'video.json').exists() else {}
        data={'base':base,'captures':captures,'calls':rows};idx=len(payload);payload.append(data)
        options=''.join(f'<option value="{i}">采集 {c["capture"]} · step {c["env_steps"]}</option>' for i,c in enumerate(captures))
        checkrows=''.join(f'<tr><td>{escape(k)}</td><td>{v}</td></tr>' for k,v in validation.get('checks',{}).items())
        lifecycle=''.join(f'<tr><td>{escape(e["at"])}</td><td>{escape(e["kind"])}</td><td>{escape(e["job"]["job_id"])}</td><td>{escape(e["job"]["status"])}</td></tr>' for e in events if e['kind'].startswith('observation_') and 'job' in e)
        task='通过' if run.get('task_success') else '未完成 / 本次未尝试'
        cards.append(f'''<article class="card" data-run="{idx}"><h2>{escape(rid)}</h2><p>{level} · 观测验证：<strong>{validation['status']}</strong> · 独立任务：{task}</p><label>同一时刻的四方向图像 <select class="capture">{options}</select></label><p class="capture-meta"></p><div class="camera-grid"></div><div class="links"><a href="{base}/replay.html">逐步 Replay / 原始公开模型消息</a><a href="{base}/run.json">Run</a><a href="{base}/observation_validation.json">观测验证</a></div>{f'<video controls preload="metadata" src="{base}/episode.mp4"></video>' if video.get('status')=='passed' else ''}<details><summary>异步任务生命周期：提交返回之后才开始采集</summary><table><tr><th>时间</th><th>事件</th><th>任务</th><th>状态</th></tr>{lifecycle}</table></details><details><summary>精确工具调用与返回</summary><select class="call"></select><pre class="call-data"></pre></details><details><summary>观测证据检查</summary><table>{checkrows}</table></details><details><summary>源码与运行环境</summary><pre>{escape(json.dumps({k:run.get(k) for k in ['source','host','interpreter','gpu_uuid','unit','pid','backend']},ensure_ascii=False,indent=2))}</pre></details></article>''')
    val=json.loads((a.batch/'validation.json').read_text())
    evidence=a.output/'evidence'/a.batch.name;evidence.mkdir(parents=True,exist_ok=True)
    for name in ('journal.md','validation.json','publication.json','preflight_s134_r1.txt','cpu_r1.log'):
        f=a.batch/name
        if f.exists():shutil.copy2(f,evidence/name)
    encoded=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>四相机异步 RGB 观测</title><style>*{box-sizing:border-box}body{margin:0;background:#eef3f6;color:#193248;font:16px/1.65 system-ui}header{background:#153444;color:white;padding:28px max(18px,calc((100vw - 1380px)/2))}main{max-width:1420px;margin:auto;padding:20px}.card{padding:24px;background:white;border:1px solid #d5e0e6;border-radius:12px;margin:22px 0}.notice{padding:18px;border-left:4px solid #198481;background:#dbefed}.camera-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}figure{margin:0}figure img{width:100%;display:block}figcaption{font-weight:700}small{display:block;font-weight:400;overflow-wrap:anywhere}.links{display:flex;gap:18px;flex-wrap:wrap;margin:18px 0}a{color:#08768a}header a{color:#8cddd1}video{width:100%;max-height:70vh;background:#10212d}select{max-width:100%;padding:8px}table{width:100%;border-collapse:collapse}td,th{padding:8px;border:1px solid #d7e1e6;text-align:left;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:600px;overflow:auto}details{margin:16px 0}summary{cursor:pointer;font-weight:600}.capture-meta{font-size:13px;overflow-wrap:anywhere}@media(max-width:850px){.camera-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:14px}table{display:block;overflow:auto}}</style><header><h1>四个固定相机 · 异步获取 RGB</h1><p>前、后、左、右，同一个仿真状态。采集不转动机器人，不推进物理仿真。</p><a href="https://github.com/echo636/agentic_mobile_manipulation">代码仓库</a> · <a href="skills_replay.html">此前三相机实验</a></header><main><p class="notice">start_observation 立即返回任务 ID；get_observation 获取进度或四张真实 RGB；cancel_observation 取消待处理任务。四相机在同一 render barrier 后读取，共享 capture_id、采集时间和 sim_step。大脑不接收腕部、深度、坐标或任务真值。</p>'''+''.join(cards)+f'''<article class="card"><h2>批次记录</h2><pre>{escape(json.dumps(val,ensure_ascii=False,indent=2))}</pre><a href="evidence/{a.batch.name}/journal.md">完整迭代与故障记录</a></article><p>生成 {datetime.now(timezone.utc).isoformat()}</p></main><script id="data" type="application/json">{encoded}</script>'''+'''<script>const D=JSON.parse(document.getElementById('data').textContent),names={front:'前',back:'后',left:'左',right:'右'};document.querySelectorAll('[data-run]').forEach(card=>{const d=D[+card.dataset.run],sel=card.querySelector('.capture'),grid=card.querySelector('.camera-grid');function show(){const c=d.captures[+sel.value];if(!c)return;grid.replaceChildren();const batch=c.synchronized_capture;card.querySelector('.capture-meta').textContent=batch?`${batch.capture_id} · ${batch.captured_at} · sim_step=${batch.sim_step} · 四个方向属于同一批采集`:'历史采集';for(const direction of ['front','back','left','right']){const image=c.images.find(i=>i.image_ref.endsWith('-'+direction));if(!image)continue;const fig=document.createElement('figure'),cap=document.createElement('figcaption'),img=document.createElement('img'),meta=document.createElement('small');cap.textContent=names[direction]+' / '+direction;meta.textContent=image.image_ref;img.src=d.base+'/'+image.file;img.alt=direction+' RGB';cap.append(meta);fig.append(cap,img);grid.append(fig)}}sel.onchange=show;show();const calls=card.querySelector('.call'),out=card.querySelector('.call-data');d.calls.forEach((c,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`${i+1}. ${c.tool} · ${c.at}`;calls.append(o)});calls.onchange=()=>out.textContent=JSON.stringify(d.calls[+calls.value],null,2);calls.onchange()});</script></html>'''
    (a.output/'surround_observation.html').write_text(page)
    print(f'Published {len(cards)} runs to {a.output}/surround_observation.html')


if __name__=='__main__': main()
