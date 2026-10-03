"""RGB-only model boundary, workflow tools, bounded actions and private evaluation."""
import copy
import json
import threading
import time
from dataclasses import asdict
from .contracts import Budget, SkillError, validate
from .harness import Harness as LegacyPlanHelpers
from .observations.boundary import public_observation, public_execution_error
from .records import write_json
from .skill_runtime import SkillLibrary
from .tools import REGISTRY, tool_specs
from .surround import SurroundJobs

class VisionHarness:
    def __init__(self, backend, recorder, budget=Budget(), *, profile='skills'):
        self.backend,self.recorder,self.budget=backend,recorder,budget
        self.owner=threading.get_ident(); self.started=time.monotonic()
        self.revision=self.actions=self.calls=0; self.closed=False
        self.profile = profile
        self.surround = SurroundJobs(self)
        self.catalog = {t['name']: t for t in tool_specs(profile)}
        self.cache,self.evidence={},{}
        self.skills = None
        if profile == 'workflow':
            self.plan,self.memory=[],{}
        if profile in {'skills','workflow'}:
            self.skills=SkillLibrary(recorder.output)
        recorder.run['config']['agent_profile'] = profile
        self.snapshot=self.refresh()
        recorder.run.update(backend=backend.provenance(),budget=asdict(budget),skill_bundle_sha256=self.skills.digest if self.skills else None,
                            observation_contract="rgb_four_camera_same_state_v1" if 'capture' in self.snapshot else "rgb_only_v1")
        write_json(recorder.output/'run.json',recorder.run)
        recorder.event('episode_started',{'observation':self.snapshot})

    def tool_specs(self): return list(self.catalog.values())

    def refresh(self):
        self.snapshot=public_observation(self.backend.observe(),self.revision)
        return copy.deepcopy(self.snapshot)

    def image_bytes(self, image_ref): return self.backend.image_bytes(image_ref)

    def tick_background(self):
        if threading.get_ident()!=self.owner: raise RuntimeError('Simulator owner thread required')
        self.surround.tick()

    def call(self,name,arguments,request_id):
        if threading.get_ident()!=self.owner: raise RuntimeError('Simulator owner thread required')
        if not isinstance(request_id,str) or not request_id or len(request_id)>200:
            return {'ok':False,'error':{'code':'invalid_request_id','message':'A bounded request ID is required'}}
        try: fingerprint=json.dumps([name,arguments],sort_keys=True,allow_nan=False)
        except (ValueError,TypeError): return {'ok':False,'error':{'code':'invalid_arguments','message':'Use finite JSON values'}}
        if request_id in self.cache:
            old,result=self.cache[request_id]
            if old!=fingerprint: return {'ok':False,'error':{'code':'request_id_conflict','message':'Use a new request ID'}}
            return copy.deepcopy(result)
        self.calls+=1
        self.recorder.event('tool_call',{'name':name,'arguments':arguments,'request_id':request_id})
        if hasattr(self.backend, 'mark_video_tool'):
            self.backend.mark_video_tool(name, arguments, request_id)
        try:
            if self.closed: raise SkillError('episode_closed','The episode is closed')
            if name not in self.catalog: raise SkillError('unknown_tool','Tool is not enabled in this agent profile')
            # Compatibility for archived callers; model schema explicitly carries
            # nullable action options. Original input remains in the event ledger.
            if name=='act' and isinstance(arguments,dict):
                arguments={'placement_yaw_degrees':None,'wait_seconds':None,**arguments}
            validate(arguments,self.catalog[name]['inputSchema'])
            if name!='finish' and (self.calls>self.budget.max_calls or time.monotonic()-self.started>self.budget.wall_seconds):
                raise SkillError('budget_exhausted','Call/time budget exhausted; finish the episode')
            execution_args = {k:v for k,v in arguments.items() if k != 'decision'}
            result={'ok':True,**REGISTRY[name].handler(self,**execution_args)}
        except SkillError as exc:
            result={'ok':False,'error':{'code':exc.code,'message':str(exc)}}
            result['observation']=self.snapshot
        event_id=self.recorder.event('tool_result',{'name':name,'request_id':request_id,'result':result})
        result['evidence_id']=event_id
        self.evidence[event_id]={'name':'act' if name in {'act','look'} else name,'ok':result['ok'],'revision':self.revision}
        self.cache[request_id]=(fingerprint,copy.deepcopy(result))
        # Batch archive / the CPU replay worker renders after the RPC returns.
        # Large evaluator records must not delay closure or cached finish replies.
        return result

    def perform(self,primitive,target,revision,**kwargs):
        if revision!=self.revision: raise SkillError('stale_observation','Use the latest observation revision')
        if self.actions>=self.budget.max_actions or self.backend.steps>=self.budget.max_sim_steps:
            raise SkillError('budget_exhausted','Action/step budget exhausted')
        no_target=primitive in {'look','release','wait'}
        if no_target!=(target is None): raise SkillError('invalid_target','Use a current RGB point, or null for release/wait')
        if target and target['image_ref'] not in {i['image_ref'] for i in self.snapshot['images']}:
            raise SkillError('stale_image_ref','Select a point in the latest returned image')
        self.actions+=1
        limit=min(self.budget.max_steps_per_action,self.budget.max_sim_steps-self.backend.steps)
        error=None
        try:
            details=self.backend.execute_visual(primitive,target,limit,**kwargs)
            self.recorder.event('private_executor_result',{'primitive':primitive,'details':details})
        except SkillError as exc:
            self.recorder.event('private_executor_error',{'code':exc.code,'detail':str(exc)})
            error=public_execution_error(exc)
        finally:
            self.revision+=1; self.refresh()
        if error: return {'ok':False,'error':error,'observation':self.snapshot,'actions_used':self.actions}
        return {'effect':{'primitive':primitive,'status':'completed','verification':'executor_operation_only'},
                'observation':self.snapshot,'actions_used':self.actions}

    def update_plan(self,reason,subgoals): return LegacyPlanHelpers._tool_update_plan(self,reason,subgoals)
    def remember(self,key,text,revision): return LegacyPlanHelpers._tool_remember(self,key,text,revision)
    def recall(self): return LegacyPlanHelpers._tool_recall(self)
    def finish(self,outcome,reason):
        self.surround.stop_for_finish()
        if hasattr(self.backend, 'finalize_video'):
            self.recorder.run['video'] = self.backend.finalize_video()
        return LegacyPlanHelpers._tool_finish(self,outcome,reason,render=False)
