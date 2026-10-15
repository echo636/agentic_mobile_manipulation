/* Render only explicit model-authored notes. No reconstruction of hidden reasoning. */
window.renderTaskNotes = function(root, notes, options = {}) {
  root.replaceChildren();
  root.hidden = !notes?.content;
  if (root.hidden) return;
  const node = (tag, text, cls) => {const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(cls)e.className=cls;return e;};
  root.classList.add('goal-notes');
  root.dataset.version=String(notes.version ?? '');
  root.append(node('div', `工作记录 v${notes.version ?? '?'} · agent step ${notes.agent_step ?? '?'} · 模型自述，非独立评分`, 'goal-caption'));
  let state;
  try {state=JSON.parse(notes.content);} catch {state=null;}
  if(state?.format!=='goal-tree-v1'||!state.L1||!Array.isArray(state.L2)||state.L2.some(g=>!g||typeof g!=='object')){
    root.append(node('p','自由格式工作笔记 · 未记录结构化目标树；以下保留模型原文。','goal-format-note'));
    root.append(node('pre',notes.content,'goal-plain'));return;
  }
  const list=(value)=>Array.isArray(value)?value:[];
  const text=(value)=>typeof value==='string'?value:JSON.stringify(value);
  root.append(node('div','L1 · 总目标','goal-level-label'));
  root.append(node('strong', text(state.L1.goal ?? state.L1.instruction ?? ''), 'goal-root'));
  const criteria=node('ul',null,'goal-criteria');
  for(const r of list(state.L1.criteria))criteria.append(node('li',typeof r==='object'&&r!==null?`${r.id ?? ''}: ${r.condition ?? ''} (${r.basis ?? '来源未注明'})`:text(r)));
  root.append(criteria);
  if(list(state.L1.assumptions).length)root.append(node('p','解释假设：'+state.L1.assumptions.map(text).join('；'),'goal-assumptions'));
  const tree=node('div',null,'goal-children');
  const labels={pending:'待做',active:'进行中',done:'认为已完成',blocked:'受阻',retired:'已撤销'};
  const counts=node('div',null,'goal-status-counts');
  for(const status of ['done','active','pending','blocked','retired']){
    const n=state.L2.filter(g=>g.status===status).length;
    if(n)counts.append(node('span',`${labels[status]} ${n}`,'goal-state-pill goal-state-'+status));
  }
  root.append(counts);
  const current=state.L2.find(g=>g.id===state.active?.subgoal_id);
  if(current)root.append(node('p',`当前聚焦：${current.id} · ${text(current.goal??'')} · ${labels[current.status]??'状态未记录'}`,'goal-focus'));
  const criterionNames=new Map(list(state.L1.criteria).filter(r=>r&&typeof r==='object').map(r=>[r.id,r.condition]));
  const goalNames=new Map(state.L2.map(g=>[g.id,g.goal]));
  for(const g of state.L2){
    const status=Object.hasOwn(labels,g.status)?g.status:'unknown';
    const child=node('div',null,'goal-child goal-'+status);child.dataset.goalId=String(g.id??'');
    if(g.id===state.active?.subgoal_id)child.classList.add('goal-current');
    const head=node('div',null,'goal-child-head');
    head.append(node('span',g.id??'','goal-id'),node('span',labels[status]??'状态未记录','goal-state-pill goal-state-'+status));
    if(g.id===state.active?.subgoal_id)head.append(node('span','当前聚焦','goal-focus-tag'));
    child.append(head,node('strong',text(g.goal??''),'goal-child-title'));
    const deps=list(g.depends_on);
    child.append(node('p',deps.length?'依赖：'+deps.map(id=>`${id} · ${goalNames.get(id)??'未记录的子目标'}`).join('；'):'依赖：模型未列出前置子目标','goal-dependencies'));
    const evidence=node('details',null,'goal-support');
    evidence.append(node('summary','完成检查与依据'),node('p','服务条件：'+(list(g.covers).map(id=>`${id} · ${criterionNames.get(id)??'条件内容未记录'}`).join('；')||'未注明')),node('p','完成检查：'+text(g.check??'未记录')));
    if(list(g.evidence).length)evidence.append(node('p','模型记录的证据/成员进度：'+g.evidence.map(text).join('；')));
    child.append(evidence);
    tree.append(child);
  }
  const details=node('details',null,'goal-tree');details.open=!options.compact;
  details.append(node('summary',`L2 · ${state.L2.length} 个子目标 · 状态由模型记录`),tree);root.append(details);
  if(state.active&&typeof state.active==='object'){
    const local=node('div',null,'goal-local');
    local.append(node('strong','L3 · 当前 '+text(state.active.subgoal_id??'无')+' 的局部步骤'));
    local.append(node('p','仅规划当前子目标，不属于目标树的新一层。','goal-local-caption'));
    const steps=node('ol');for(const s of list(state.active.steps))steps.append(node('li',text(s)));
    if(!list(state.active.steps).length)local.append(node('p','当前版本没有记录待执行步骤。'));
    local.append(steps,node('p','下一检查：'+text(state.active.next_check??'')));root.append(local);
  }
  if(list(state.uncertainties).length)root.append(node('p','未决问题：'+state.uncertainties.map(text).join('；'),'goal-uncertainties'));
  if(state.change)root.append(node('p','本次更新：'+text(state.change),'goal-change'));
  const raw=node('details',null,'goal-raw');raw.append(node('summary','查看该版本笔记原文'),node('pre',notes.content));root.append(raw);
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
