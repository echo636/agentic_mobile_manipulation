/* The conversation is built once. Playback only changes focus, never history. */
window.ReplayTranscript = class ReplayTranscript {
  constructor(root, follow, onSelect, onBrowse) {
    this.onBrowse=onBrowse||(()=>{});this.root=root; this.follow=follow; this.onSelect=onSelect; this.entries=[]; this.groups=[];
    this.feed=document.createElement('div'); this.feed.className='conversation-feed'; this.feed.tabIndex=0;
    this.feed.setAttribute('aria-label','完整模型与工具会话'); root.replaceChildren(this.feed);
    for(const event of ['wheel','touchstart']) this.feed.addEventListener(event,()=>{follow.checked=false;this.onBrowse()},{passive:true});
    this.feed.addEventListener('keydown',e=>{if(['ArrowUp','ArrowDown','PageUp','PageDown','Home','End',' '].includes(e.key)){follow.checked=false;this.onBrowse();e.stopPropagation()}});
    follow.onchange=()=>{if(follow.checked)this.scrollCurrent();else this.onBrowse()};
    if(window.ResizeObserver){this.resizeObserver=new ResizeObserver(()=>{if(follow.checked)this.scrollCurrent()});this.resizeObserver.observe(this.feed)}
  }
  node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
  detail(card,label,entry,content){
    const d=this.node('details',undefined,'raw-detail');d.append(this.node('summary',label));
    const source=entry.source==='model_events.jsonl'?`${entry.source_id||''} · event ${entry.source_event_index}`:`${entry.at||''} · timestamp alignment`;
    d.append(this.node('small',source,'source-reference'));
    for(const [text,cls] of content)d.append(this.node('pre',text,cls));card.append(d);
  }
  setData(data){
    this.feed.replaceChildren();this.entries=[];this.groups=[];this.key=null;this.follow.checked=true;this.marked=[];
    const steps=new Map(data.steps.map(s=>[s.index,s]));let group=null;
    for(const entry of data.model_transcript||[]){
      if(!group||group.step!==entry.step){
        const step=steps.get(entry.step),box=this.node('section',undefined,'step-group');
        const heading=this.node('div',undefined,'step-group-heading');
        const title=step?String(step.index).padStart(2,'0')+'  '+(step.arguments.primitive||step.tool):'模型记录';
        if(step){const button=this.node('button',title,'step-jump');button.onclick=()=>this.onSelect(step.index);heading.append(button);box.dataset.step=step.index}
        else heading.append(this.node('strong',title));
        if(step?.tool_seconds!=null)heading.append(this.node('span',step.tool_seconds.toFixed(1)+' s','step-duration'));
        box.append(heading);group={step:entry.step,box,hasText:false};this.groups.push(group);this.feed.append(box);
      }
      const card=this.node('article',undefined,'conversation-entry kind-'+entry.kind);card.dataset.kind=entry.kind;card.dataset.sequence=entry.sequence;
      if(entry.step!=null)card.dataset.step=entry.step;
      if(['assistant','provider_summary'].includes(entry.kind)){
        group.hasText=true;
        card.append(this.node('div',entry.kind==='assistant'?'模型输出':'推理摘要 · 接口原文','message-label'));
        card.append(this.node('div',entry.text,'conversation-original'));
        this.detail(card,'来源',entry,[]);
      }else if(entry.kind==='tool_call'){
        const args=entry.arguments||{},name=args.primitive||entry.tool;
        const line=this.node('div',undefined,'tool-line');line.append(this.node('span','↗','tool-symbol'),this.node('strong',name));
        if(entry.tool==='read_skill'&&args.name)line.append(this.node('span',args.name,'tool-context'));
        if(entry.tool==='look'&&args.yaw_degrees!=null)line.append(this.node('span',args.yaw_degrees+'°','tool-context'));
        card.append(line);this.detail(card,'调用参数',entry,[[JSON.stringify(entry.arguments,null,2),'conversation-arguments']]);
      }else{
        let payload=entry.structured_content||{};
        for(const text of entry.text_blocks||[]){try{const value=JSON.parse(text);if(value&&typeof value==='object'&&!Array.isArray(value)){payload=value;break}}catch{}}
        const failure=entry.error||entry.is_error||entry.status==='failed'||payload.ok===false;
        const error=entry.error||payload.error;
        const code=typeof error==='object'?error?.code||error?.message:typeof error==='string'?error:'';
        const status=failure?'调用失败'+(code?' · '+String(code).slice(0,130):''):entry.tool==='finish'?'结束请求已接受':entry.image_count?'已返回 '+entry.image_count+' 张 RGB':payload.job?.status?'观测任务 · '+payload.job.status:'工具已返回';
        card.append(this.node('div',status,'tool-outcome '+(failure?'failed':'ok')));
        const content=(entry.text_blocks||[]).map(t=>[t,'conversation-result']);
        if(entry.error!=null)content.push([JSON.stringify(entry.error,null,2),'conversation-error']);
        if(entry.structured_content!=null)content.push([JSON.stringify(entry.structured_content,null,2),'conversation-result']);
        this.detail(card,'完整返回'+(content.length?' · '+content.length+' 段文本':''),entry,content);
      }
      group.box.append(card);this.entries.push({entry,card,group});
    }
    const returned=new Set((data.model_transcript||[]).filter(e=>e.kind==='tool_result').map(e=>e.call_id));
    for(const {entry,card} of this.entries)if(entry.kind==='tool_call'&&!returned.has(entry.call_id))card.append(this.node('div','未记录工具返回','tool-outcome failed'));
    for(const g of this.groups)if(!g.hasText)g.box.querySelector('.step-group-heading').after(this.node('p','本步直接调用工具，无新增模型文字','no-model-text'));
    if(!this.entries.length)this.feed.append(this.node('p',data.failure?'运行在产生模型记录前结束。':'尚无已归档的模型输出。','empty-conversation'));
    this.feed.scrollTop=0;
  }
  resumeFollow(){this.follow.checked=true;this.scrollCurrent()}
  setActive(state,focus){
    if(this.key===state.key)return;this.key=state.key;
    for(const pair of this.marked||[]){pair.card.querySelector('.conversation-original').textContent=pair.entry.text}this.marked=[];
    const matching=new Set(focus.ranges.map(r=>r.sequence));if(focus.sequence!=null)matching.add(focus.sequence);
    let current=null;
    for(const g of this.groups){
      const active=this.entries.some(p=>p.group===g&&matching.has(p.entry.sequence));
      g.box.classList.toggle('is-current',active);g.box.classList.toggle('is-future',state.phase==='initial'||g.step!=null&&state.step!=null&&g.step>state.step);
    }
    for(const pair of this.entries){
      const {entry,card}=pair,active=matching.has(entry.sequence);card.classList.toggle('is-current',active);
      card.classList.toggle('pending-result',entry.kind==='tool_result'&&entry.step===state.step&&['decision','execution','tool'].includes(state.phase));
      if(active)card.setAttribute('aria-current','true');else card.removeAttribute('aria-current');
      if(entry.sequence===focus.sequence)current=card;
      const range=focus.ranges.find(r=>r.sequence===entry.sequence);
      if(range){const original=card.querySelector('.conversation-original'),mark=this.node('mark',entry.text.slice(range.start,range.end),'current-text');
        original.replaceChildren(document.createTextNode(entry.text.slice(0,range.start)),mark,document.createTextNode(entry.text.slice(range.end)));this.marked.push(pair);
        if(entry.sequence===focus.sequence)current=mark;
      }
    }
    this.root.dataset.step=state.step??'';this.root.dataset.phase=state.phase;this.root.dataset.page=state.page??'';this.root.dataset.sequence=focus.sequence??'';
    this.current=current;if(this.follow.checked){if(state.phase==='initial')this.feed.scrollTop=0;else this.scrollCurrent()};
  }
  scrollCurrent(){if(!this.current)return;const top=this.current.getBoundingClientRect().top-this.feed.getBoundingClientRect().top+this.feed.scrollTop;this.feed.scrollTop=Math.max(0,top-this.feed.clientHeight*.15)}
};
