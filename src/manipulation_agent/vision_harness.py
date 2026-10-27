"""RGB-only model boundary, workflow tools, bounded actions and private evaluation."""
import copy
import json
import threading
import time
from dataclasses import asdict
from .contracts import Budget, SkillError, validate
from .deadline import DEADLINE_STEP_SENTINEL, FINISH_GRACE_SECONDS, EpisodeDeadline
from .harness import Harness as LegacyPlanHelpers
from .observations.boundary import public_observation, public_execution_error
from .records import write_json
from .skill_runtime import SkillLibrary
from .tools import REGISTRY, tool_specs
from .tool_context import ToolContext
from .surround import SurroundJobs
from .closure import EpisodeClosure
from .task_notes import TaskNotes

class VisionHarness:
    def __init__(self, backend, recorder, budget=Budget(), *, profile='skills', agent_step_limit=None,
                 goal_skill='hierarchical', require_task_notes=False, unbounded_episode=False):
        if goal_skill not in {'hierarchical','none'}: raise ValueError('Unknown goal skill mode')
        if require_task_notes and profile not in {'skills','workflow'}:
            raise ValueError('Task notes require skills or workflow profile')
        self.goal_skill=goal_skill;self.require_task_notes=require_task_notes
        self.task_notes=TaskNotes(recorder.output)
        if agent_step_limit is not None and (type(agent_step_limit) is not int or agent_step_limit<=0):
            raise ValueError('agent_step_limit must be a positive integer')
        if agent_step_limit is not None and budget.wall_seconds is not None:
            raise ValueError('Step-only episodes require wall_seconds=None')
        if unbounded_episode and (agent_step_limit is not None or budget.wall_seconds is not None):
            raise ValueError('Unbounded episodes require no action or wall-time limit')
        self.unbounded_episode=unbounded_episode
        self.agent_step_limit=agent_step_limit;self.agent_steps=0
        self.agent_steps_exhausted_at=None
        self.backend,self.recorder,self.budget=backend,recorder,budget
        self.owner=threading.get_ident(); self.started=time.monotonic()
        self.deadline=getattr(backend,'deadline',None) or EpisodeDeadline.from_env()
        backend.deadline=self.deadline
        # Select this policy before a standalone client can arm its local clock.
        # Only a clock supplied by the external episode supervisor replaces the
        # legacy counters; standalone / CPU callers retain their stated budgets.
        self.execution_deadline_only=self.deadline.managed
        self.unrestricted_physics=self.execution_deadline_only or agent_step_limit is not None or unbounded_episode
        self.legacy_limits_active=not self.unrestricted_physics
        self._video_finalized=False
        self.revision=self.actions=self.calls=0; self.closed=False
        self.closure=EpisodeClosure(recorder.output)
        self.profile = profile
        self.surround = SurroundJobs(self)
        self.catalog = {t['name']: t for t in tool_specs(profile)}
        self.cache,self.evidence={},{}
        self.skills = None
        if profile == 'workflow':
            self.plan,self.memory=[],{}
        if profile in {'skills','workflow'}:
            self.skills=SkillLibrary(recorder.output,exclude={'goal-decomposition'} if goal_skill=='none' else ())
        recorder.run['config']['agent_profile'] = profile
        recorder.run['config'].update(goal_skill=goal_skill,require_task_notes=require_task_notes)
        recorder.run['episode_deadline_unix']=self.deadline.unix
        self.deadline.check()
        self.snapshot=self.refresh()
        recorder.run.update(backend=backend.provenance(),budget=asdict(budget),skill_bundle_sha256=self.skills.digest if self.skills else None,
                            observation_contract="rgb_four_camera_same_state_v1" if 'capture' in self.snapshot else "rgb_only_v1")
        recorder.run['execution_limit_policy']={'clock':'episode_execution_excludes_initialization',
            'managed_action_step_limit':'execution_deadline_only',
            'unmanaged_action_step_limit':budget.max_steps_per_action,
            'finish_interrupt':'cooperative_owner_thread_checkpoint',
            'episode_budget_policy':'execution_deadline_only' if self.execution_deadline_only else 'legacy_counters_and_clock',
            'limits_still_configured':({} if self.execution_deadline_only else
                {'actions':budget.max_actions,'tool_calls':budget.max_calls,
                 'environment_steps':budget.max_sim_steps}),
            'legacy_limits_active':not self.execution_deadline_only}
        if agent_step_limit is not None:
            recorder.run.update(agent_steps=0,max_agent_steps=agent_step_limit)
            recorder.run['execution_limit_policy']={
                'clock':'execution_start_only_no_wall_deadline','episode_budget_policy':'agent_steps',
                'max_agent_steps':agent_step_limit,'counted_tools':['act','look'],
                'failed_attempts_count':True,'cached_replays_count':False,
                'episode_wall_seconds':None,'legacy_limits_active':False,
                'managed_action_step_limit':'no_physics_step_cutoff',
                'finish_grace_seconds':FINISH_GRACE_SECONDS}
        if unbounded_episode:
            recorder.run.update(agent_steps=0,max_agent_steps=None)
            recorder.run['execution_limit_policy']={
                'clock':'execution_start_only_no_wall_deadline','episode_budget_policy':'unbounded',
                'max_agent_steps':None,'counted_tools':['act','look'],
                'failed_attempts_count':True,'cached_replays_count':False,
                'episode_wall_seconds':None,'legacy_limits_active':False,
                'managed_action_step_limit':'no_physics_step_cutoff'}
        write_json(recorder.output/'run.json',recorder.run)
        recorder.event('episode_started',{'observation':self.snapshot})

    def tool_specs(self): return list(self.catalog.values())

    def sync_execution_clock(self): return LegacyPlanHelpers.sync_execution_clock(self)
    def start_standalone_clock(self): return LegacyPlanHelpers.start_standalone_clock(self)

    def initialize(self):
        """Deliver the ready episode snapshot without reset, physics or rendering."""
        result={'initialized': True, 'observation': copy.deepcopy(self.snapshot)}
        if hasattr(self.backend,'local_manipulation'):
            result['control']={'manipulation_protocol':
                'target_locked_approach_v2' if self.backend.local_manipulation else 'explicit_navigation_v1',
                'local_base_adjustment':bool(self.backend.local_manipulation)}
        if 'task_notes' in self.catalog: result['notes']=self.task_notes.read()
        return result

    def refresh(self):
        self.snapshot=public_observation(self.backend.observe(),self.revision)
        return copy.deepcopy(self.snapshot)

    def image_bytes(self, image_ref): return self.backend.image_bytes(image_ref)

    def request_finish(self, arguments, request_id):
        """Accept closure intent from HTTP without touching the simulator."""
        if self.closed or not isinstance(request_id,str) or not 0<len(request_id)<=200:
            return
        try:
            validate(arguments,self.catalog['finish']['inputSchema'])
            fingerprint=json.dumps(['finish',arguments],sort_keys=True,allow_nan=False)
        except (SkillError,ValueError,TypeError):
            return
        cached=self.cache.get(request_id)
        if cached is not None and cached[0]!=fingerprint:
            return
        self.deadline.request_stop()

    def tick_background(self):
        if threading.get_ident()!=self.owner: raise RuntimeError('Simulator owner thread required')
        self.surround.tick()

    def agent_step_closure_due(self):
        return (self.agent_steps_exhausted_at is not None and
                time.monotonic()-self.agent_steps_exhausted_at>=FINISH_GRACE_SECONDS)

    def call(self,name,arguments,request_id, *, origin='controller'):
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
        self.sync_execution_clock()
        self.recorder.event('tool_call',{'name':name,'arguments':arguments,'request_id':request_id,'origin':origin})
        self._call_origin=origin
        if hasattr(self.backend, 'mark_video_tool'):
            self.backend.mark_video_tool(name, arguments, request_id)
        try:
            if self.closed: raise SkillError('episode_closed','The episode is closed')
            if name not in self.catalog: raise SkillError('unknown_tool','Tool is not enabled in this agent profile')
            if (self.agent_step_limit is not None or self.unbounded_episode) and name in {'act','look'}:
                if self.agent_step_limit is not None and self.agent_steps>=self.agent_step_limit:
                    raise SkillError('agent_step_budget_exhausted','No action steps remain. Review the latest RGB and call finish.')
                # Charge each new agent action request, even if validation or
                # execution fails. Cached transport retries returned above.
                self.agent_steps+=1
                self.recorder.run['agent_steps']=self.agent_steps
                write_json(self.recorder.output/'run.json',self.recorder.run)
                self.recorder.event('agent_step_admitted',{'step':self.agent_steps,
                    'limit':self.agent_step_limit,'tool':name,'request_id':request_id})
            # Compatibility for archived callers; model schema explicitly carries
            # nullable action options. Original input remains in the event ledger.
            if name=='act' and isinstance(arguments,dict):
                arguments={'placement_yaw_degrees':None,'wait_seconds':None,**arguments}
            validate(arguments,self.catalog[name]['inputSchema'])
            if name in {'act','look'} and self.require_task_notes and not self.task_notes.state['content']:
                raise SkillError('task_notes_required','Before acting, write your intended approach with task_notes. Choose your own note format.')
            if name!='finish': self.deadline.check()
            if name!='finish' and (
                    (self.legacy_limits_active and self.calls>self.budget.max_calls) or
                    (self.budget.wall_seconds is not None and not self.deadline.managed and time.monotonic()-self.started>self.budget.wall_seconds)):
                raise SkillError('budget_exhausted','Call/time budget exhausted; finish the episode')
            execution_args = {k:v for k,v in arguments.items() if k != 'decision'}
            context=ToolContext(self, request_id=request_id, tool_name=name)
            result={'ok':True,**REGISTRY[name].handler(context,**execution_args)}
        except SkillError as exc:
            result={'ok':False,'error':{'code':exc.code,'message':str(exc)}}
            result['observation']=self.snapshot
        # Report the existing clock after execution/rendering, without arming,
        # extending or adding a limit. This is runtime metadata, never scene GT.
        remaining = self.deadline.remaining()
        if self.agent_step_limit is not None and not self.closed:
            result['execution_budget']={'scope':'agent_steps','steps_used':self.agent_steps,
                'steps_remaining':max(0,self.agent_step_limit-self.agent_steps),
                'max_steps':self.agent_step_limit,'counted_tools':['act','look'],'wall_time_limit_seconds':None}
            if self.agent_steps>=self.agent_step_limit:
                # Begin closure grace only after the last admitted action and
                # its fresh RGB have returned; never interrupt that action.
                if self.agent_steps_exhausted_at is None:self.agent_steps_exhausted_at=time.monotonic()
                result['execution_budget']['message']='No action steps remain. Review this observation and call finish.'
        elif self.unbounded_episode and not self.closed:
            result['execution_budget']={'scope':'unbounded','steps_used':self.agent_steps,
                'steps_remaining':None,'max_steps':None,'counted_tools':['act','look'],
                'wall_time_limit_seconds':None}
        elif remaining is not None and not self.closed:
            result['execution_budget'] = {
                'remaining_seconds': round(remaining, 1),
                'scope': 'execution_wall_time_excluding_initialization',
            }
        event_id=self.recorder.event('tool_result',{'name':name,'request_id':request_id,'result':result,'origin':origin})
        result['evidence_id']=event_id
        self.evidence[event_id]={'name':'act' if name in {'act','look'} else name,'ok':result['ok'],'revision':self.revision}
        self.cache[request_id]=(fingerprint,copy.deepcopy(result))
        if name=='finish' and result.get('closed'):
            self.closure.advance('result_ready')
        # Batch archive / the CPU replay worker renders after the RPC returns.
        # Large evaluator records must not delay closure or cached finish replies.
        return result

    def perform(self,primitive,target,revision,*,_parent_request_id=None,**kwargs):
        self.deadline.check()
        if revision!=self.revision: raise SkillError('stale_observation','Use the latest observation revision')
        if self.legacy_limits_active and (
                self.actions>=self.budget.max_actions or self.backend.steps>=self.budget.max_sim_steps):
            raise SkillError('budget_exhausted','Action/step budget exhausted')
        no_target=primitive in {'look','release','wait'}
        if no_target!=(target is None): raise SkillError('invalid_target','Use a current RGB point, or null for release/wait')
        if target and target['image_ref'] not in {i['image_ref'] for i in self.snapshot['images']}:
            raise SkillError('stale_image_ref','Select a point in the latest returned image')
        self.actions+=1
        remaining=(DEADLINE_STEP_SENTINEL if self.unrestricted_physics else self.budget.max_sim_steps)-self.backend.steps
        # A managed episode has one execution deadline. Counters remain recorded,
        # but neither episode totals nor the legacy per-action limit end it early.
        limit=remaining if self.unrestricted_physics or self.deadline.managed else min(self.budget.max_steps_per_action,remaining)
        error=None
        self.backend._active_request_id=_parent_request_id
        try:
            details=self.backend.execute_visual(primitive,target,limit,**kwargs)
            self.recorder.event('private_executor_result',{'primitive':primitive,'details':details})
        except SkillError as exc:
            self.recorder.event('private_executor_error',{'code':exc.code,'detail':str(exc)})
            error=public_execution_error(exc)
        finally:
            self.backend._active_request_id=None
            self.revision+=1
            # A timed-out action may have changed the world. Invalidate its RGB,
            # but do not spend the expired task budget rendering another capture.
            if not self.deadline.expired and not self.deadline.stop_requested: self.refresh()
        if error: return {'ok':False,'error':error,'observation':self.snapshot,'actions_used':self.actions}
        effect={'primitive':primitive,'status':'completed','verification':'executor_operation_only'}
        if primitive=='cut':
            # Report this action's transformation, without object/material IDs
            # or task-goal state. Particle counts are not physical piece counts.
            if details.get('transition')=='SlicingRule':
                effect['cut']={'result':'sliced','pieces_created':int(details['created_objects'])}
            elif details.get('transition')=='DicingRule':
                effect['cut']={'result':'diced_material'}
        if primitive in {'pour','scoop'}:
            materials=details.get('materials',[])
            if materials:
                remaining=details.get('source_has_more_material')
                effect['transfer']={
                    'portion_transferred':True,
                    'source_has_more_material':remaining if isinstance(remaining,bool) else None,
                    'held_object_retained':True,
                    'message':'A material portion was transferred. One operation need not empty the source; inspect the returned RGB.'}
        local=details.get('local_manipulation')
        if local:
            effect['local_adjustment']={'repositions':int(local['repositions']),
                'primitive_attempts':int(local['primitive_attempts']),
                'departure':local['departure'],
                'operation_committed':True,
                'recovery_required':local['departure']!='checked',
                'message':('Operation completed. Local departure is not certified after bounded recovery; inspect RGB and select a different navigation approach. Do not repeat the completed operation.'
                           if local['departure']!='checked' else
                           'Operation completed on the same selected target after bounded local preparation.')}
        if primitive=='navigate_to':
            # Only an action outcome crosses the RGB boundary, never private
            # coordinates, target distances, object identities or map geometry.
            messages={
                'reached':'Reached the selected point. Inspect the updated RGB.',
                'approached':'Moved to a feasible approach toward the selected point. This does not verify manipulation reach; inspect the updated RGB.',
                'already_at_closest_approach':'Already at the closest approach found by this navigation search. Repeating the same click will not move closer; inspect the updated RGB for an alternative.',
            }
            status=details.get('nav_status')
            if status in messages:effect.update(navigation_status=status,message=messages[status])
            handoff=details.get('manipulation_handoff')
            handoffs={
                'travel_only':'Ground-point travel completed. Select the intended object in the updated RGB for local manipulation.',
                'selected_surface_nearby':'The selected surface is visible and nearby. Request the intended operation on the updated RGB; its local controller checks the actual manipulation stance.',
                'within_local_range':'Reached a general approach within local adjustment range. Select the intended operation in the updated RGB; its local controller will check and adjust the stance as needed.',
                'needs_closer_approach':'The selected surface is still outside immediate reach. Local manipulation may approach within its bounded range; a distant target needs a closer general approach.',
            }
            if handoff in handoffs:effect.update(manipulation_handoff=handoff,message=handoffs[handoff])
        return {'effect':effect,
                'observation':self.snapshot,'actions_used':self.actions}

    def update_plan(self,reason,subgoals): return LegacyPlanHelpers._tool_update_plan(self,reason,subgoals)
    def remember(self,key,text,revision): return LegacyPlanHelpers._tool_remember(self,key,text,revision)
    def recall(self): return LegacyPlanHelpers._tool_recall(self)
    def finish(self,outcome,reason):
        self.surround.stop_for_finish()
        return LegacyPlanHelpers._tool_finish(self,outcome,reason,render=False)

    def finalize_recording(self):
        """Offline closure after the finish reply; never replaces an evaluator result."""
        if self._video_finalized or not hasattr(self.backend,'finalize_video'): return
        self._video_finalized=True
        try:
            self.recorder.run['video']=self.backend.finalize_video()
        except Exception as exc:
            video_path=self.recorder.output/'video.json'
            try: video=json.loads(video_path.read_text()) if video_path.exists() else {}
            except (OSError,ValueError): video={}
            video.update(status='failed',failure_type=type(exc).__name__,failure=str(exc))
            self.recorder.run['video']=video
            self.recorder.event('video_failure',{'type':type(exc).__name__,'message':str(exc)})
        write_json(self.recorder.output/'run.json',self.recorder.run)
