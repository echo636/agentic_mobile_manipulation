(() => {
'use strict';
const $=id=>document.getElementById(id),D=JSON.parse($('replay-data').textContent),video=$('episode-video');
const embedded=new URLSearchParams(location.search).has('embed');if(embedded)document.body.classList.add('embedded');
const views=['front','back','left','right','spectator'],names={front:'前视 RGB',back:'后视 RGB',left:'左视 RGB',right:'右视 RGB',spectator:'第三人称 · 回放'};
const tiles=new Map(),imageCache=new Map();let mode='execution',index=-1,phase='initial',zoomView=null,state=null,pendingIndex=-1,loading=false;
let edition=D.inspection_video?.status==='passed'?'inspection':'raw',record=null;
const text=(id,value)=>$(id).textContent=value??'—',clock=s=>Number.isFinite(s)?Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0'):'—';
const step=()=>D.steps[index],action=s=>s?.arguments?.primitive||s?.tool||'等待记录';
const phaseLabels={initial:'初始画面',decision:'模型决策',execution:'执行中',tool:'执行中',result:'工具返回',final:'结束输出',evaluation:'离线评分',unmapped:'未标注区间',outside_tool_wait:'调用间等待'};
const conversation=new ReplayTranscript($('conversation'),$('follow'),number=>selectStep(D.steps.findIndex(s=>s.index===number),true),()=>video.pause());conversation.setData(D);
const navigationTarget=new ReplayNavigationTarget($('navigation-target'),()=>{video.pause();zoomView='target';text('zoom-title',navigationTarget.title.textContent+' · 调用前原图');paintZoom();$('zoom').showModal()});
for(const name of views){
 const tile=document.createElement('figure');tile.className='camera-tile camera-'+name;
 const canvas=document.createElement('canvas');canvas.width=512;canvas.height=512;canvas.setAttribute('aria-label',names[name]);
 const caption=document.createElement('figcaption');caption.textContent=names[name];
 const empty=document.createElement('span');empty.className='camera-empty';empty.textContent=name==='spectator'?'无第三人称录像':'等待观测';
 tile.append(canvas,caption,empty);$('camera-grid').append(tile);tiles.set(name,{tile,canvas,empty});
 tile.onclick=()=>{if(!tile.classList.contains('has-image'))return;zoomView=name;text('zoom-title',names[name]);paintZoom();$('zoom').showModal()};
}
function paintZoom(){if(!zoomView)return;const source=zoomView==='target'?navigationTarget.canvas:tiles.get(zoomView).canvas,target=$('zoom-canvas');if(target.width!==source.width||target.height!==source.height){target.width=source.width;target.height=source.height}target.getContext('2d').drawImage(source,0,0)}
$('close-zoom').onclick=()=>{$('zoom').close();zoomView=null};
function blank(name,message){const t=tiles.get(name);t.tile.classList.remove('has-image');t.empty.textContent=message;t.canvas.getContext('2d').clearRect(0,0,t.canvas.width,t.canvas.height);delete t.canvas.dataset.imageRef}
function paint(name,source,x,y,w,h,imageRef){
 const t=tiles.get(name);if(t.canvas.width!==w||t.canvas.height!==h){t.canvas.width=w;t.canvas.height=h}
 t.canvas.getContext('2d').drawImage(source,x,y,w,h,0,0,w,h);t.tile.classList.add('has-image');
 if(imageRef)t.canvas.dataset.imageRef=imageRef;else delete t.canvas.dataset.imageRef;
 if(imageRef)navigationTarget.mark(t.canvas,imageRef);
}
function cameraBox(name){
 if(edition==='inspection'){const box=record?.camera_boxes?.[name];return box?[box[0],box[1]+26,box[2],box[2]-26]:null}
 const i=(D.video?.views||[]).indexOf(name),size=D.video?.width/2;
 if(i<0||!size)return null;
 if(edition==='walltime'){
  const scale=Math.min(1000/D.video.width,1078/D.video.height),w=Math.round(D.video.width*scale),h=Math.round(D.video.height*scale),sx=w/D.video.width,sy=h/D.video.height;
  return [12+Math.floor((1000-w)/2)+i%2*size*sx,1+Math.floor((1078-h)/2)+(Math.floor(i/2)*size+26)*sy,size*sx,(size-26)*sy].map(Math.round);
 }
 return [i%2*size,Math.floor(i/2)*size+26,size,size-26];
}
function observation(){const s=step()||(['final','evaluation'].includes(phase)?D.steps.at(-1):null);return s?.[$('input-side').value]}
function paintCameras(){
 const obs=observation();
 for(const name of views){
  if(mode==='input'&&name!=='spectator'){
   const item=obs?.images?.find(im=>im.view===name);
   if(!item?.file){blank(name,obs?'此视角未归档':'此时尚未收到 RGB');continue}
   let image=imageCache.get(item.file);
   if(!image){image=new Image();image.onload=paintCameras;image.onerror=()=>{image.dataset.failed='true';paintCameras()};image.src=item.file;imageCache.set(item.file,image);if(imageCache.size>64)imageCache.delete(imageCache.keys().next().value)}
   if(image.complete&&image.naturalWidth)paint(name,image,0,0,image.naturalWidth,image.naturalHeight,item.image_ref);
   else blank(name,image.dataset.failed?'图像读取失败':'读取 RGB…');
  }else{
   const box=cameraBox(name);
   if(video.readyState>=2&&box)paint(name,video,...box);
   else blank(name,record?'读取录像…':name==='spectator'?'没有第三人称录像':'没有连续录像');
  }
 }
 if(zoomView&&$('zoom').open)paintZoom();
}
function updateObservationNote(){
 const obs=observation();
 text('observation-note',mode==='execution'?'运行时录制的连续画面 · 第三人称仅供回放':obs?'模型已收到 RGB · revision '+obs.revision+(obs.capture?' · '+obs.capture.captured_at.slice(11,19)+' UTC':''):'此时尚未通过工具向模型返回 RGB');
}
function setMode(value){mode=value;$('mode-execution').setAttribute('aria-pressed',String(mode==='execution'));$('mode-input').setAttribute('aria-pressed',String(mode==='input'));$('input-side').hidden=mode!=='input';updateObservationNote();paintCameras()}
$('mode-execution').onclick=()=>setMode('execution');$('mode-input').onclick=()=>setMode('input');$('input-side').disabled=true;$('input-side').title='由视频当前阶段自动选择，避免显示尚未收到的观测';
const phaseTitle=document.createElement('span'),phaseLabel=document.createElement('span'),phaseProgress=document.createElement('progress');
phaseLabel.className='phase';phaseProgress.id='phase-progress';phaseProgress.max=1;phaseProgress.setAttribute('aria-label','当前阶段进度');
$('current-step').append(phaseTitle,phaseLabel,phaseProgress);
function applyState(next){
 const changed=state?.key!==next.key;state=next;index=state.index;phase=state.phase;
 if(changed){
  const s=step();$('step-select').value=String(index);$('prev').disabled=index<=0;$('next').disabled=!D.steps.length||index>=D.steps.length-1;
  phaseTitle.textContent=s?String(s.index).padStart(2,'0')+' / '+D.steps.length+'  ·  '+action(s):phaseLabels[phase]||'尚未调用工具';
  $('input-side').value=state.side;conversation.setActive(state,ReplayTiming.focus(D,edition,state));
  if(zoomView==='target'){$('zoom').close();zoomView=null}
  navigationTarget.update(s,state);updateObservationNote();paintCameras();
 }
 const paging=state.pages>1?' · '+state.page+'/'+state.pages+' 页':'';
 phaseLabel.textContent=(phaseLabels[phase]||phase)+paging+(state.duration?' · '+state.elapsed.toFixed(1)+' / '+state.duration.toFixed(1)+' s':'');
 phaseProgress.value=state.duration?state.elapsed/state.duration:0;
 $('current-step').dataset.time=state.time;$('current-step').dataset.step=state.step??'';$('current-step').dataset.phase=phase;
}
function synchronize(){
 if(loading||!record)return;
 $('video-seek').value=video.currentTime;text('video-time',clock(video.currentTime)+' / '+clock(video.duration));
 applyState(ReplayTiming.locate(D,edition,video.currentTime));
}
function seekTo(time){
 if(!record||!Number.isFinite(time))return;
 video.pause();conversation.resumeFollow();
 video.currentTime=Math.max(0,Math.min(time,Number.isFinite(video.duration)?Math.max(0,video.duration-.001):time));synchronize();
}
function selectStep(i,seek){
 if(i< -1||i>=D.steps.length)return;
 if(loading){pendingIndex=i;return}
 if(record){const time=i<0?0:ReplayTiming.seekTime(D,edition,i);if(time!=null&&seek)seekTo(time);else synchronize()}
 else {conversation.resumeFollow();applyState({index:i,step:D.steps[i]?.index??null,phase:i<0?'initial':'decision',side:'before',key:'archive:'+i,time:0,duration:0,elapsed:0,page:null,pages:null})}
 if(!embedded)history.replaceState(null,'',i<0?'#start':'#step='+D.steps[i].index);
}
function chooseEdition(){
 const desired=loading?pendingIndex:index;video.pause();loading=true;pendingIndex=desired;state=null;conversation.key=null;
 edition=$('video-edition').value;record=edition==='inspection'?D.inspection_video:edition==='walltime'?D.walltime_video:D.video;
 if(!record||record.status!=='passed')record=null;
 $('video-play').disabled=!record;$('video-seek').disabled=!record;$('video-download').hidden=!record;$('video-error').hidden=true;
 if(!record){video.removeAttribute('src');video.load();loading=false;text('time-note','未归档完整录像 · 按步骤查看原始记录');text('video-time','—');$('video-seek').value=0;setMode('input');selectStep(desired,false);return}
 video.src=record.playback_file||record.file;video.load();video.playbackRate=Number($('video-speed').value);$('video-download').href=record.file;
 text('time-note',edition==='inspection'?'按视频阶段同步；含阅读停留，非逐 token 时间':edition==='walltime'?'保留调用等待；工具内帧时间为估计':'连续仿真录像；模型等待已省略');
}
video.addEventListener('loadedmetadata',()=>{if(!record)return;loading=false;$('video-seek').max=video.duration;selectStep(pendingIndex,true);synchronize()});
$('video-edition').value=edition;$('video-edition').querySelector('[value="inspection"]').disabled=D.inspection_video?.status!=='passed';$('video-edition').querySelector('[value="raw"]').disabled=D.video?.status!=='passed';$('video-edition').querySelector('[value="walltime"]').disabled=D.walltime_video?.status!=='passed';
$('video-edition').onchange=chooseEdition;$('video-speed').onchange=()=>{video.playbackRate=Number($('video-speed').value)};
$('video-play').onclick=()=>{if(!video.paused){video.pause();return}conversation.resumeFollow();synchronize();video.play().catch(e=>{if(e.name!=='AbortError'){$('video-error').hidden=false;text('video-error','视频播放失败：'+e.message)}})};
$('video-seek').oninput=()=>seekTo(Number($('video-seek').value));
$('prev').onclick=()=>selectStep(index-1,true);$('next').onclick=()=>selectStep(index+1,true);$('step-select').onchange=()=>selectStep(Number($('step-select').value),true);
const initialOption=document.createElement('option');initialOption.value='-1';initialOption.textContent='开始 / 结束画面';$('step-select').append(initialOption);
for(const [i,s] of D.steps.entries()){const option=document.createElement('option');option.value=i;option.textContent=String(s.index).padStart(2,'0')+' · '+action(s);$('step-select').append(option)}
$('step-select').disabled=!D.steps.length;
for(const name of ['play','pause','ended'])video.addEventListener(name,()=>{text('video-play',video.paused?'▶ 播放':'Ⅱ 暂停');if(name==='play')conversation.resumeFollow();synchronize()});
video.addEventListener('error',()=>{if(record){$('video-error').hidden=false;text('video-error','录像读取失败，可继续查看模型输入与原始记录。')}});
for(const event of ['timeupdate','seeking'])video.addEventListener(event,synchronize);for(const event of ['loadeddata','seeked'])video.addEventListener(event,()=>{synchronize();paintCameras()});
if(video.requestVideoFrameCallback){const draw=()=>{synchronize();paintCameras();video.requestVideoFrameCallback(draw)};video.requestVideoFrameCallback(draw)}
else{const draw=()=>{if(!video.paused){synchronize();paintCameras()}requestAnimationFrame(draw)};requestAnimationFrame(draw)}
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA','BUTTON','SUMMARY'].includes(e.target.tagName)||$('zoom').open)return;if(e.key==='ArrowRight'){e.preventDefault();selectStep(index+1,true)}if(e.key==='ArrowLeft'){e.preventDefault();selectStep(index-1,true)}if(e.code==='Space'){e.preventDefault();$('video-play').click()}});
window.addEventListener('hashchange',()=>{const match=location.hash.match(/^#step=(\d+)$/);if(match)selectStep(Number(match[1])-1,true)});
const evaluation=D.evaluation_offline_only||{},success=evaluation.task_success;
text('title',(D.config.task||D.run_id).replaceAll('_',' '));text('instruction',D.instruction||'未记录任务指令');
text('evaluation-badge',success===true?'独立评分：成功':success===false?'独立评分：未完成':'未取得最终评分');$('evaluation-badge').classList.add(success===true?'passed':success===false?'failed':'unknown');
text('archive-summary','运行状态：'+D.status+' · 工具调用：'+D.steps.length+' · Q：'+(evaluation.evaluation?.goal_satisfaction_fraction??'未评测')+'。模型文字为已记录的原始输出与接口摘要；某一步没有新增文字时保持空缺。执行画面是运行时录像，不表示每帧都输入了模型。');
text('provenance',JSON.stringify({evaluation,source:D.source,execution:D.execution,backend:D.backend,failure:D.failure},null,2));$('public-trace-link').hidden=!D.has_public_trace;$('audit-link').hidden=!D.audit;
const hash=location.hash.match(/^#step=(\d+)$/);if(hash)index=Math.max(0,Math.min(D.steps.length-1,Number(hash[1])-1));
applyState(ReplayTiming.locate(D,edition,0));if(hash)index=D.steps.findIndex(s=>s.index===Number(hash[1]));chooseEdition();
})();
