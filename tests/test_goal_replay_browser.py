"""Rendered model-authored goal history, not inferred reasoning or task scoring."""
import json
from pathlib import Path
import unittest
from test_task_notes import tree, branching_tree
from test_replay_browser import sync_playwright, fixture
from manipulation_agent.replay import render_replay_page


@unittest.skipUnless(sync_playwright,'optional Playwright browser dependency')
class GoalReplayBrowserTests(unittest.TestCase):
    def test_v2_uneven_tree_dependencies_and_malformed_notes_on_mobile(self):
        data=fixture();state=branching_tree()
        state['goals'][0]['goal']='<img src=x onerror=alert(1)>'
        # Parentage comes from IDs, never list order.
        state['goals'].reverse()
        note={'version':1,'agent_step':0,'content':json.dumps(state)}
        data['steps'][1].update(task_notes=note,is_motor_action=True,agent_step=1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':390,'height':844})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(render_replay_page(data))
                panel=page.locator('#tool-trace-feed .goal-notes')
                self.assertEqual(panel.locator('.goal-inline-tree > .goal-children > .goal-child').count(),3)
                self.assertEqual(panel.locator('[data-goal-id="work"] > .goal-children > .goal-child').count(),2)
                self.assertEqual(panel.locator('[data-goal-id="prepare"] > .goal-children > [data-goal-id="access"]').count(),1)
                self.assertEqual(panel.locator('[data-goal-id="supplies"] .goal-child').count(),0)
                self.assertEqual(panel.locator('.goal-current').get_attribute('data-goal-id'),'access')
                self.assertIn('supplies',panel.locator('[data-goal-id="process"] .goal-dependencies').inner_text())
                self.assertIn('<img',panel.locator('.goal-root').inner_text())
                self.assertEqual(panel.locator('img').count(),0)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                for defect in ('duplicate','orphan','cycle'):
                    malformed=branching_tree()
                    if defect=='duplicate':malformed['goals'].append(dict(malformed['goals'][1]))
                    if defect=='orphan':malformed['goals'][1]['parent_id']='missing'
                    if defect=='cycle':malformed['goals'][1]['parent_id']='access'
                    content=json.dumps(malformed)
                    page.evaluate('(content)=>renderTaskNotes(document.querySelector("#tool-trace-feed .goal-notes"),{version:2,content})',content)
                    self.assertEqual(panel.locator('.goal-child').count(),0,defect)
                    self.assertTrue(panel.locator('.goal-warning').is_visible(),defect)
                    self.assertEqual(panel.locator('.goal-plain').text_content(),content)
                self.assertEqual(errors,[])
            finally:browser.close()

    def test_independent_evidence_tracks_time_and_preserves_violations(self):
        evaluation={'score_type':'custom_joint_terminal_and_public_history_v1',
            'terminal_conditions':{'clean':False},'historical_conditions':{'delivered':True},
            'goal_events':[{'kind':'terminal_state','id':'clean','satisfied':True,'env_step':0},
                           {'kind':'terminal_state','id':'clean','satisfied':False,'env_step':20}],
            'milestone_events':{'delivered':{'env_step':10}},
            'public_constraint_violations':[{'id':'lid_open','env_step':15}]}
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content('<div id="evidence"></div>')
                page.add_script_tag(path=str(Path(__file__).parents[1]/'src/manipulation_agent/replay_assets/goals.js'))
                render=lambda step:page.evaluate('([e,s])=>renderGoalEvidence(document.querySelector("#evidence"),e,s)',[evaluation,step])
                render(5);text=page.locator('#evidence').inner_text()
                self.assertIn('1/1',text);self.assertIn('未提供给模型',text)
                self.assertIn('尚未达成',page.locator('#evidence ul').text_content())
                self.assertNotIn('lid_open',page.locator('#evidence ul').text_content())
                evaluation['score_type'] = 'custom_joint_terminal_and_public_history_v2'
                render(5)
                self.assertTrue(page.locator('#evidence').is_visible())
                self.assertIn('1/1',page.locator('#evidence').inner_text())
                render(25)
                self.assertIn('0/1',page.locator('#evidence').inner_text())
                self.assertIn('已达成',page.locator('#evidence ul').text_content())
                self.assertIn('lid_open',page.locator('#evidence ul').text_content())
                page.evaluate('()=>renderGoalEvidence(document.querySelector("#evidence"),{},25)')
                self.assertTrue(page.locator('#evidence').is_hidden())
                self.assertFalse(errors,errors)
            finally:browser.close()

    def test_notes_render_tree_local_steps_and_plain_control_safely(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content('<div id="notes"></div>')
                page.add_script_tag(path=str(Path(__file__).parents[1]/'src/manipulation_agent/replay_assets/goals.js'))
                state=tree();state['L1']['goal']='<img src=x onerror=alert(1)>'
                notes={'version':1,'agent_step':0,'content':json.dumps(state)}
                page.evaluate('(n)=>renderTaskNotes(document.querySelector("#notes"),n)',notes)
                self.assertEqual(page.locator('.goal-child').count(),1)
                self.assertEqual(page.locator('.goal-local ol li').inner_text(),'toggle_on the visible device')
                self.assertIn('<img',page.locator('.goal-root').inner_text())
                self.assertEqual(page.locator('#notes img').count(),0)
                page.evaluate('(n)=>renderTaskNotes(document.querySelector("#notes"),n)',{'version':2,'content':'My own plan; no hierarchy imposed.'})
                self.assertEqual(page.locator('.goal-child').count(),0)
                self.assertIn('no hierarchy',page.locator('.goal-plain').inner_text())
                self.assertFalse(errors,errors)
            finally:browser.close()

    def test_inline_tree_follows_step_versions_and_video_without_separate_view(self):
        data=fixture();first={'version':1,'agent_step':0,'content':json.dumps(tree())}
        final_state=tree('blocked');final_state['active']['steps']=[]
        final={'version':2,'agent_step':1,'content':json.dumps(final_state)}
        data['steps'][0].update(task_notes=first,task_notes_before=first,agent_step=1,is_motor_action=True)
        data['steps'][1].update(task_notes=final,task_notes_before=first,agent_step=2,is_motor_action=True)
        data['task_note_versions']=[first,final]
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(render_replay_page(data))
                self.assertEqual(page.locator('#view-goals, #goals-workspace, .goal-tree-link').count(),0)
                panels=page.locator('#tool-trace-feed .goal-notes')
                self.assertEqual(panels.count(),2)
                self.assertIn('进行中',panels.nth(0).inner_text())
                self.assertNotIn('受阻',panels.nth(0).inner_text())
                self.assertIn('受阻',panels.nth(1).inner_text())
                self.assertEqual(panels.nth(1).locator('.goal-local ol li').count(),0)
                self.assertTrue(panels.nth(0).locator('.goal-child').is_visible())
                self.assertTrue(all(p.is_hidden() for p in panels.nth(0).locator('.goal-support p').all()))
                page.locator('#view-video').click();page.locator('#step-select').select_option('1')
                notes=page.locator('#video-task-notes')
                self.assertEqual(notes.get_attribute('data-version'),'1')
                self.assertTrue(notes.locator('.goal-child').is_visible())
                notes.locator('.goal-local summary').click()
                self.assertIn('toggle_on',notes.locator('.goal-local').inner_text())
                self.assertEqual(errors,[])
            finally:browser.close()

    def test_legacy_goal_bookmark_lands_on_matching_inline_record(self):
        data=fixture()
        for i,s in enumerate(data['steps'][:2]):
            note={'version':i+1,'agent_step':i,'content':json.dumps(tree('active' if i==0 else 'blocked'))}
            s.update(task_notes=note,is_motor_action=True,agent_step=i+1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('http://replay.test/**',lambda route:route.fulfill(body=render_replay_page(data),content_type='text/html'))
                page.goto('http://replay.test/replay.html?view=goals&version=2')
                target=page.locator('#tool-trace-feed .goal-notes[data-version="2"]')
                page.wait_for_function("document.querySelector('#tool-trace-feed').scrollTop>0")
                self.assertTrue(page.locator('#trace-workspace').is_visible())
                self.assertIn('受阻',target.inner_text())
                self.assertNotIn('view=goals',page.url)
                self.assertEqual(errors,[])
            finally:browser.close()

    def test_compact_tree_keeps_dependencies_and_plain_notes_on_mobile(self):
        state=tree();state['L2'][0]['depends_on']=['missing']
        state['L2'].append({'id':'g2','goal':'Store the device','status':'pending','depends_on':['g1']})
        data=fixture();note={'version':1,'agent_step':0,'content':json.dumps(state)}
        data['steps'][1].update(task_notes=note,is_motor_action=True,agent_step=1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':390,'height':844})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(render_replay_page(data))
                panel=page.locator('#tool-trace-feed .goal-notes')
                self.assertEqual(panel.locator('.goal-child').count(),2)
                self.assertIn('missing',panel.locator('.goal-dependencies').first.inner_text())
                self.assertEqual(panel.locator('.goal-current').get_attribute('data-goal-id'),'g1')
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                page.evaluate("()=>document.documentElement.dataset.masTheme='dark'")
                self.assertNotEqual(panel.evaluate('(e)=>getComputedStyle(e).color'),'rgb(38, 62, 80)')
                page.evaluate("()=>renderTaskNotes(document.querySelector('.goal-notes'),{version:2,content:'Find and move the device.'})")
                self.assertEqual(panel.locator('.goal-child').count(),0)
                self.assertIn('Find and move',panel.inner_text())
                self.assertEqual(errors,[])
            finally:browser.close()


if __name__=='__main__':unittest.main()
