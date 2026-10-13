"""Rendered model-authored goal history, not inferred reasoning or task scoring."""
import json
from pathlib import Path
import unittest
from test_task_notes import tree
from test_replay_browser import sync_playwright, fixture
from manipulation_agent.replay import render_replay_page


@unittest.skipUnless(sync_playwright,'optional Playwright browser dependency')
class GoalReplayBrowserTests(unittest.TestCase):
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
                page.locator('#video-task-notes .goal-tree summary').click()
                self.assertIn('Device on',page.locator('#video-task-notes').inner_text())
                self.assertEqual(page.locator('#video-task-notes').get_attribute('data-version'),'1')
                self.assertFalse(errors,errors)
            finally:browser.close()


if __name__=='__main__':unittest.main()
