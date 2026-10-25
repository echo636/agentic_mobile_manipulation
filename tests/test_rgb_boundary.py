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
        self.assertEqual(set(obs),{'images','observation_mode','revision','capture'})
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
    def test_transfer_failure_categories_survive_the_public_boundary(self):
        from manipulation_agent.contracts import SkillError
        for code, phrase in [('same_transfer_vessel','same object'),
                             ('invalid_transfer_destination','receiving object'),
                             ('material_dispenser_off','switched off'),
                             ('material_source_empty','no accessible loose material')]:
            def fail(*args, **kwargs):
                raise SkillError(code, 'PRIVATE_OBJECT_NAME / private geometry and diagnostics')
            self.backend.execute_visual=fail
            result=self.act('pour')
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'],code)
            self.assertIn(phrase,result['error']['message'])
            self.assertNotIn('PRIVATE_OBJECT_NAME',json.dumps(result))
            self.assertNotIn('private geometry',json.dumps(result))
    def test_transfer_reports_partial_source_without_private_material_identity(self):
        for remaining, expected in ((0,False),(12,True),(None,None)):
            self.backend.execute_visual=lambda *a,**kw:{'materials':[{
                'material':'PRIVATE_MATERIAL_SYSTEM','moved_particles':512,
                'source_remaining_particles':remaining}], 'source_has_more_material':expected, 'private_pose':[1,2,3]}
            result=self.act('pour')
            self.assertIs(result['effect']['transfer']['source_has_more_material'],expected)
            self.assertTrue(result['effect']['transfer']['held_object_retained'])
            self.assertNotIn('PRIVATE_MATERIAL_SYSTEM',json.dumps(result))
            self.assertNotIn('moved_particles',json.dumps(result))
    def test_navigation_partial_approach_is_public_but_geometry_stays_private(self):
        for status in ('reached','approached','already_at_closest_approach'):
            self.backend.execute_visual=lambda *a,**kw:{'nav_status':status,'private_pose':[1,2,3],
                'target_projection_distance_m':2.5,'private_object':'hidden'}
            result=self.act('navigate_to')
            self.assertEqual(result['effect']['navigation_status'],status)
            self.assertNotIn('private_pose',json.dumps(result))
            self.assertNotIn('target_projection_distance_m',json.dumps(result))
            self.assertNotIn('hidden',json.dumps(result))
    def test_invalid_private_diagnostic_does_not_abort_act_or_scoring(self):
        execute = self.backend.execute_visual
        diagnostic = {'surface_point_distance_m': float('nan'),
                      'visual_triangle_position': [float('inf'), -float('inf'), 1.]}
        def nonfinite_details(*args, **kwargs):
            return {**execute(*args, **kwargs), 'private_grounding': diagnostic}
        self.backend.execute_visual = nonfinite_details
        result = self.act()
        self.assertTrue(result['ok'])
        self.assertEqual(result['observation']['revision'], 1)
        self.assertNotIn('private_grounding', json.dumps(result, allow_nan=False))
        finished = self.call('finish', outcome='achieved', reason='test')
        self.assertTrue(finished['closed'])
        self.assertTrue(self.recorder.run['task_success'])
        # Diagnostic serialization must not modify the original backend result.
        self.assertNotEqual(diagnostic['surface_point_distance_m'], diagnostic['surface_point_distance_m'])
        events = [json.loads(line, parse_constant=lambda x: self.fail('Nonstandard JSON: '+x))
                  for line in (self.output/'events.jsonl').read_text().splitlines()]
        private = next(e for e in events if e['kind'] == 'private_executor_result')
        self.assertIsNone(private['details']['private_grounding']['surface_point_distance_m'])
        self.assertEqual(private['serialization_warnings'], [
            {'path': ['details','private_grounding','surface_point_distance_m'], 'value': 'nan'},
            {'path': ['details','private_grounding','visual_triangle_position',0], 'value': 'inf'},
            {'path': ['details','private_grounding','visual_triangle_position',1], 'value': '-inf'}])
        self.assertTrue(any(e['kind'] == 'independent_evaluation' for e in events))

    def test_handoff_exposes_only_approved_readiness_category(self):
        for handoff in ('travel_only','selected_surface_nearby','within_local_range','needs_closer_approach','PRIVATE_OBJECT'):
            self.backend.execute_visual=lambda *a,**kw:{'nav_status':'approached','manipulation_handoff':handoff}
            result=self.act('navigate_to')
            if handoff=='PRIVATE_OBJECT':
                self.assertNotIn(handoff,json.dumps(result))
            else:self.assertEqual(result['effect']['manipulation_handoff'],handoff)
    def test_nonfinite_evaluator_values_are_not_silently_sanitized(self):
        with self.assertRaises(ValueError):
            self.recorder.event('independent_evaluation', {'q_score': float('nan')})
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
        self.assertEqual({x['name'] for x in tool_specs('workflow')},{'initialize','observe','look','act','update_plan','remember','recall','list_skills','read_skill','finish','start_observation','get_observation','cancel_observation','task_notes'})
    def test_duplicate_request_does_not_repeat_motion(self):
        args={'primitive':'toggle_on','target':self.target(),'revision':0}
        one=self.h.call('act',args,'repeat');two=self.h.call('act',args,'repeat')
        self.assertEqual(one,two);self.assertEqual(self.backend.steps,1)

if __name__=='__main__':unittest.main()
