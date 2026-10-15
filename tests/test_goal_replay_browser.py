"""Rendered model-authored goal history, not inferred reasoning or task scoring."""
import json
from pathlib import Path
import unittest
from test_task_notes import tree
from test_replay_browser import sync_playwright, fixture
from manipulation_agent.replay import render_replay_page


@unittest.skipUnless(sync_playwright,'optional Playwright browser dependency')
class GoalReplayBrowserTests(unittest.TestCase):
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

    def test_full_replay_keeps_plan_visible_at_its_action_step(self):
        data=fixture();notes={'version':1,'agent_step':0,'content':json.dumps(tree())}
        action=data['steps'][1]
        action.update(task_notes_before=notes,task_notes=notes,agent_step=1,is_motor_action=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(render_replay_page(data))
                self.assertGreater(page.locator('#tool-trace-feed .goal-notes').count(),0)
                self.assertIn('agent step 1',page.locator('#tool-trace-feed').inner_text())
                page.locator('#view-video').click();page.locator('#step-select').select_option('1')
                page.locator('#video-task-notes .goal-tree > summary').click()
                self.assertIn('Device on',page.locator('#video-task-notes').inner_text())
                self.assertEqual(page.locator('#video-task-notes').get_attribute('data-version'),'1')
                self.assertFalse(errors,errors)
            finally:browser.close()

    def test_goal_history_revisions_keep_matching_actions_and_plain_control(self):
        data=fixture()
        first={'version':1,'agent_step':0,'content':json.dumps(tree())}
        final_state=tree('blocked');final_state['active']['steps']=[]
        final={'version':2,'agent_step':1,'content':json.dumps(final_state)}
        data['task_note_versions']=[first,final]
        data['steps'][1].update(task_notes_before=first,task_notes=first,agent_step=1,is_motor_action=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.set_content(render_replay_page(data));page.locator('#view-goals').click()
                self.assertEqual(page.locator('.gh-current').get_attribute('data-version'),'1')
                self.assertIn('进行中',page.locator('.gh-current').inner_text())
                self.assertNotIn('受阻',page.locator('.gh-current').inner_text())
                self.assertEqual(page.locator('.gh-action').count(),1)
                page.locator('.gh-action button').click()
                self.assertEqual(page.locator('#step-select').input_value(),'1')
                page.locator('#view-goals').click();page.locator('.gh-version').nth(1).click()
                self.assertIn('进行中 → 受阻',page.locator('.gh-diff').inner_text())
                self.assertEqual(page.locator('.gh-action').count(),0)
                self.assertEqual(page.locator('.gh-current .goal-local ol li').count(),0)
                data['task_note_versions']=[{'version':1,'agent_step':0,'content':'Find and move the device.'}]
                page.set_content(render_replay_page(data));page.locator('#view-goals').click()
                self.assertEqual(page.locator('.gh-current .goal-child').count(),0)
                self.assertIn('自由格式',page.locator('.gh-current').inner_text())
                self.assertFalse(errors,errors)
            finally:browser.close()

    def test_goal_diff_preserves_removal_and_reopening(self):
        old=tree('done');new=tree('active')
        old['L2'].append({'id':'g2','goal':'Unused child','status':'pending'})
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.set_content('<div id="goals"></div>')
                assets=Path(__file__).parents[1]/'src/manipulation_agent/replay_assets'
                for name in ['goals.js','goal_history.js']:page.add_script_tag(path=str(assets/name))
                page.evaluate('(ns)=>new ReplayGoalHistory(document.querySelector("#goals"),{task_note_versions:ns,steps:[]})',
                    [{'version':1,'agent_step':0,'content':json.dumps(old)},
                     {'version':2,'agent_step':1,'content':json.dumps(new)}])
                page.locator('.gh-version').nth(1).click();diff=page.locator('.gh-diff').inner_text()
                self.assertIn('认为已完成 → 进行中',diff)
                self.assertIn('移除不代表完成',diff)
            finally:browser.close()


if __name__=='__main__':unittest.main()
