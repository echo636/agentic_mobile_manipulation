/* The complete trace is the default view. Video is initialized on demand. */
(() => {
'use strict';
const $=id=>document.getElementById(id),data=JSON.parse($('replay-data').textContent);
const params=new URLSearchParams(location.search);if(params.has('embed'))document.body.classList.add('embedded');
const title=(data.config?.task||data.run_id||'Episode').replaceAll('_',' ');
$('title').textContent=title;$('instruction').textContent=data.instruction||'未记录任务指令';
const success=data.evaluation_offline_only?.task_success;
$('evaluation-badge').textContent=success===true?'独立评分：成功':success===false?'独立评分：未完成':'未取得最终评分';
$('evaluation-badge').classList.add(success===true?'passed':success===false?'failed':'unknown');
let mode='trace';
function activate(next){
 mode=next==='video'?'video':'trace';document.body.classList.toggle('trace-mode',mode==='trace');
 $('trace-workspace').hidden=mode!=='trace';$('video-workspace').hidden=mode!=='video';
 $('view-trace').setAttribute('aria-pressed',String(mode==='trace'));$('view-video').setAttribute('aria-pressed',String(mode==='video'));
 $('trace-stats').hidden=mode!=='trace';$('trace-top').hidden=mode!=='trace';
 if(mode==='video')window.startReplayPlayer();
 else if(window.replayPlayerStarted){const play=$('video-play');if(play.textContent.includes('暂停'))play.click();$('episode-video').pause();}
 const url=new URL(location.href);if(mode==='video')url.searchParams.set('view','video');else{url.searchParams.delete('view');url.hash='';}if(location.protocol!=='about:')history.replaceState(null,'',url);
 window.dispatchEvent(new Event('resize'));
}
function replay(number){activate('video');const i=data.steps.findIndex(s=>s.index===number);if(i>=0){$('step-select').value=String(i);$('step-select').dispatchEvent(new Event('change'));}}
function showImage(item,context={}){
 const url=context.url||item.file;if(!url)return;
 const dialog=$('trace-image-dialog'),img=$('trace-image-full'),marker=$('trace-image-marker');
 img.src=url;$('trace-image-title').textContent=(item.view||'RGB')+(item.image_ref?' · '+item.image_ref:'');
 const point=context.target?.point||context.target;
 marker.hidden=!(Array.isArray(point)&&point.length>=2);
 if(!marker.hidden){marker.style.left=(point[0]*100)+'%';marker.style.top=(point[1]*100)+'%';}
 $('trace-image-note').textContent=marker.hidden?'实际工具返回的图像附件':'模型在此前观测中的选点；标记不代表机器人最终停靠位置。';
 dialog.showModal();
}
const trace=new ReplayToolTrace($('tool-trace-feed'),data,{onReplay:replay,onImage:showImage});
const entries=data.tool_trace||data.model_transcript||[];
$('trace-stats').textContent=entries.filter(e=>['assistant','provider_summary'].includes(e.kind)).length+' 条模型输出 · '+entries.filter(e=>e.kind==='tool_call').length+' 次工具调用 · '+entries.reduce((n,e)=>n+(e.images?.length||0),0)+' 张附件';
$('view-trace').onclick=()=>activate('trace');$('view-video').onclick=()=>activate('video');
$('trace-top').onclick=()=>{$('tool-trace-feed').scrollTo({top:0,behavior:'auto'});if(matchMedia('(max-width:700px)').matches)window.scrollTo(0,0)};
$('trace-image-close').onclick=()=>$('trace-image-dialog').close();
$('trace-image-dialog').addEventListener('click',e=>{if(e.target===$('trace-image-dialog'))$('trace-image-dialog').close()});
activate(params.get('view')==='video'||/^#step=\d+$/.test(location.hash)?'video':'trace');
})();
