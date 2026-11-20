/* Display explicit model-authored L1/L2 only. L3 and actual actions stay separate. */
window.ReplayGoalTree = class {
  constructor(root, state, options = {}) {
    this.root=root;this.state=state;this.options=options;this.scale=1;
    this.labels={pending:'待做',active:'进行中',done:'模型认为完成',blocked:'受阻',retired:'已撤销'};
    this.cards=[];this.byId=new Map();this.duplicates=new Set();
    state.L2.forEach((g,i)=>{if(this.byId.has(g.id))this.duplicates.add(g.id);else this.byId.set(g.id,i);});
    root.classList.add('gt-workspace');
    const toolbar=this.node('div',null,'gt-toolbar');
    const legend=this.node('div',null,'gt-legend');
    for(const [status,label] of Object.entries(this.labels)){
      const count=state.L2.filter(g=>g.status===status).length;
      if(count)legend.append(this.node('span',`${label} ${count}`,'goal-state-pill goal-state-'+status));
    }
    const dependencyLabel=this.node('label',null,'gt-dependency-toggle');
    this.dependencies=this.node('input');this.dependencies.type='checkbox';this.dependencies.checked=true;
    this.dependencies.onchange=()=>this.schedule();
    dependencyLabel.append(this.dependencies,document.createTextNode('依赖箭头'));
    toolbar.append(legend,dependencyLabel);
    const zoom=this.node('div',null,'gt-zoom');
    for(const [label,action] of [['缩小',()=>this.setScale(this.scale-.15)],['放大',()=>this.setScale(this.scale+.15)],['适合宽度',()=>this.fit()]]){
      const b=this.node('button',label);b.type='button';b.onclick=action;zoom.append(b);
    }
    this.zoomLabel=this.node('span','100%');zoom.append(this.zoomLabel);toolbar.append(zoom);root.append(toolbar);
    root.append(this.node('p','实线：L1 包含 L2。虚线箭头：前置子目标 → 后续子目标。点击节点查看条件与证据；颜色来自模型笔记。','gt-caption'));
    const layout=this.node('div',null,'gt-layout');this.viewport=this.node('div',null,'gt-viewport');
    this.viewport.tabIndex=0;this.viewport.setAttribute('aria-label','目标树画布，可滚动');
    this.sizer=this.node('div',null,'gt-sizer');this.canvas=this.node('div',null,'gt-canvas');
    this.svg=document.createElementNS('http://www.w3.org/2000/svg','svg');this.svg.classList.add('gt-edges');this.svg.setAttribute('aria-hidden','true');
    this.canvas.append(this.svg);
    this.rootCard=this.node('button',null,'gt-node gt-root');this.rootCard.type='button';
    this.rootCard.append(this.node('span','L1 · 宏观总目标','gt-level'),this.node('strong',this.text(state.L1.goal??state.L1.instruction??'未记录目标'),'gt-node-title'),this.node('span',`${this.list(state.L1.criteria).length} 项完成条件 · ${state.L2.length} 个子目标`,'gt-node-foot'));
    this.rootCard.onclick=()=>this.select(null);this.canvas.append(this.rootCard);
    const children=this.node('div',null,'gt-children');this.canvas.append(children);
    const previous=new Map((options.previous?.L2??[]).map(g=>[g.id,g]));
    state.L2.forEach((g,i)=>{
      const status=Object.hasOwn(this.labels,g.status)?g.status:'unknown';
      const card=this.node('button',null,'gt-node gt-child gt-'+status);card.type='button';card.dataset.goalId=this.text(g.id??'');
      const head=this.node('span',null,'gt-node-head');head.append(this.node('span','L2 · '+this.text(g.id??'?'),'gt-level'),this.node('span',this.labels[status]??'状态未记录','goal-state-pill goal-state-'+status));
      card.append(head,this.node('strong',this.text(g.goal??'未记录目标'),'gt-node-title'));
      const foot=this.node('span',null,'gt-node-foot');
      if(g.id===state.active?.subgoal_id)foot.append(this.node('span','当前聚焦','gt-focus'));
      if(options.previous){
        const old=previous.get(g.id),change=!old?'新增':JSON.stringify(old)!==JSON.stringify(g)?'本版更新':null;
        if(change){card.classList.add('gt-changed');foot.append(this.node('span',change,'gt-change'));}
      }
      if(this.list(g.depends_on).length)foot.append(this.node('span','依赖 '+this.list(g.depends_on).map(v=>this.text(v)).join('、')));
      card.append(foot);card.onclick=()=>this.select(i);children.append(card);this.cards.push(card);
    });
    if(!state.L2.length)children.append(this.node('p','此版本未记录 L2 子目标。','gt-caption'));
    this.sizer.append(this.canvas);this.viewport.append(this.sizer);
    this.inspector=this.node('aside',null,'gt-inspector');this.inspector.setAttribute('aria-label','选中目标详情');
    layout.append(this.viewport,this.inspector);root.append(layout);
    this.warnings=this.node('p',null,'gt-warning');root.append(this.warnings);
    this.selected=options.selectedId===null?null:this.byId.get(options.selectedId??state.active?.subgoal_id)??null;
    this.select(this.selected);
    this.resize=new ResizeObserver(()=>this.schedule());this.resize.observe(this.canvas);this.resize.observe(this.viewport);
    this.schedule();
  }
  node(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(cls)e.className=cls;return e;}
  list(v){return Array.isArray(v)?v:[];}
  text(v){return typeof v==='string'?v:JSON.stringify(v)??'';}
  select(index){
    this.selected=index;const g=index===null?null:this.state.L2[index];
    this.selectedId=g?.id??null;
    this.rootCard.setAttribute('aria-pressed',String(index===null));
    this.cards.forEach((c,i)=>c.setAttribute('aria-pressed',String(index===i)));
    this.inspector.replaceChildren();
    const heading=this.node('h3',g?`L2 · ${this.text(g.id??'?')} · ${this.labels[g.status]??'状态未记录'}`:'L1 · 宏观总目标');
    this.inspector.append(heading,this.node('p',this.text(g?.goal??this.state.L1.goal??this.state.L1.instruction??''),'gt-detail-goal'));
    const field=(label,value)=>{const section=this.node('section',null,'gt-field');section.append(this.node('h4',label));const values=Array.isArray(value)?value:[value];const ul=this.node('ul');for(const v of values)ul.append(this.node('li',this.text(v)));if(!values.length)ul.append(this.node('li','未记录'));section.append(ul);this.inspector.append(section);};
    if(!g){
      if(this.state.L1.instruction)field('原始指令',this.state.L1.instruction);
      field('完成条件',this.list(this.state.L1.criteria).map(r=>r&&typeof r==='object'?`${r.id??''} · ${r.condition??''}（${r.basis??'来源未注明'}）`:r));
      field('解释假设',this.list(this.state.L1.assumptions));
      if(this.list(this.state.uncertainties).length)field('未决问题',this.state.uncertainties);
    }else{
      field('完成检查',g.check??'未记录');
      field('模型记录的证据 / 成员进度',this.list(g.evidence));
      const criteria=new Map(this.list(this.state.L1.criteria).filter(r=>r&&typeof r==='object').map(r=>[r.id,r.condition]));
      field('服务的 L1 条件',this.list(g.covers).map(id=>`${this.text(id)} · ${criteria.get(id)??'条件未记录'}`));
      const links=(title,ids)=>{
        const section=this.node('section',null,'gt-field');section.append(this.node('h4',title));
        if(!ids.length)section.append(this.node('p','模型未列出'));
        for(const id of ids){const i=this.byId.get(id),valid=i!==undefined&&!this.duplicates.has(id);const b=this.node('button',this.text(id)+(valid?' · '+this.text(this.state.L2[i].goal):' · 缺失或重复 ID'),'gt-dep-link');b.disabled=!valid;b.onclick=()=>this.select(i);section.append(b);}
        this.inspector.append(section);
      };
      links('依赖这些前置目标',this.list(g.depends_on));
      links('这些目标依赖它',this.state.L2.filter(c=>this.list(c.depends_on).includes(g.id)).map(c=>c.id));
      const active=this.state.active;
      if(g.id===active?.subgoal_id){
        const local=this.node('section',null,'goal-local gt-field');local.append(this.node('h4','L3 · 当前子目标的局部计划'),this.node('p','不属于目标树层级；计划不等于已执行。','gt-caption'));
        const ol=this.node('ol');for(const step of this.list(active.steps))ol.append(this.node('li',this.text(step)));local.append(ol);
        if(!this.list(active.steps).length)local.append(this.node('p','此版本没有待执行步骤。'));
        local.append(this.node('p','下一检查：'+this.text(active.next_check??'未记录')));this.inspector.insertBefore(local,this.inspector.children[2]??null);
      }else this.inspector.append(this.node('p','此版本只为当前聚焦目标记录 L3；没有为这个节点补写执行步骤。','gt-caption'));
    }
    this.inspector.scrollTop=0;this.schedule();
  }
  setScale(value){this.scale=Math.max(.4,Math.min(1.6,value));this.schedule();}
  fit(){this.setScale(Math.min(1,(this.viewport.clientWidth-24)/this.canvas.offsetWidth));}
  schedule(){cancelAnimationFrame(this.frame);this.frame=requestAnimationFrame(()=>this.draw());}
  draw(){
    if(!this.canvas.offsetWidth)return;
    const w=this.canvas.offsetWidth,h=this.canvas.offsetHeight;
    this.canvas.style.transform=`scale(${this.scale})`;this.sizer.style.width=`${w*this.scale}px`;this.sizer.style.height=`${h*this.scale}px`;this.zoomLabel.textContent=Math.round(this.scale*100)+'%';
    this.svg.setAttribute('viewBox',`0 0 ${w} ${h}`);this.svg.replaceChildren();
    const ns='http://www.w3.org/2000/svg';
    const defs=document.createElementNS(ns,'defs'),marker=document.createElementNS(ns,'marker');
    // Unique per instance so hidden replay panels cannot capture another graph's arrows.
    window.replayGoalTreeSerial=(window.replayGoalTreeSerial??0)+1;const markerId='gt-arrow-'+window.replayGoalTreeSerial;
    for(const [k,v] of Object.entries({id:markerId,viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'6',markerHeight:'6',orient:'auto-start-reverse'}))marker.setAttribute(k,v);
    const tip=document.createElementNS(ns,'path');tip.setAttribute('d','M 0 0 L 10 5 L 0 10 z');tip.setAttribute('class','gt-arrow');marker.append(tip);defs.append(marker);this.svg.append(defs);
    const point=(el,right)=>{const box=el.getBoundingClientRect(),base=this.canvas.getBoundingClientRect();return{x:(box.left-base.left+(right?box.width:0))/this.scale,y:(box.top-base.top+box.height/2)/this.scale};};
    const path=(d,cls,arrow=false)=>{const p=document.createElementNS(ns,'path');p.setAttribute('d',d);p.setAttribute('class',cls);if(arrow)p.setAttribute('marker-end',`url(#${markerId})`);this.svg.append(p);};
    const origin=point(this.rootCard,true);
    this.cards.forEach((card,i)=>{const end=point(card,false),middle=origin.x+28;path(`M${origin.x},${origin.y} H${middle} V${end.y} H${end.x}`,'gt-hierarchy'+(i===this.selected?' gt-emphasis':''));});
    const warnings=[];if(this.duplicates.size)warnings.push('存在重复子目标 ID；相关依赖无法唯一连线。');
    let edge=0;
    this.state.L2.forEach((g,i)=>{
      for(const id of this.list(g.depends_on)){
        const from=this.byId.get(id);
        if(from===undefined||this.duplicates.has(id)||this.duplicates.has(g.id)){warnings.push(`${this.text(g.id)} 的依赖 ${this.text(id)} 缺失或不唯一。`);continue;}
        if(this.dependencies.checked){const a=point(this.cards[from],true),b=point(this.cards[i],true),lane=a.x+18+(edge++%5)*9;
          const d=from===i?`M${a.x},${a.y-12} C${lane+30},${a.y-40} ${lane+30},${a.y+40} ${b.x},${b.y+12}`:`M${a.x},${a.y} C${lane},${a.y} ${lane},${a.y} ${lane},${(a.y+b.y)/2} S${lane},${b.y} ${b.x},${b.y}`;
          path(d,'gt-dependency'+(from===this.selected||i===this.selected?' gt-emphasis':''),true);
        }
      }
    });
    this.warnings.textContent=[...new Set(warnings)].join(' ');this.warnings.hidden=!warnings.length;
  }
  destroy(){cancelAnimationFrame(this.frame);this.resize?.disconnect();}
};
