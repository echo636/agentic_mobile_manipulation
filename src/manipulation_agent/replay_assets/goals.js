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
    root.append(node('pre',notes.content,'goal-plain'));return;
  }
  const list=(value)=>Array.isArray(value)?value:[];
  const text=(value)=>typeof value==='string'?value:JSON.stringify(value);
  root.append(node('strong', 'L1 · '+text(state.L1.goal ?? state.L1.instruction ?? ''), 'goal-root'));
  const criteria=node('ul',null,'goal-criteria');
  for(const r of list(state.L1.criteria))criteria.append(node('li',typeof r==='object'&&r!==null?`${r.id ?? ''}: ${r.condition ?? ''} (${r.basis ?? '来源未注明'})`:text(r)));
  root.append(criteria);
  if(list(state.L1.assumptions).length)root.append(node('p','解释假设：'+state.L1.assumptions.map(text).join('；'),'goal-assumptions'));
  const tree=node('div',null,'goal-children');
  const labels={pending:'待做',active:'进行中',done:'认为已完成',blocked:'受阻',retired:'已撤销'};
  for(const g of state.L2){
    const status=Object.hasOwn(labels,g.status)?g.status:'pending';
    const child=node('div',null,'goal-child goal-'+status);child.dataset.goalId=String(g.id??'');
    child.append(node('strong',`${g.id??''} · ${labels[status]} · ${text(g.goal??'')}`));
    child.append(node('p',`服务条件：${list(g.covers).join(', ')||'未注明'} · 依赖：${list(g.depends_on).join(', ')||'无'}`));
    child.append(node('p','完成检查：'+text(g.check??'未记录')));
    if(list(g.evidence).length)child.append(node('p','证据/成员进度：'+g.evidence.map(text).join('；')));
    tree.append(child);
  }
  const details=node('details',null,'goal-tree');details.open=!options.compact;
  details.append(node('summary',`L2 · ${state.L2.length} 个子目标`),tree);root.append(details);
  if(state.active&&typeof state.active==='object'){
    const local=node('div',null,'goal-local');
    local.append(node('strong','L3 · 当前 '+text(state.active.subgoal_id??'无')+' 的局部步骤'));
    const steps=node('ol');for(const s of list(state.active.steps))steps.append(node('li',text(s)));
    local.append(steps,node('p','下一检查：'+text(state.active.next_check??'')));root.append(local);
  }
  if(list(state.uncertainties).length)root.append(node('p','未决问题：'+state.uncertainties.map(text).join('；'),'goal-uncertainties'));
  if(state.change)root.append(node('p','本次更新：'+text(state.change),'goal-change'));
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
