/* A selected pixel belongs only to its archived input image, never a moving video. */
window.ReplayNavigationTarget = class {
  constructor(panel, openImage) {
    this.panel=panel;this.key=null;this.target=null;this.image=null;
    this.button=document.createElement('button');this.button.className='navigation-target-image';
    this.button.setAttribute('aria-label','放大导航目标原图');this.button.onclick=()=>openImage();
    this.canvas=document.createElement('canvas');this.canvas.width=this.canvas.height=512;
    this.button.append(this.canvas);
    const copy=document.createElement('div');copy.className='navigation-target-copy';
    this.title=document.createElement('strong');this.note=document.createElement('p');
    this.detail=document.createElement('p');this.detail.className='navigation-target-detail';
    copy.append(this.title,this.note,this.detail);panel.append(this.button,copy);
  }
  update(step,state) {
    // Do not reveal a later tool's point during the preceding model decision.
    const called=['execution','tool','result','outside_tool_wait'].includes(state?.phase);
    const isNavigation=String(step?.arguments?.primitive||'').toLowerCase()==='navigate_to';
    const target=isNavigation&&called?step.arguments.target:null;
    const point=target?.point;
    const valid=Array.isArray(point)&&point.length===2&&point.every(n=>Number.isFinite(n)&&n>=0&&n<=1);
    const item=valid?step.before?.images?.find(im=>im.image_ref===target.image_ref):null;
    const key=JSON.stringify([step?.index,state?.phase,target,item?.file]);
    if(this.key===key)return;this.key=key;this.target=null;this.image=null;
    this.canvas.getContext('2d').clearRect(0,0,this.canvas.width,this.canvas.height);
    this.panel.hidden=!isNavigation;this.button.hidden=true;
    delete this.panel.dataset.imageRef;delete this.panel.dataset.point;
    if(!isNavigation)return;
    this.panel.dataset.step=step.index;
    this.title.textContent='导航目标';
    this.note.textContent=called?'此调用未记录可显示的目标点。':'工具调用时显示实际选择的目标点。';
    this.detail.textContent='';
    if(!valid)return;
    this.panel.dataset.imageRef=target.image_ref;this.panel.dataset.point=JSON.stringify(point);
    this.title.textContent='导航目标 · '+({front:'前视',back:'后视',left:'左视',right:'右视'}[item?.view]||target.image_ref);
    this.note.textContent='调用前原图 · 红圈为模型点击点';
    this.detail.textContent='机器人停在目标附近；红圈不是最终站立位置。';
    if(!item?.file){this.note.textContent='目标原图未归档，无法标注。';return}
    const url=new URL(item.file,location.href);
    if(url.origin!==location.origin||!url.pathname.startsWith(new URL('.',location.href).pathname)){
      this.note.textContent='目标原图路径不可用。';return;
    }
    this.target={...target,item};this.note.textContent='读取目标原图…';
    const image=new Image();
    image.onload=()=>{
      if(this.key!==key)return;
      this.image=image;this.canvas.width=image.naturalWidth;this.canvas.height=image.naturalHeight;
      this.canvas.getContext('2d').drawImage(image,0,0);this.mark(this.canvas,target.image_ref);
      this.button.hidden=false;this.note.textContent='调用前原图 · 红圈为模型点击点';
    };
    image.onerror=()=>{if(this.key===key)this.note.textContent='目标原图读取失败。'};
    image.src=url.href;
  }
  mark(canvas,imageRef) {
    if(!this.target||this.target.image_ref!==imageRef)return;
    const [u,v]=this.target.point,w=canvas.width,h=canvas.height,x=u*(w-1),y=v*(h-1);
    const ctx=canvas.getContext('2d'),r=Math.max(7,Math.min(w,h)*.019);
    ctx.save();
    for(const [color,width] of [['#fff',5],['#e12b35',2.5]]){
      ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.arc(x,y,r,0,2*Math.PI);
      ctx.moveTo(x-r-7,y);ctx.lineTo(x-r+2,y);ctx.moveTo(x+r-2,y);ctx.lineTo(x+r+7,y);
      ctx.moveTo(x,y-r-7);ctx.lineTo(x,y-r+2);ctx.moveTo(x,y+r-2);ctx.lineTo(x,y+r+7);ctx.stroke();
    }
    ctx.fillStyle='#e12b35';ctx.beginPath();ctx.arc(x,y,2.5,0,2*Math.PI);ctx.fill();ctx.restore();
  }
};
