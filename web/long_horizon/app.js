(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('task-data').textContent);
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusText = {passed:'通过',failed:'未通过',running:'运行中',planned:'待验证',blocked:'阻塞'};
  const views = {front:'前',back:'后',left:'左',right:'右'};
  const primitives = {navigate_to:'导航',grasp:'抓取',place_on_top:'放置',wipe:'擦拭去尘',spray:'喷洒',cut:'切割',sweep:'清扫',vacuum:'吸尘',toggle_on:'开启',toggle_off:'关闭',wait:'等待',look:'观察'};
  const errorText = {
    'No checked complete route among bounded endpoint candidates':'候选落脚点未找到通过碰撞检查的完整路径。',
    'No visible feasible destination in the start-connected region':'当前连通区域内没有可见、可达的目标落脚点。',
    'Navigation planning budget exhausted; select another visible approach':'导航规划耗尽搜索预算，需要验证其他接近方式。'
  };
  let current = 0, phase = 0, camera = 'front', tab = 'overview', reference = 0, action = 0, zoom = 1;
  const task = () => data.tasks[current];
  const badge = (status, label) => `<span class="tag ${escape(status)}">${escape(label || statusText[status] || status)}</span>`;
  const hashState = () => new URLSearchParams(location.hash.slice(1));
  function setHash() {
    const params = new URLSearchParams({task:task().family,view:tab,camera,phase:String(phase+1)});
    history.replaceState(null,'','#'+params.toString());
  }
  $('updated').textContent = '证据更新 '+new Date(data.updated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' CST';
  $('built-count').innerHTML = data.tasks.filter(t => t.gates[1].status==='passed' && t.gates[2].status==='passed').length+'<span> / '+data.tasks.length+'</span>';
  $('reference-count').innerHTML = data.tasks.filter(t => t.reference_status==='passed').length+'<span> / '+data.tasks.length+'</span>';
  $('pending-count').textContent = data.pending.length;
  $('batch-label').textContent = '批次 '+data.batch;
  $('task-cards').innerHTML = data.tasks.map((t,i) => {
    const frame=t.frames.find(f=>f.view===t.cover);
    return `<button class="task-card" data-task="${i}" aria-pressed="false" aria-controls="workspace" aria-label="查看${escape(t.name)}"><div class="card-cover"><img src="${escape(frame.url)}" alt="${escape(t.name)}的真实初态" width="512" height="512"><span class="card-selected" aria-hidden="true">✓</span></div><div class="card-text"><h3>${escape(t.name)}</h3><p>${escape(t.scale)}</p></div></button>`;
  }).join('');
  $('pending-list').innerHTML = data.pending.map(t=>`<article class="pending-card"><div><h3>${escape(t.name)}</h3>${badge('planned',t.calls+' 步设计')}</div><p>${escape(t.note)}</p></article>`).join('');
  $('task-cards').addEventListener('click',event=>{
    const b=event.target.closest('[data-task]');if(!b)return;
    selectTask(Number(b.dataset.task));setHash();
  });
  function selectTask(index, state) {
    current=index;phase=state ? Math.max(0,Math.min(3,Number(state.get('phase')||1)-1)) : 0;
    if(!Number.isFinite(phase))phase=0;
    camera=state && Object.hasOwn(views,state.get('camera')) ? state.get('camera') : task().cover;
    reference=Math.max(0,task().references.length-1);action=0;zoom=1;
    document.querySelectorAll('[data-task]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.task)===current)));
    const t=task();
    $('task-kicker').textContent = t.scene_name+' · 任务 '+String(index+1).padStart(2,'0')+' / 04';
    $('task-name').textContent=t.story.title;
    $('mission-intro').textContent=t.story.intro;
    $('task-badges').innerHTML=badge(t.gates[1].status,'构建'+statusText[t.gates[1].status])+badge(t.reference_status,'完整参考'+statusText[t.reference_status]);
    $('instruction').textContent=t.instruction;
    renderStory();
    $('model-budget').textContent=t.model_budget;
    $('phase-buttons').innerHTML=t.phases.map((p,i)=>`<button class="phase-button" data-phase="${i}" aria-pressed="false"><span class="phase-index">${String(i+1).padStart(2,'0')}</span><span><strong>${escape(p.title)}</strong><small>${p.calls===null?'未计入有效动作账本':p.calls+' 次设计动作'}</small></span></button>`).join('');
    $('gate-list').innerHTML=t.gates.map(g=>`<div class="gate"><span>${escape(g.name)}</span>${badge(g.status)}</div>`).join('');
    $('reference-select').innerHTML=t.references.map((r,i)=>`<option value="${i}" ${i===reference?'selected':''}>${escape(r.id)} · ${escape(statusText[r.status])}</option>`).join('');
    $('requirements').innerHTML=t.requirements.map(r=>`<li>${escape(r)}</li>`).join('');
    const provenance=[['实例',t.instance],['构建版本',t.build_id],['场景',t.scene],['源码',t.source_commit],['任务哈希',t.spec_sha256],['场景哈希',t.scene_sha256],['初态断言',t.initial_assertions+' 项'],['运行依赖',Object.entries(t.packages).map(([k,v])=>k+' '+v).join(' · ')],['测评放行','未放行（benchmark_ready=false）']];
    $('provenance').innerHTML=provenance.map(([k,v])=>`<dt>${escape(k)}</dt><dd>${escape(v)}</dd>`).join('');
    $('cpu-note').textContent='共用 CPU 契约与测评入口检查：'+data.cpu_tests+' 项通过。该结果不构成物理可解性或自主模型成功证据。';
    updateCamera();selectPhase(phase);renderReference();setTab(state?.get('view')||tab,false);
  }
  // These drawings explain desired states, never observed task completion.
  const drawings = {
    plant:'<path d="M10 20h16l-3 12H13z" fill="#b17c54"/><path d="M18 21V8m0 8C7 17 6 7 7 5c8 0 12 4 11 11Zm0-3C18 5 25 3 30 4c0 8-5 12-12 12" fill="#65916b" stroke="#3f6d49" stroke-width="1.3"/>',
    shoe:'<path d="m7 10 9 1 4 9 10 3q3 2 2 6H4V17Z" fill="#b7c8dd" stroke="#546d8a" stroke-width="1.5"/><path d="M4 27h28M17 17l5-1m-3 5 5-1" fill="none" stroke="#546d8a" stroke-width="1.5"/>',
    book:'<path d="M7 5h22v27H9q-4-2-2-5Z" fill="#c98967" stroke="#865b45" stroke-width="1.5"/><path d="M12 5v22m-5 0h22M16 12h9m-9 5h9" fill="none" stroke="#ffe4c9" stroke-width="1.5"/>',
    toy:'<path d="m5 13 13-7 13 7v15l-13 7-13-7Z" fill="#d4ad67" stroke="#987637" stroke-width="1.4"/><path d="m5 13 13 7 13-7M18 20v15" fill="none" stroke="#fff0cc" stroke-width="1.6"/>',
    log:'<path d="m9 5 20 17-12 12L3 12Z" fill="#b58b63" stroke="#7e5b3d" stroke-width="1.5"/><ellipse cx="23" cy="27" rx="9" ry="6" transform="rotate(-46 23 27)" fill="#ead1a5" stroke="#7e5b3d" stroke-width="1.5"/><path d="m10 13 8 7m-6-2 5 4" stroke="#7e5b3d"/>',
    half:'<path d="m7 8 23 13-14 13L3 15Z" fill="#cba576" stroke="#865f3d" stroke-width="1.4"/><path d="m7 8 23 13-8 4L3 15Z" fill="#f2ddba" stroke="#865f3d" stroke-width="1.3"/>'
  };
  function objectIcon(kind, index, state) {
    const shape=kind==='item'?(index%2?'toy':'book'):kind;
    const detail=shape==='shoe'&&state==='before'?'<g fill="#9c815c"><circle cx="15" cy="22" r="1.5"/><circle cx="23" cy="25" r="1"/><circle cx="8" cy="21" r="1"/></g>':
      shape==='plant'&&state==='after'?'<g fill="#4d8599"><circle cx="29" cy="15" r="1.5"/><circle cx="32" cy="20" r="1.3"/><circle cx="27" cy="24" r="1.2"/></g>':'';
    return `<svg class="object-icon" viewBox="0 0 36 38" aria-hidden="true">${drawings[shape]||''}${detail}</svg>`;
  }
  function renderStory() {
    const t=task(),s=t.story;
    $('before-title').textContent=s.before_title;$('after-title').textContent=s.after_title;
    for(const state of ['before','after']) {
      const groups=s[state],container=$(state+'-scene');
      container.className='object-scene '+(t.family==='hotel'&&state==='after'?'shoe-slots':'');
      container.innerHTML=groups.map(g=>`<div class="object-group surface-${escape(g.color)}" role="img" aria-label="${escape(g.label)}"><div class="object-grid" style="--columns:${g.count===2?2:g.count===12?6:g.count===16||g.count===32?8:5}">${Array.from({length:g.count},(_,i)=>objectIcon(g.kind,i,state)).join('')}</div><p>${escape(g.label)}</p></div>`).join('');
    }
    const notes={garden:['等待喷洒驱虫剂','全部喷洒完成，倾角 ≤ 15°'],hotel:['每只鞋都需要去尘','每位恰好 2 只，彼此相邻'],bedroom:['2 片积尘地面 · 4 盏灯亮着','2 片干净地面 · 4 盏灯关闭'],garage:['指定地面有砂、有尘','指定地面无砂、无尘']};
    $('before-note').textContent=notes[t.family][0];$('after-note').textContent=notes[t.family][1];
    $('story-invariant').textContent=s.invariant;
    $('story-steps').innerHTML=s.steps.map((step,i)=>`<li><span>${i+1}</span><strong>${escape(step)}</strong></li>`).join('');
    $('story-loop').textContent=s.loop;
    const types={result:'最终',history:'过程',always:'全程',finish:'收尾'};
    $('success-rules').innerHTML=s.rules.map((r,i)=>`<li data-rule="${i}"><span class="rule-number">${String(i+1).padStart(2,'0')}</span><div><span class="rule-type">${types[r.kind]}</span><strong>${escape(r.title)}</strong><p>${escape(r.text)}</p></div></li>`).join('');
    $('example-buttons').innerHTML=s.examples.map((e,i)=>`<button data-example="${i}" aria-pressed="false" aria-controls="example-text">${escape(e.label)}</button>`).join('');
    selectExample(0);
    const passed=data.tasks.filter(x=>x.reference_status==='passed').length;
    document.querySelector('.overview-status').textContent=`当前进度：${data.tasks.length} 个场景已构建；完整参考流程通过 ${passed} / ${data.tasks.length}。上图表示目标结果。`;
  }
  function selectExample(index) {
    const e=task().story.examples[index];
    $('example-text').textContent=e.text;
    document.querySelectorAll('[data-example]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.example)===index)));
    document.querySelectorAll('[data-rule]').forEach(r=>r.classList.toggle('example-rule',Number(r.dataset.rule)===e.rule));
  }
  $('example-buttons').addEventListener('click',e=>{const b=e.target.closest('[data-example]');if(b)selectExample(Number(b.dataset.example));});
  function setTab(name, save=true) {
    tab=['overview','scene','validation','contract'].includes(name)?name:'overview';
    document.querySelectorAll('[data-tab]').forEach(b=>{
      const active=b.dataset.tab===tab;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;
      $('panel-'+b.dataset.tab).hidden=!active;
    });
    if(save)setHash();
  }
  document.querySelector('.tabs').addEventListener('click',e=>{const b=e.target.closest('[data-tab]');if(b)setTab(b.dataset.tab);});
  document.querySelector('.tabs').addEventListener('keydown',e=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();const tabs=[...document.querySelectorAll('[data-tab]')];let i=tabs.findIndex(b=>b.dataset.tab===tab);
    i=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    setTab(tabs[i].dataset.tab);tabs[i].focus();
  });
  function updateCamera() {
    const t=task(),frame=t.frames.find(f=>f.view===camera)||t.frames[0];camera=frame.view;
    $('camera-tabs').innerHTML=t.frames.map(f=>`<button data-camera="${f.view}" aria-pressed="${f.view===camera}" aria-label="${views[f.view]}视角">${views[f.view]}</button>`).join('');
    const alt=t.name+' · '+views[camera]+'视角 · 冻结初态';
    $('camera-image').src=frame.url;$('camera-image').alt=alt;
    $('camera-caption').textContent='初态 RGB / '+camera.toUpperCase();
    $('dialog-image').src=frame.url;$('dialog-image').alt=alt;
    $('dialog-title').textContent=t.name+' · 真实初态';$('dialog-view').textContent=views[camera]+'视角 / '+camera.toUpperCase();
  }
  $('camera-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-camera]');if(b){camera=b.dataset.camera;updateCamera();setHash();}});
  $('camera-open').addEventListener('click',()=>{updateCamera();$('image-dialog').showModal();});
  $('dialog-close').addEventListener('click',()=>$('image-dialog').close());
  $('image-dialog').addEventListener('click',e=>{if(e.target===$('image-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  function nextCamera(delta){const frames=task().frames,i=frames.findIndex(f=>f.view===camera);camera=frames[(i+delta+frames.length)%frames.length].view;updateCamera();setHash();}
  $('image-prev').addEventListener('click',()=>nextCamera(-1));$('image-next').addEventListener('click',()=>nextCamera(1));
  $('image-dialog').addEventListener('keydown',e=>{if(e.key==='ArrowLeft'){e.preventDefault();nextCamera(-1);}if(e.key==='ArrowRight'){e.preventDefault();nextCamera(1);}});
  $('phase-buttons').addEventListener('click',e=>{const b=e.target.closest('[data-phase]');if(b){selectPhase(Number(b.dataset.phase));setHash();}});
  function selectPhase(index) {
    phase=index;const p=task().phases[phase];
    document.querySelectorAll('[data-phase]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.phase)===phase)));
    $('phase-label').textContent='PHASE '+String(phase+1).padStart(2,'0')+' / 设计流程';
    $('phase-title').textContent=p.title;$('phase-description').textContent=p.description;
    $('phase-count').textContent=p.calls===null?'另计':p.calls;
    $('phase-count-label').textContent=p.calls===null?'收尾 / 工具交接':'条件有效动作';
    $('map-selection').textContent='已高亮「'+p.title+'」使用的区域。点击台面或物体查看位置。';renderMap();
  }
  const svgNS='http://www.w3.org/2000/svg';
  function node(name,attributes={},text){const e=document.createElementNS(svgNS,name);for(const [k,v] of Object.entries(attributes))e.setAttribute(k,v);if(text!==undefined)e.textContent=text;return e;}
  function renderMap(){
    const m=task().map,svg=$('layout-map'),p=task().phases[phase];
    const width=Math.max(280,svg.clientWidth||640),height=Math.max(200,svg.clientHeight||490),pad=22;
    svg.replaceChildren();const bounds=m.bounds,scale=Math.min((width-2*pad)/(bounds[2]-bounds[0]),(height-2*pad)/(bounds[3]-bounds[1]));
    const mapW=(bounds[2]-bounds[0])*scale,mapH=(bounds[3]-bounds[1])*scale;
    const x=v=>(v-bounds[0])*scale+(width-mapW)/2,y=v=>(bounds[3]-v)*scale+(height-mapH)/2;
    const viewportWidth=width/zoom,viewportHeight=height/zoom;svg.setAttribute('viewBox',`${(width-viewportWidth)/2} ${(height-viewportHeight)/2} ${viewportWidth} ${viewportHeight}`);
    const defs=node('defs'),pattern=node('pattern',{id:'map-grid',x:x(Math.floor(bounds[0])),y:y(Math.ceil(bounds[3])),width:scale,height:scale,patternUnits:'userSpaceOnUse'});
    pattern.append(node('path',{d:`M ${scale} 0 L 0 0 0 ${scale}`,fill:'none',stroke:'#dce4d7','stroke-width':.65}));defs.append(pattern);svg.append(defs);
    svg.append(node('rect',{x:0,y:0,width,height,fill:'#f5f7f1'}));
    svg.append(node('rect',{x:0,y:0,width,height,fill:'url(#map-grid)'}));
    const rect=(a,attrs)=>node('rect',{x:x(a[0][0]),y:y(a[1][1]),width:Math.max(1,(a[1][0]-a[0][0])*scale),height:Math.max(1,(a[1][1]-a[0][1])*scale),...attrs});
    m.floors.forEach(f=>{svg.append(rect(f.aabb,{fill:'#edf1e7','fill-opacity':.6,stroke:'#d2dcc9','stroke-width':1,'stroke-dasharray':'4 4'}));});
    m.background.forEach(g=>svg.append(rect(g.aabb,{fill:'#c9d1c5','fill-opacity':.42,stroke:'#bac5b5','stroke-width':.6,rx:1})));
    const inspect=(item,element)=>{
      element.setAttribute('tabindex','0');element.setAttribute('role','button');element.setAttribute('aria-label',item.label+'，查看初态位置');
      const show=()=>{const [a,b]=item.aabb;const center=[(a[0]+b[0])/2,(a[1]+b[1])/2];$('map-selection').textContent=item.label+' · x '+center[0].toFixed(2)+' m / y '+center[1].toFixed(2)+' m · '+(b[0]-a[0]).toFixed(2)+' × '+(b[1]-a[1]).toFixed(2)+' m';};
      element.addEventListener('click',show);element.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();show();}});
      element.append(node('title',{},item.label));
    };
    for(const f of m.fixtures){
      const active=p.roles.includes(f.role),g=node('g',{class:'map-item '+(active?'focused':'muted'),'data-map-role':f.role});
      const c=f.rgba.slice(0,3).map(v=>Math.round(v*255));
      g.append(rect(f.aabb,{fill:`rgb(${c.join(',')})`,'fill-opacity':.68,stroke:'#6d8068','stroke-width':1,rx:2,class:'shape'}));
      const [a,b]=f.aabb;
      if((b[0]-a[0])*scale>42 && (b[1]-a[1])*scale>17)g.append(node('text',{x:x((a[0]+b[0])/2),y:y((a[1]+b[1])/2)+3,'text-anchor':'middle','font-size':10},f.label));
      inspect(f,g);svg.append(g);
    }
    for(const o of m.objects){
      const g=node('g',{class:'map-item','data-object-role':o.role}),[a,b]=o.aabb;
      // A larger transparent hit target keeps small measured objects accessible.
      g.append(node('circle',{cx:x((a[0]+b[0])/2),cy:y((a[1]+b[1])/2),r:6,fill:'transparent'}));
      g.append(node('circle',{cx:x((a[0]+b[0])/2),cy:y((a[1]+b[1])/2),r:Math.max(2,Math.min(4,(b[0]-a[0])*scale/3)),fill:'#304b3c',stroke:'#f4f7ef','stroke-width':.7,class:'shape'}));inspect(o,g);svg.append(g);
    }
    if(m.robot){const [a,b]=m.robot,rx=x((a[0]+b[0])/2),ry=y((a[1]+b[1])/2);svg.append(node('circle',{cx:rx,cy:ry,r:5,fill:'#fff',stroke:'#354f69','stroke-width':1.5}));svg.append(node('text',{x:rx+8,y:ry+3,'font-size':10},'机器人'));}
    const barX=width-28-scale;svg.append(node('path',{d:`M ${barX} ${height-18} h ${scale}`,stroke:'#6e8068','stroke-width':2}));svg.append(node('text',{x:barX+scale/2,y:height-25,'text-anchor':'middle','font-size':9},'1 m'));
  }
  $('map-plus').addEventListener('click',()=>{zoom=Math.min(2.5,zoom+.25);renderMap();});
  $('map-minus').addEventListener('click',()=>{zoom=Math.max(.75,zoom-.25);renderMap();});
  $('map-reset').addEventListener('click',()=>{zoom=1;renderMap();});
  $('reference-select').addEventListener('change',e=>{reference=Number(e.target.value);renderReference();});
  function renderReference(){
    const r=task().references[reference];
    if(!r){$('trace-summary').textContent='尚未运行参考流程';$('trace-list').replaceChildren();$('action-detail').textContent='暂无动作记录';$('trace-error').hidden=true;return;}
    $('trace-summary').innerHTML=`<span><b>${r.actions.length}</b>次调用尝试</span><span><b>${r.successful_calls}</b>次调用通过</span><span><b>${r.manipulation_calls}</b>次操作通过（不含导航）</span>`;
    $('trace-error').hidden=!r.error && r.status!=='blocked';
    $('trace-error').textContent=r.error?(errorText[r.error]||r.error)+' 尚不能据此认定任务物理不可解。':r.status==='blocked'?'本次记录在启动阶段阻塞，仿真动作未开始；后续重试单独记录。':'';
    $('trace-list').innerHTML=r.actions.map((a,i)=>`<li><button data-action="${i}" aria-pressed="false"><span class="trace-index">${String(a.index).padStart(2,'0')}</span><span class="trace-icon ${a.status==='failed'?'failure':''}"></span><span>${escape(primitives[a.primitive]||a.primitive)}</span><span class="trace-target">${escape(a.target_label)}</span>${badge(a.status)}</button></li>`).join('');
    if(r.actions.length)selectAction(r.actions.length-1);
    else{$('action-detail').textContent='未记录到完成的动作调用。本次启动不计入任务执行成功或失败。';}
  }
  function selectAction(index){
    action=index;const r=task().references[reference],a=r.actions[action];
    document.querySelectorAll('[data-action]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.action)===action)));
    $('action-detail').innerHTML=`<p class="eyebrow">RECORDED ACTION / ${String(a.index).padStart(2,'0')}</p><h4>${escape(primitives[a.primitive]||a.primitive)} · ${escape(a.target_label)}</h4><dl><dt>调用结果</dt><dd>${badge(a.status)}</dd><dt>原子动作</dt><dd>${escape(a.primitive)}</dd><dt>目标角色</dt><dd>${escape(a.role)}</dd><dt>耗时</dt><dd>${Number(a.seconds).toFixed(2)} 秒</dd><dt>来源</dt><dd>特权参考程序 · 现有 checked executor</dd></dl>${a.error?`<p class="action-error">${escape(errorText[a.error]||a.error)}<br><code>${escape(a.code)}</code><br>${escape(a.error)}</p>`:''}`;
  }
  $('trace-list').addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(b)selectAction(Number(b.dataset.action));});
  function restoreHash(){const state=hashState(),index=data.tasks.findIndex(t=>t.family===state.get('task'));selectTask(index<0?0:index,state);}
  addEventListener('hashchange',restoreHash);restoreHash();
  new ResizeObserver(()=>{if($('layout-map').clientWidth>0)renderMap();}).observe(document.querySelector('.map-wrap'));
})();
