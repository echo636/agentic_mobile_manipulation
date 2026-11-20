"""Check public Manip/expandable children in an actual installed browser."""
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from manipulation_agent.replay import render_replay_page


def fixture():
    steps = []
    for i, name in enumerate(['initialize','manip','move_joints','grasp'], 1):
        steps.append({'index':i,'tool':name,'request_id':str(i),'event_id':'call-'+str(i),
                      'arguments':{'recipe':'countertop_pick'} if name=='manip' else {},
                      'at':f'2026-11-03T00:00:0{i}+00:00',
                      'result_at':f'2026-11-03T00:00:0{6 if name=="manip" else i}+00:00',
                      'status':'passed','tool_seconds':1,'video_start_seconds':{1:0,2:None,3:.5,4:1.5}[i],
                      'before':None,'after':{'revision':i,'images':[]},
                      'new_observation':True,'result':{'ok':True},
                      'parent_request_id':'2' if i>2 else None,
                      'child_request_ids':['3','4'] if name=='manip' else []})
    return {'run_id':'CPU browser fixture — no physical trial','config':{'task':'CPU fixture'},
            'instruction':'UI fixture only; no simulator, video, or physical acceptance.',
            'steps':steps,'model_transcript':[],'video':None,
            'audit':None,'has_public_trace':False,
            'evaluation_offline_only':{'task_success':None}}


SCRIPT = '''<script>
window.addEventListener('load',()=>setTimeout(()=>{
const errors=[],check=(ok,why)=>{if(!ok)errors.push(why)};
try{
document.getElementById('view-video').click();
const all=document.getElementById('show-all');all.checked=true;all.dispatchEvent(new Event('change'));
const details=document.querySelector('.procedure-children');
check(document.querySelectorAll('.conversation-feed>.step-group').length===2,'public groups must be initialize + manip');
check(document.querySelectorAll('.procedure-child').length===2,'missing internal actions');
check(details&&!details.open,'internal actions must start collapsed');
check(details?.textContent.includes('move_joints')&&details.textContent.includes('grasp'),'children missing actual tool names');
check(document.querySelectorAll('#step-track button').length===2,'internal actions leaked into public step track');
check(document.querySelectorAll('#step-select option').length===3,'internal actions leaked into public selector');
const data=JSON.parse(document.getElementById('replay-data').textContent);
data.video={status:'passed',duration_seconds:2.5};
const review=ReplayTiming.review(data);
data.review_timeline=review;
check(!review.segments.some(s=>s.step===2),'parent header duplicated physical duration');
check(review.segments.find(s=>s.step===1&&s.phase==='tool').media_end===.5,'parent marker caused prior action to consume whole video');
check(Math.abs(review.segments.reduce((sum,s)=>sum+s.media_end-s.media_start,0)-2.5)<1e-6,'physical video duration duplicated');
check(ReplayTiming.seekTime(data,'review',1)!==null,'manip cannot seek to first child');
const transcript=window.atomicTestData||null;
details.open=true;check(details.querySelector('.procedure-child').getBoundingClientRect().height>0,'expanded child invisible');
}catch(e){errors.push(String(e))}
const out=document.createElement('pre');out.id='atomic-browser-result';out.textContent=JSON.stringify({passed:errors.length===0,errors});document.body.append(out);
},200));
</script>'''


@unittest.skipUnless(shutil.which('google-chrome'),'installed Chrome required')
class AtomicManipBrowserTests(unittest.TestCase):
    def run_browser(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);page=root/'fixture.html';page.write_text(render_replay_page(data)+SCRIPT)
            result=subprocess.run(['google-chrome','--headless=new','--disable-gpu','--no-sandbox',
                                   '--disable-dev-shm-usage','--disable-background-networking',
                                   '--user-data-dir='+str(root/'chrome'),'--virtual-time-budget=2500',
                                   '--dump-dom',page.as_uri()],capture_output=True,text=True,timeout=45)
            match=re.search(r'<pre id="atomic-browser-result">(.*?)</pre>',result.stdout,re.S)
            self.assertIsNotNone(match,result.stderr[-1500:])
            value=json.loads(html.unescape(match[1]));self.assertTrue(value['passed'],value['errors'])

    def test_parent_is_public_and_children_expand_without_duplicate_timeline(self):
        self.run_browser(fixture())

    def test_show_all_and_expand_work_while_video_is_still_loading(self):
        data=fixture()
        data['video']={'status':'passed','duration_seconds':2.5,'file':'not-yet-loaded-fixture.mp4'}
        self.run_browser(data)


if __name__=='__main__':unittest.main()
