"""Browser acceptance for four-view batches, async replay and responsive layout."""
import argparse
import json
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit,unquote
from datetime import datetime,timezone
from playwright.sync_api import sync_playwright


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        for k,v in attrs:
            if (tag=='a' and k=='href') or (tag in {'video','img'} and k=='src'):self.links.append(v)


def main():
    p=argparse.ArgumentParser();p.add_argument('--reports',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--site',default='http://127.0.0.1:8765');a=p.parse_args()
    errors=[];http=[];checked=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1550,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('response',lambda r:http.append([r.status,r.url]) if r.status>=400 else None)
        page.goto(a.site+'/surround_observation.html',wait_until='networkidle')
        data=page.evaluate('JSON.parse(document.getElementById("data").textContent)')
        for i,row in enumerate(data):
            card=page.locator(f'[data-run="{i}"]')
            assert card.locator('.camera-grid img').count()==4
            assert card.locator('.camera-grid img').evaluate_all('xs=>xs.every(x=>x.complete&&x.naturalWidth===512)')
            if len(row['captures'])>1:
                card.locator('.capture').select_option(str(len(row['captures'])-1))
                assert row['captures'][-1]['synchronized_capture']['capture_id'] in card.locator('.capture-meta').text_content()
            card.locator('summary').filter(has_text='精确工具调用').click()
            call=next(j for j,c in enumerate(row['calls']) if c['tool']=='get_observation' and c['result'].get('job',{}).get('status')=='passed')
            card.locator('.call').select_option(str(call))
            assert json.loads(card.locator('.call-data').text_content())==row['calls'][call]
        page.screenshot(path=str(a.reports/'surround_observation_desktop.png'),full_page=True)
        paths=[a.reports/'surround_observation.html']
        for row in data:
            path=a.reports/row['base']/'replay.html';paths.append(path)
            page.goto(a.site+'/'+row['base']+'/replay.html',wait_until='networkidle')
            replay=page.evaluate('JSON.parse(document.getElementById("replay-data").textContent)')
            assert set(page.locator('[data-view]').evaluate_all('xs=>xs.map(x=>x.dataset.view)'))=={'front','back','left','right'}
            assert page.locator('#workflow-panels').is_hidden()
            step=next(s for s in replay['steps'] if (s.get('observation_job') or {}).get('status')=='passed')
            page.locator(f'.timeline-step[data-step="{step["index"]}"]').click()
            assert page.locator('#observation-job-card').is_visible()
            assert json.loads(page.locator('#observation-job').text_content())==step['observation_job']
            for direction in ['front','back','left','right']:
                page.locator(f'[data-view="{direction}"]').click()
                assert '-'+direction+'.jpg' in page.locator('#after-frame img').get_attribute('src')
            quality=None
            if (replay.get('video') or {}).get('status')=='passed':
                video=page.locator('#episode-video');page.wait_for_function('document.getElementById("episode-video").readyState>=2')
                video.evaluate('async v=>{v.muted=true;await v.play()}');page.wait_for_timeout(500);video.evaluate('v=>v.pause()')
                assert video.evaluate('v=>v.error') is None
                quality=video.evaluate('v=>{const q=v.getVideoPlaybackQuality();return {decoded:q.totalVideoFrames,dropped:q.droppedVideoFrames}}')
            checked.append({'run':replay['run_id'],'four_camera_tabs':True,'async_job_exact':True,'video':quality})
        for path in paths:
            parser=Links();parser.feed(path.read_text())
            for href in parser.links:
                u=urlsplit(href)
                if not u.scheme and not u.netloc and u.path:assert (path.parent/unquote(u.path)).exists(),href
        page.set_viewport_size({'width':390,'height':844})
        for path in paths:
            page.goto(a.site+'/'+str(path.relative_to(a.reports)),wait_until='networkidle')
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),str(path)
        page.goto(a.site+'/surround_observation.html',wait_until='networkidle');page.screenshot(path=str(a.reports/'surround_observation_mobile.png'),full_page=True)
        assert not errors,errors;assert not http,http;browser.close()
    result={'status':'passed','at':datetime.now(timezone.utc).isoformat(),'pages':len(paths),'checks':checked,'javascript_errors':errors,'http_errors':http,'mobile_overflow':False}
    a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
