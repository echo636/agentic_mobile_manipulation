/* A reader for archived, explicit notes. Never infer a plan from motor actions. */
window.ReplayGoalHistory = class {
  constructor(root, data, options = {}) {
    this.root=root;this.data=data;this.options=options;
    const candidates=data.task_note_versions?.length?data.task_note_versions:(data.steps??[]).map(s=>s.task_notes).filter(Boolean);
    const seen=new Set();this.notes=candidates.filter(n=>{
      if(typeof n.content!=='string'||seen.has(n.version))return false;
      seen.add(n.version);return true;
    });
    root.replaceChildren();
    const header=this.node('div',null,'gh-heading');
    header.append(this.node('h2','目标拆解与维护'),this.node('p','按笔记版本查看：L1 总目标 → L2 子目标，以及当前子目标的 L3 步骤。完成状态是模型判断，独立评分见页面顶部。'));
    root.append(header);
    if(!this.notes.length){
      root.append(this.node('p','这条运行没有归档可展示的 task_notes。不能从动作轨迹反推模型曾维护的目标树。','gh-empty'));return;
    }
    const structured=this.notes.filter(n=>this.state(n)).length;
    const first=this.notes[0].agent_step;
    root.append(this.node('p',`${this.notes.length} 次笔记写入 · ${structured} 个结构化目标树版本 · ${first===0?'首次计划在行动前':Number.isFinite(first)?'首次记录在 agent step '+first+' 后':'首次记录时点未注明'}`,'gh-meta'));
    const layout=this.node('div',null,'gh-layout');
    this.timeline=this.node('nav',null,'gh-timeline');this.timeline.setAttribute('aria-label','计划版本');
    this.panel=this.node('div',null,'gh-panel');layout.append(this.timeline,this.panel);root.append(layout);
    this.buttons=this.notes.map((n,i)=>{
      const button=this.node('button',null,'gh-version');button.type='button';
      button.append(this.node('strong',`v${n.version} · ${i===0?'初始记录':'计划更新'}`),this.node('span',n.agent_step===0?'行动前':`agent step ${n.agent_step??'?'} 后`));
      button.onclick=()=>this.select(i);this.timeline.append(button);return button;
    });
    const requested=new URLSearchParams(location.search).get('version');
    this.select(Math.max(0,this.notes.findIndex(n=>String(n.version)===requested)));
  }
  node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(cls)e.className=cls;return e;}
  state(note){
    try{const s=JSON.parse(note?.content);return s?.format==='goal-tree-v1'&&s.L1&&Array.isArray(s.L2)&&s.L2.every(g=>g&&typeof g==='object')?s:null;}catch{return null;}
  }
  changes(before,after){
    const old=this.state(before),next=this.state(after),out=[];
    if(!before)return [next?'首次保存 L1/L2 目标树与当前 L3 计划。':'首次保存自由格式计划。'];
    if(!old||!next)return [!old&&!next?'自由格式笔记更新；未记录结构化目标差异。':next?'本版本开始使用结构化目标树。':'本版本改为自由格式笔记。'];
    const changed=(a,b)=>JSON.stringify(a??null)!==JSON.stringify(b??null);
    const label={pending:'待做',active:'进行中',done:'认为已完成',blocked:'受阻',retired:'已撤销'};
    if(changed(old.L1,next.L1))out.push('L1 总目标、完成条件或解释假设发生修改。');
    const previous=new Map(old.L2.map(g=>[g.id,g]));
    for(const g of next.L2){
      const p=previous.get(g.id),name=`${g.id??'?'} · ${g.goal??''}`;
      if(!p){out.push('新增子目标：'+name);continue;}
      if(p.status!==g.status)out.push(`${name}：${label[p.status]??p.status??'未记录'} → ${label[g.status]??g.status??'未记录'}`);
      if(changed(p.goal,g.goal))out.push(`${g.id} 的目标描述已修改：${p.goal??'未记录'} → ${g.goal??'未记录'}`);
      if(changed(p.depends_on,g.depends_on))out.push(`${g.id} 的依赖已修改：${(p.depends_on??[]).join(', ')||'无'} → ${(g.depends_on??[]).join(', ')||'无'}`);
      if(changed(p.covers,g.covers)||changed(p.check,g.check))out.push(`${g.id} 的覆盖条件或完成检查已更新。`);
      if(changed(p.evidence,g.evidence))out.push(`${g.id} 的证据或成员进度已更新。`);
    }
    const ids=new Set(next.L2.map(g=>g.id));
    for(const g of old.L2)if(!ids.has(g.id))out.push(`本版本未保留 ${g.id} · ${g.goal??''}；移除不代表完成。`);
    if(old.active?.subgoal_id!==next.active?.subgoal_id)out.push(`当前聚焦：${old.active?.subgoal_id??'无'} → ${next.active?.subgoal_id??'无'}`);
    if(changed(old.active?.steps,next.active?.steps)||changed(old.active?.next_check,next.active?.next_check))out.push('L3 局部步骤或下一检查已更新。');
    if(changed(old.uncertainties,next.uncertainties))out.push('未决问题已更新。');
    return out.length?out:['目标、状态和步骤未改变；请查看本次更新说明。'];
  }
  select(index){
    this.selected=index;const note=this.notes[index];this.panel.replaceChildren();
    this.buttons.forEach((b,i)=>{b.setAttribute('aria-current',i===index?'step':'false');b.classList.toggle('selected',i===index);});
    const controls=this.node('div',null,'gh-controls');
    const prev=this.node('button','← 上一版本'),next=this.node('button','下一版本 →');
    prev.disabled=index===0;next.disabled=index===this.notes.length-1;
    prev.onclick=()=>this.select(index-1);next.onclick=()=>this.select(index+1);
    controls.append(prev,this.node('strong',`第 ${index+1} / ${this.notes.length} 次记录 · v${note.version}`),next);this.panel.append(controls);
    const diff=this.node('section',null,'gh-diff');
    diff.append(this.node('h3',index?'这一版改了什么':'初始目标拆解'));
    const list=this.node('ul');for(const change of this.changes(this.notes[index-1],note))list.append(this.node('li',change));
    diff.append(list);const state=this.state(note);
    if(state?.change)diff.append(this.node('p','模型给出的更新理由：'+state.change,'gh-reason'));
    this.panel.append(diff);
    const notes=this.node('section',null,'gh-current');renderTaskNotes(notes,note);this.panel.append(notes);
    const actionSection=this.node('section',null,'gh-actions');actionSection.append(this.node('h3','此版本之后，实际执行了什么'));
    const actions=(this.data.steps??[]).filter(s=>s.is_motor_action&&(s.task_notes_before??s.task_notes)?.version===note.version);
    if(!actions.length)actionSection.append(this.node('p','归档中没有使用此笔记版本的后续 act/look。笔记里的计划步骤不等于已执行动作。'));
    for(const s of actions){
      const row=this.node('div',null,'gh-action');
      const ok=s.result?.ok;const status=ok===true?'工具成功':ok===false?'工具失败':s.status==='passed'?'工具成功':s.status==='failed'?'工具失败':'未记录完成';
      const error=s.result?.error?.code;
      row.append(this.node('span',`S${s.agent_step??'?'} · ${s.arguments?.primitive??s.tool}`,'gh-action-name'),this.node('span',status+(error?' · '+error:''),ok===false||s.status==='failed'?'gh-action-error':'gh-action-status'));
      if(this.options.onReplay){const button=this.node('button','查看此步画面 ↗');button.onclick=()=>this.options.onReplay(s.index);row.append(button);}
      actionSection.append(row);
    }
    this.panel.append(actionSection);
    if(location.protocol!=='about:'){
      const url=new URL(location.href);url.searchParams.set('view','goals');url.searchParams.set('version',String(note.version));history.replaceState(null,'',url);
    }
  }
};
