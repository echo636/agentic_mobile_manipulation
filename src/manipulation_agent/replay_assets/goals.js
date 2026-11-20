/* Render only explicit model-authored notes, at their original point in the replay. */
window.renderTaskNotes = function(root, notes, options = {}) {
  root.replaceChildren();
  root.hidden = !notes?.content;
  if (root.hidden) return;
  const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(cls)e.className=cls;return e;};
  const list=value=>Array.isArray(value)?value:[];
  const text=value=>typeof value==='string'?value:JSON.stringify(value);
  root.classList.add('goal-notes');
  root.dataset.version=String(notes.version??'');
  root.setAttribute('aria-label','目标与当前计划');
  const when=notes.agent_step===0?'行动前':`step ${notes.agent_step??'?'} 后`;
  root.append(node('div',`目标 · v${notes.version??'?'} · ${when} · 状态由模型记录`,'goal-caption'));
  let state;
  try{state=JSON.parse(notes.content);}catch{state=null;}
  if(state?.format!=='goal-tree-v1'||!state.L1||!Array.isArray(state.L2)||state.L2.some(g=>!g||typeof g!=='object')){
    root.append(node('pre',notes.content,'goal-plain'));return;
  }
  const labels={pending:'待做',active:'进行中',done:'已完成',blocked:'受阻',retired:'已撤销'};
  const tree=node('div',null,'goal-inline-tree');tree.setAttribute('aria-label','总目标与子目标');
  const top=node('div',null,'goal-root-node');
  top.append(node('span','总目标','goal-level-label'),node('strong',text(state.L1.goal??state.L1.instruction??''),'goal-root'));
  tree.append(top);
  const children=node('ul',null,'goal-children');
  const goalNames=new Map(state.L2.map(g=>[g.id,text(g.goal??'')]));
  for(const g of state.L2){
    const status=Object.hasOwn(labels,g.status)?g.status:'unknown';
    const child=node('li',null,'goal-child goal-'+status);child.dataset.goalId=String(g.id??'');
    const current=g.id===state.active?.subgoal_id;
    if(current)child.classList.add('goal-current');
    const head=node('div',null,'goal-child-head');
    head.append(node('span',g.id??'','goal-id'),node('strong',text(g.goal??''),'goal-child-title'),node('span',labels[status]??'未记录状态','goal-state-pill goal-state-'+status));
    if(current)head.append(node('span','当前','goal-focus-tag'));
    child.append(head);
    const deps=list(g.depends_on);
    if(deps.length){
      const dependency=node('p','前置：'+deps.map(text).join('、'),'goal-dependencies');
      dependency.title=deps.map(id=>`${id} · ${goalNames.get(id)??'未记录的子目标'}`).join('；');child.append(dependency);
    }
    if(g.check||list(g.evidence).length){
      const detail=node('details',null,'goal-support');detail.append(node('summary','完成依据'));
      if(g.check)detail.append(node('p',text(g.check)));
      for(const item of list(g.evidence))detail.append(node('p',text(item)));
      child.append(detail);
    }
    children.append(child);
  }
  if(!state.L2.length)children.append(node('li','尚未记录子目标','goal-empty'));
  tree.append(children);root.append(tree);
  const active=state.active;
  if(active&&(list(active.steps).length||active.next_check)){
    const local=node('details',null,'goal-local');local.open=!options.compact;
    local.append(node('summary',`当前步骤${active.subgoal_id?' · '+text(active.subgoal_id):''}`));
    if(list(active.steps).length){const steps=node('ol');for(const s of active.steps)steps.append(node('li',text(s)));local.append(steps);}
    if(active.next_check)local.append(node('p','下一检查：'+text(active.next_check)));
    root.append(local);
  }
  if(state.change)root.append(node('p','更新：'+text(state.change),'goal-change'));
  const raw=node('details',null,'goal-raw');raw.append(node('summary','笔记原文'),node('pre',notes.content));root.append(raw);
};

/* Independent evidence is shown only in archived review, never policy input. */
window.renderGoalEvidence = function(root, evaluation, envStep) {
  root.replaceChildren();
  root.hidden=evaluation?.score_type!=='custom_joint_terminal_and_public_history_v1';
  if(root.hidden)return;
  root.classList.add('goal-notes');
  const node=(tag,text)=>{const e=document.createElement(tag);if(text!=null)e.textContent=String(text);return e;};
  const terminal={};
  for(const event of evaluation.goal_events??[]){
    if(event.kind==='terminal_state'&&event.env_step<=envStep)terminal[event.id]=event.satisfied;
  }
  const done=Object.values(terminal).filter(Boolean).length;
  const total=Object.keys(evaluation.terminal_conditions??{}).length;
  root.append(node('strong',`独立验证 · 当前终态条件 ${done}/${total} · 仅供回放，未提供给模型`));
  const details=node('details'),list=node('ul');
  details.append(node('summary','历史条件与过程中违反的约束'));
  for(const id of Object.keys(evaluation.historical_conditions??{})){
    const event=evaluation.milestone_events?.[id];
    list.append(node('li',`${event&&event.env_step<=envStep?'已达成':'尚未达成'} · ${id}`));
  }
  for(const event of evaluation.public_constraint_violations??[]){
    if(event.env_step<=envStep)list.append(node('li',`约束违反 · ${event.id} · 仿真步 ${event.env_step}`));
  }
  details.append(list);root.append(details);
};
