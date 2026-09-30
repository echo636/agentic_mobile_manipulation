import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from manipulation_agent.observations.mock_rgb import MockRGBBackend
from manipulation_agent.observations.boundary import public_observation
from manipulation_agent.records import Recorder
from manipulation_agent.vision_harness import VisionHarness
from manipulation_agent.tools import tool_specs

class RGBBoundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/'episode'
        self.recorder=Recorder(self.output,{'backend':'mock','observation_mode':'rgb_only'})
        self.backend=MockRGBBackend(self.output)
        self.h=VisionHarness(self.backend,self.recorder,profile='workflow')
        self.index=0
    def call(self,tool_name,**args):
        self.index+=1
        return self.h.call(tool_name,args,str(self.index))
    def target(self):return {'image_ref':self.h.snapshot['images'][0]['image_ref'],'point':[0.5,0.5]}
    def act(self,primitive='toggle_on',target=None):
        return self.call('act',primitive=primitive,target=target or self.target(),revision=self.h.revision)
    def test_no_object_truth_in_observation(self):
        obs=self.call('observe')['observation']
        self.assertEqual(set(obs),{'images','observation_mode','revision'})
        self.assertNotIn('PRIVATE_OBJECT_NAME',json.dumps(obs))
    def test_observer_with_truth_fails_closed(self):
        obs=self.backend.observe();obs['objects']=[{'id':'hidden'}]
        with self.assertRaises(RuntimeError):public_observation(obs,0)
    def test_private_error_does_not_leak_names_or_state(self):
        self.backend.fail_next=True;r=self.act()
        self.assertFalse(r['ok'])
        self.assertNotIn('PRIVATE_OBJECT_NAME',json.dumps(r));self.assertNotIn('REAL_STATE',json.dumps(r))
        self.assertEqual(r['observation']['revision'],1)
        self.assertIn('PRIVATE_OBJECT_NAME',(self.output/'events.jsonl').read_text())
    def test_success_effect_does_not_leak_grounding(self):
        r=self.act()
        self.assertTrue(r['ok']);self.assertNotIn('private_object',json.dumps(r));self.assertNotIn('private_pose',json.dumps(r))
    def test_old_image_ref_expires_even_without_physics(self):
        old=self.target();self.call('observe')
        r=self.act(target=old)
        self.assertEqual(r['error']['code'],'stale_image_ref');self.assertEqual(self.backend.steps,0)
    def test_object_id_target_is_rejected(self):
        r=self.call('act',primitive='grasp',target='hidden.n.01_1',revision=0)
        self.assertEqual(r['error']['code'],'invalid_arguments');self.assertEqual(self.backend.steps,0)
    def test_invalid_pixel_is_rejected(self):
        for point in ([1.01,0.5],[-1,0.5],[True,0.5],[0.5], [float('nan'),0.5]):
            r=self.act(target={'image_ref':self.target()['image_ref'],'point':point})
            self.assertFalse(r['ok'])
        self.assertEqual(self.backend.steps,0)
    def test_rgb_bytes_match_metadata(self):
        f=self.h.snapshot['images'][0];data,mime=self.h.image_bytes(f['image_ref'])
        self.assertEqual(hashlib.sha256(data).hexdigest(),f['sha256']);self.assertEqual(mime,'image/png')
        self.assertTrue(data.startswith(b'\x89PNG'))
    def test_skill_library_is_frozen_and_whitelisted(self):
        r=self.call('read_skill',name='visual-manipulation',resource='SKILL.md')
        self.assertTrue(r['ok']);self.assertTrue((self.output/'skill_snapshot/visual-manipulation/SKILL.md').exists())
        bad=self.call('read_skill',name='visual-manipulation',resource='../../run.json')
        self.assertEqual(bad['error']['code'],'unknown_skill_resource')
    def test_false_visual_claim_does_not_pass_task(self):
        result=self.call('finish',outcome='achieved',reason='unfounded claim')
        self.assertTrue(result['closed']);self.assertNotIn('evaluation',result)
        self.assertFalse(self.recorder.run['task_success'])
    def test_tool_surface_has_workflows_and_look(self):
        self.assertEqual({x['name'] for x in tool_specs('workflow')},{'observe','look','act','update_plan','remember','recall','list_skills','read_skill','finish'})
    def test_duplicate_request_does_not_repeat_motion(self):
        args={'primitive':'toggle_on','target':self.target(),'revision':0}
        one=self.h.call('act',args,'repeat');two=self.h.call('act',args,'repeat')
        self.assertEqual(one,two);self.assertEqual(self.backend.steps,1)

if __name__=='__main__':unittest.main()
