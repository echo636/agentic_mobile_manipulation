/* One display clock. Review holds are reading time, never measured LLM latency. */
window.ReplayTiming = {
  review(data) {
    const segments=[],video=data.video?.status==='passed'?data.video:null,duration=video?.duration_seconds||0;
    if(!video&&!data.steps.length&&!(data.model_transcript||[]).length)return null;
    let cursor=0,media=0;
    const add=(step,phase,seconds,start,end,through)=>{
      if(!(seconds>0))return;
      segments.push({step,phase,start_seconds:cursor,end_seconds:cursor+seconds,
        media_start:start,media_end:end,visible_through:through});cursor+=seconds;
    };
    const readTime=entries=>Math.min(16,Math.max(2,entries.reduce((n,e)=>n+(e.text||'').length,0)/42));
    add(null,'initial',.6,0,0,0);
    for(let i=0;i<data.steps.length;i++){
      const step=data.steps[i],entries=(data.model_transcript||[]).filter(e=>e.step===step.index);
      const call=entries.find(e=>e.kind==='tool_call'),result=entries.find(e=>e.kind==='tool_result');
      const before=entries.filter(e=>['assistant','provider_summary'].includes(e.kind)&&(!call||e.sequence<call.sequence));
      media=Math.min(duration,Math.max(media,step.video_start_seconds??media));
      const next=data.steps[i+1]?.video_start_seconds;
      const end=Math.min(duration,Math.max(media,next??duration));
      const prior=call?call.sequence-1:Math.max(0,...entries.map(e=>e.sequence));
      if(before.length)add(step.index,'decision',readTime(before),media,media,prior);
      add(step.index,'tool',Math.max(.35,end-media),media,end,call?.sequence??prior);
      add(step.index,'result',step.status==='failed'?2:step.new_observation?1.2:.6,end,end,
        result?.sequence??Math.max(prior,...entries.map(e=>e.sequence)));
      media=end;
    }
    const final=(data.model_transcript||[]).filter(e=>e.step==null&&['assistant','provider_summary'].includes(e.kind));
    add(null,'final',final.length?readTime(final):.6,duration,duration,Infinity);
    return {status:'passed',file:video?.playback_file||video?.file,
      duration_seconds:cursor,segments,reading_holds:true,source:'browser_event_clock'};
  },
  locate(data, edition, time) {
    const record=edition==='review'?data.review_timeline:edition==='inspection'?data.inspection_video:edition==='walltime'?data.walltime_video:data.video;
    time=Math.max(0,Number(time)||0);
    let segment=null,phase='initial',step=null;
    if(edition==='raw'){
      for(const candidate of data.steps){if(candidate.video_start_seconds!=null&&candidate.video_start_seconds<=time+1e-6)step=candidate.index}
      if(step!=null){phase='execution';const s=data.steps.find(s=>s.index===step),next=data.steps.find(n=>n.video_start_seconds>s.video_start_seconds+1e-7);segment={step,phase,start_seconds:s.video_start_seconds,end_seconds:next?.video_start_seconds??record?.duration_seconds??time}}
    }else{
      const segments=record?.segments||[];
      let lo=0,hi=segments.length;
      while(lo<hi){const mid=(lo+hi)>>1;if(segments[mid].start_seconds<=time+1e-6)lo=mid+1;else hi=mid}
      const candidate=segments[lo-1];
      if(candidate&&(time<candidate.end_seconds||lo===segments.length&&time<=Math.max(record.duration_seconds,candidate.end_seconds)+.1))segment=candidate;
      if(segment){phase=segment.phase;step=segment.step??null}else if(lo>0)phase='unmapped';
    }
    const index=step==null?-1:data.steps.findIndex(s=>s.index===step);
    const start=segment?.start_seconds??0,end=segment?.end_seconds??start;
    return {time,step,index,phase,segment,start,end,elapsed:Math.max(0,Math.min(time-start,end-start)),duration:Math.max(0,end-start),page:segment?.page??null,pages:segment?.pages??null,
      side:['result','outside_tool_wait','final','evaluation'].includes(phase)?'after':'before',key:[edition,start,end,step,phase,segment?.page].join(':')};
  },
  visibleThrough(data,state){
    if(state.segment?.visible_through!=null)return state.segment.visible_through;
    if(['final','evaluation'].includes(state.phase))return Infinity;
    if(['initial','unmapped'].includes(state.phase)||state.step==null)return 0;
    const entries=(data.model_transcript||[]).filter(e=>e.step===state.step);
    const call=entries.find(e=>e.kind==='tool_call'),result=entries.find(e=>e.kind==='tool_result');
    if(state.phase==='decision')return call?call.sequence-1:Math.max(0,...entries.map(e=>e.sequence));
    if(['execution','tool'].includes(state.phase))return call?.sequence??0;
    return result?.sequence??Math.max(0,...entries.map(e=>e.sequence));
  },
  focus(data, edition, state){
    const entries=(data.model_transcript||[]).filter(e=>state.phase==='final'?e.step==null&&['assistant','provider_summary'].includes(e.kind):state.step!=null&&e.step===state.step);
    if(['initial','unmapped','evaluation'].includes(state.phase))return {sequence:null,ranges:[]};
    let selected=null,ranges=[];
    const ledger=edition==='inspection'?data.inspection_video?.text_pages?.[state.segment?.text_id]:null;
    if(ledger&&state.page&&['decision','final'].includes(state.phase)){
      const start=ledger.pages.slice(0,state.page-1).join('').length,end=start+ledger.pages[state.page-1].length;
      const used=new Map();
      for(const entry of entries){
        if(!['assistant','provider_summary'].includes(entry.kind)||!entry.text)continue;
        const at=ledger.text.indexOf(entry.text,used.get(entry.text)||0);if(at<0)continue;used.set(entry.text,at+entry.text.length);
        const a=Math.max(start,at),b=Math.min(end,at+entry.text.length);
        if(b>a){ranges.push({sequence:entry.sequence,start:a-at,end:b-at});if(!selected)selected=entry}
      }
      if(!selected&&state.phase==='decision')selected=entries.find(e=>e.kind==='tool_call');
    }
    if(!selected){
      const kind=['result','outside_tool_wait'].includes(state.phase)?'tool_result':['execution','tool'].includes(state.phase)?'tool_call':null;
      selected=kind?entries.find(e=>e.kind===kind):entries.find(e=>['assistant','provider_summary'].includes(e.kind))||entries.find(e=>e.kind==='tool_call');
    }
    return {sequence:selected?.sequence??null,ranges};
  },
  seekTime(data,edition,index){
    const step=data.steps[index];if(!step)return null;
    if(edition==='raw')return step.video_start_seconds??null;
    const record=edition==='review'?data.review_timeline:edition==='inspection'?data.inspection_video:data.walltime_video;
    const segment=record?.segments?.find(s=>s.step===step.index&&(edition!=='walltime'||s.phase==='tool')&&s.end_seconds>s.start_seconds);
    return segment?segment.start_seconds+Math.min(.04,(segment.end_seconds-segment.start_seconds)/2):null;
  }
};
