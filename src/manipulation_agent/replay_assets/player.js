window.startReplayPlayer = function () {
if(window.replayPlayerStarted)return;window.replayPlayerStarted=true;
'use strict';
const $=id=>document.getElementById(id),D=JSON.parse($('replay-data').textContent),video=$('episode-video');
// Missing model transport records must not hide simulator tool failures.
if(!(D.model_transcript||[]).length&&D.steps.length){
 D.model_transcript=[];for(const s of D.steps){
  const base={step:s.index,tool:s.tool,call_id:s.request_id,source:'events.jsonl',source_id:s.event_id};
  D.model_transcript.push({...base,kind:'tool_call',sequence:D.model_transcript.length+1,arguments:s.arguments});
  if(s.result)D.model_transcript.push({...base,kind:'tool_result',sequence:D.model_transcript.length+1,text_blocks:[JSON.stringify(s.result)],image_count:s.result.observation?.images?.length||0});
 }
}
D.review_timeline=ReplayTiming.review(D);
const embedded=new URLSearchParams(location.search).has('embed');if(embedded)document.body.classList.add('embedded');
const views=['front','back','left','right','spectator'],names={front:'前视 RGB',back:'后视 RGB',left:'左视 RGB',right:'右视 RGB',spectator:'第三人称 · 回放'};
const tiles=new Map(),imageCache=new Map(),chips=new Map();
let mode='execution',index=-1,phase='initial',zoomView=null,state=null,pendingIndex=-1,loading=false,primary='front';
let edition='review',record=null,reviewTime=0,reviewPlaying=false,reviewMediaKey=null,lastTick=performance.now(),lastPaint=-1;
const text=(id,value)=>{if($(id))$(id).textContent=value??'—'},clock=s=>Number.isFinite(s)?Math.floor(s/60)+':'+String(Math.floor(s%60)).padStart(2,'0'):'—';
const step=()=>D.steps[index],action=s=>s?.arguments?.primitive||s?.tool||'等待记录';
const phaseLabels={initial:'初始画面',decision:'模型输出',execution:'执行中',tool:'执行中',result:'工具返回',final:'结束输出',evaluation:'离线评分',unmapped:'未标注区间',outside_tool_wait:'调用间等待'};
const isPlaying=()=>edition==='review'?reviewPlaying:!video.paused;
const showAll=()=>$('show-all')?.checked||false;
function playbackLabel(){text('video-play',isPlaying()?'Ⅱ 暂停':'▶ 播放')}
function pause(){reviewPlaying=false;video.pause();playbackLabel()}
const conversation=new ReplayTranscript($('conversation'),$('follow'),number=>selectStep(D.steps.findIndex(s=>s.index===number),true),pause);conversation.setData(D);
const navigationTarget=new ReplayNavigationTarget($('navigation-target'),()=>{pause();zoomView='target';text('zoom-title',navigationTarget.title.textContent+' · 调用前原图');paintZoom();$('zoom').showModal()});
function setPrimary(name){primary=name;for(const [view,t] of tiles){t.tile.classList.toggle('camera-primary',view===name);t.tile.setAttribute('aria-label',names[view]+(view===name?'，点击放大':'，点击设为主画面'))}}
for(const name of views){
 const tile=document.createElement('figure');tile.className='camera-tile camera-'+name;tile.tabIndex=0;tile.setAttribute('role','button');
 const canvas=document.createElement('canvas');canvas.width=512;canvas.height=512;canvas.setAttribute('aria-label',names[name]);
 const caption=document.createElement('figcaption');caption.textContent=names[name];
 const empty=document.createElement('span');empty.className='camera-empty';empty.textContent=name==='spectator'?'无第三人称录像':'等待观测';
 tile.append(canvas,caption,empty);$('camera-grid').append(tile);tiles.set(name,{tile,canvas,empty});
 tile.onclick=()=>{if(primary!==name){setPrimary(name);return}if(!tile.classList.contains('has-image'))return;zoomView=name;text('zoom-title',names[name]);paintZoom();$('zoom').showModal()};
 tile.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();tile.click()}};
}
setPrimary('front');
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
 const i=(D.video?.views||[]).indexOf(name),size=D.video?.width/2;if(i<0||!size)return null;
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
   else blank(name,record?.file?'读取录像…':name==='spectator'?'没有第三人称录像':'没有连续录像');
  }
 }
 if(zoomView&&$('zoom').open)paintZoom();
}
function updateObservationNote(){const obs=observation();text('observation-note',mode==='execution'?'实际运行录像 · 第三人称仅供回放 · 点击缩略图切换主画面':obs?'模型已收到 RGB · revision '+obs.revision+(obs.capture?' · '+obs.capture.captured_at.slice(11,19)+' UTC':''):'此时尚未通过工具向模型返回 RGB')}
function setMode(value){mode=value;$('mode-execution').setAttribute('aria-pressed',String(mode==='execution'));$('mode-input').setAttribute('aria-pressed',String(mode==='input'));$('input-side').hidden=mode!=='input';updateObservationNote();paintCameras()}
$('mode-execution').onclick=()=>setMode('execution');$('mode-input').onclick=()=>setMode('input');$('input-side').disabled=true;$('input-side').title='随当前阶段切换，避免显示尚未收到的观测';
const phaseTitle=document.createElement('span'),phaseLabel=document.createElement('span'),phaseProgress=document.createElement('progress');
phaseLabel.className='phase';phaseProgress.id='phase-progress';phaseProgress.max=1;phaseProgress.setAttribute('aria-label','当前阶段进度');$('current-step').append(phaseTitle,phaseLabel,phaseProgress);
function applyState(next){
 const changed=state?.key!==next.key;state=next;index=state.index;phase=state.phase;
 if(changed){
  const s=step();$('step-select').value=String(index);$('prev').disabled=index<=0;$('next').disabled=!D.steps.length||index>=D.steps.length-1;
  phaseTitle.textContent=s?String(s.index).padStart(2,'0')+' / '+D.steps.length+'  ·  '+action(s):phaseLabels[phase]||'尚未调用工具';
  $('input-side').value=state.side;conversation.setActive(state,ReplayTiming.focus(D,edition,state),showAll());
  for(const [i,chip] of chips){chip.classList.toggle('is-current',i===index);chip.classList.toggle('is-past',i<index||phase==='final');chip.setAttribute('aria-current',i===index?'step':'false')}
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
 const time=edition==='review'?reviewTime:video.currentTime,duration=edition==='review'?record.duration_seconds:video.duration;
 $('video-seek').value=time;text('video-time',clock(time)+' / '+clock(duration));applyState(ReplayTiming.locate(D,edition,time));
}
function syncReviewMedia(){
 if(edition!=='review'||!record?.file||video.readyState<1||!state?.segment)return;
 const s=state.segment,moving=s.media_end>s.media_start+.1;
 if(reviewMediaKey!==state.key){
  reviewMediaKey=state.key;video.pause();
  const target=s.media_start+(moving?state.elapsed*(s.media_end-s.media_start)/(s.end_seconds-s.start_seconds):0),at=Math.max(0,Math.min(target,Math.max(0,video.duration-.001)));
  if(Math.abs(video.currentTime-at)>.015)video.currentTime=at;
 }
 if(reviewPlaying&&moving&&!video.seeking){video.playbackRate=Number($('video-speed').value)*(s.media_end-s.media_start)/(s.end_seconds-s.start_seconds);if(video.paused)video.play().catch(()=>{})}
 else if(!moving&&!video.paused)video.pause();
}
function seekTo(time){
 if(!record||!Number.isFinite(time))return;
 pause();conversation.resumeFollow();
 if(edition==='review'){reviewTime=Math.max(0,Math.min(time,record.duration_seconds));reviewMediaKey=null;synchronize();syncReviewMedia()}
 else{video.currentTime=Math.max(0,Math.min(time,Number.isFinite(video.duration)?Math.max(0,video.duration-.001):time));synchronize()}
}
function selectStep(i,seek){
 if(i< -1||i>=D.steps.length)return;
 if(loading){pendingIndex=i;return}
 if(record){const time=i<0?0:ReplayTiming.seekTime(D,edition,i);if(time!=null&&seek)seekTo(time);else synchronize()}
 else{conversation.resumeFollow();applyState({index:i,step:D.steps[i]?.index??null,phase:i<0?'initial':'result',side:'after',key:'archive:'+i,time:0,duration:0,elapsed:0,page:null,pages:null})}
 if(!embedded)history.replaceState(null,'',i<0?'#start':'#step='+D.steps[i].index);
}
function chooseEdition(){
 const desired=loading?pendingIndex:index;pause();loading=true;pendingIndex=desired;state=null;conversation.key=null;reviewMediaKey=null;
 edition=$('video-edition').value;record=edition==='review'?D.review_timeline:edition==='inspection'?D.inspection_video:edition==='walltime'?D.walltime_video:D.video;
 if(!record||record.status!=='passed')record=null;
 $('video-play').disabled=!record;$('video-seek').disabled=!record;$('video-download').hidden=!record?.file;$('video-error').hidden=true;
 if(!record){video.removeAttribute('src');video.load();loading=false;text('time-note','未归档此模式 · 按步骤查看模型输入');setMode('input');selectStep(desired,false);return}
 text('time-note',edition==='review'?'事件回放：含阅读停留，非模型真实速度；执行沿用原录像':edition==='inspection'?'详读录像：含阅读停留，非逐 token 时间':edition==='walltime'?'真实耗时：保留等待；工具内帧时间为估计':'仿真时间：省略模型等待，同一时刻的工具会合并；逐步查看请用事件回放');
 if(!record.file){video.removeAttribute('src');video.load();loading=false;$('video-seek').max=record.duration_seconds;setMode('input');selectStep(desired,true);return}
 $('video-download').href=edition==='review'?D.video.file:record.file;text('video-download',edition==='review'?'下载原始录像 ↗':'下载此录像 ↗');
 const source=record.playback_file||record.file;
 if(video.getAttribute('src')===source&&video.readyState>=1){loading=false;$('video-seek').max=edition==='review'?record.duration_seconds:video.duration;selectStep(desired,true);return}
 video.src=source;video.load();video.playbackRate=Number($('video-speed').value);
}
video.addEventListener('loadedmetadata',()=>{if(!record)return;loading=false;$('video-seek').max=edition==='review'?record.duration_seconds:video.duration;selectStep(pendingIndex,true);synchronize();syncReviewMedia()});
if(!$('video-edition').querySelector('[value="review"]')){const option=document.createElement('option');option.value='review';option.textContent='事件回放 · 推荐';$('video-edition').prepend(option)}
$('video-edition').value=edition;for(const [name,key] of [['inspection','inspection_video'],['raw','video'],['walltime','walltime_video']]){const option=$('video-edition').querySelector('[value="'+name+'"]');if(option)option.disabled=D[key]?.status!=='passed'}
$('video-edition').onchange=chooseEdition;$('video-speed').onchange=()=>{video.playbackRate=Number($('video-speed').value)};
$('video-play').onclick=()=>{
 if(isPlaying()){pause();return}conversation.resumeFollow();
 if(edition==='review'){if(reviewTime>=record.duration_seconds)seekTo(0);reviewPlaying=true;lastTick=performance.now();synchronize();syncReviewMedia();playbackLabel()}
 else video.play().catch(e=>{if(e.name!=='AbortError'){$('video-error').hidden=false;text('video-error','视频播放失败：'+e.message)}});
};
$('video-seek').oninput=()=>seekTo(Number($('video-seek').value));
$('prev').onclick=()=>selectStep(index-1,true);$('next').onclick=()=>selectStep(index+1,true);$('step-select').onchange=()=>selectStep(Number($('step-select').value),true);
const initialOption=document.createElement('option');initialOption.value='-1';initialOption.textContent='开始 / 结束画面';$('step-select').append(initialOption);
for(const [i,s] of D.steps.entries()){
 const option=document.createElement('option');option.value=i;option.textContent=String(s.index).padStart(2,'0')+' · '+action(s);$('step-select').append(option);
 if($('step-track')){const chip=document.createElement('button');chip.textContent=String(s.index).padStart(2,'0');chip.title=action(s);chip.setAttribute('aria-label','第 '+s.index+' 步 '+action(s));chip.onclick=()=>selectStep(i,true);$('step-track').append(chip);chips.set(i,chip)}
}
$('step-select').disabled=!D.steps.length;
if($('show-all'))$('show-all').onchange=()=>{conversation.key=null;conversation.setActive(state,ReplayTiming.focus(D,edition,state),showAll())};
if($('fullscreen'))$('fullscreen').onclick=()=>{const panel=document.querySelector('.replay-workspace');if(document.fullscreenElement)document.exitFullscreen();else panel?.requestFullscreen?.().catch(()=>{})};
document.addEventListener('fullscreenchange',()=>{text('fullscreen',document.fullscreenElement?'退出全屏':'全屏')});
for(const name of ['play','pause','ended'])video.addEventListener(name,()=>{playbackLabel();if(edition!=='review'&&name==='play')conversation.resumeFollow();synchronize()});
video.addEventListener('error',()=>{if(record?.file){pause();$('video-error').hidden=false;text('video-error','录像读取失败；可切换模型输入查看归档图像。')}});
for(const event of ['timeupdate','seeking'])video.addEventListener(event,synchronize);
for(const event of ['loadeddata','seeked'])video.addEventListener(event,()=>{synchronize();paintCameras();syncReviewMedia()});
function frame(now){
 const dt=Math.min(.25,Math.max(0,(now-lastTick)/1000));lastTick=now;
 if(edition==='review'&&record&&!loading){
  if(reviewPlaying&&state?.segment){
   const s=state.segment,moving=record.file&&s.media_end>s.media_start+.1;
   if(moving){
    if(!video.seeking&&video.readyState>=2){
     const elapsed=Math.max(0,video.currentTime-s.media_start)*(s.end_seconds-s.start_seconds)/(s.media_end-s.media_start);
     reviewTime=video.ended||video.currentTime>=s.media_end-.012?s.end_seconds:Math.min(s.end_seconds,s.start_seconds+elapsed);
    }
   }else if(!video.seeking)reviewTime=Math.min(s.end_seconds,reviewTime+dt*Number($('video-speed').value));
   if(reviewTime>=record.duration_seconds){reviewTime=record.duration_seconds;pause()}
  }
  synchronize();syncReviewMedia();
 }
 if(video.currentTime!==lastPaint&&!video.seeking){lastPaint=video.currentTime;paintCameras()}
 requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA','BUTTON','SUMMARY'].includes(e.target.tagName)||$('zoom').open)return;if(e.key==='ArrowRight'){e.preventDefault();selectStep(index+1,true)}if(e.key==='ArrowLeft'){e.preventDefault();selectStep(index-1,true)}if(e.code==='Space'){e.preventDefault();$('video-play').click()}});
window.addEventListener('hashchange',()=>{const match=location.hash.match(/^#step=(\d+)$/);if(match)selectStep(Number(match[1])-1,true)});
const evaluation=D.evaluation_offline_only||{},success=evaluation.task_success;
text('title',(D.config.task||D.run_id).replaceAll('_',' '));text('instruction',D.instruction||'未记录任务指令');
text('evaluation-badge',success===true?'独立评分：成功':success===false?'独立评分：未完成':'未取得最终评分');$('evaluation-badge').classList.add(success===true?'passed':success===false?'failed':'unknown');
const hash=location.hash.match(/^#step=(\d+)$/);
applyState(ReplayTiming.locate(D,edition,0));
index=hash?D.steps.findIndex(s=>s.index===Number(hash[1])):location.hash==='#start'||!D.steps.length?-1:0;
chooseEdition();
};
