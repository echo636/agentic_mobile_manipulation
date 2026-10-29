"""RGB-only model boundary, workflow tools, bounded actions and private evaluation."""
import copy
import json
import os
import threading
import time
from dataclasses import asdict
from .contracts import Budget, SkillError, validate
from .deadline import DEADLINE_STEP_SENTINEL, EpisodeDeadline
from .harness import Harness as LegacyPlanHelpers
from .observations.boundary import public_observation, public_execution_error, public_action_effect
from .records import write_json
from .skill_runtime import SkillLibrary
from .tools import REGISTRY, tool_specs, PLANNING_PROFILES
from .tool_context import ToolContext
from .surround import SurroundJobs

TIME_EXHAUSTION_CLAIMS = ('execution window', 'time window', 'time budget',
                          'time limit', 'deadline', 'out of time',
                          'running out of time', 'available time',
                          '时间不够', '时间不足', '时限', '时间窗口')
SESSION_EXHAUSTION_CLAIMS = ('within this session', 'session time limit',
                             'session budget exhausted')
TERMINAL_ACTION_ERRORS = frozenset({'navigation_invalid_start', 'physics_instability'})
SUPERVISOR_FINISH_ID = 'demo-supervisor-finish'

class VisionHarness:
    def __init__(self, backend, recorder, budget=Budget(), *, profile='skills'):
        self.backend,self.recorder,self.budget=backend,recorder,budget
        self.owner=threading.get_ident(); self.started=time.monotonic()
        self.deadline=getattr(backend,'deadline',None) or EpisodeDeadline.from_env()
        backend.deadline=self.deadline
        # Select this policy before a standalone client can arm its local clock.
        # Only a clock supplied by the external episode supervisor replaces the
        # legacy counters; standalone / CPU callers retain their stated budgets.
        self.execution_deadline_only=self.deadline.managed
        self._video_finalized=False
        self.revision=self.actions=self.calls=0; self.closed=False
        self._last_action_error=None
        # Opt-in native protocol; archived runs retain their accepted-blocked rule.
        self.correct_false_clock_claims = os.environ.get('MAS_REJECT_FALSE_TIME_CLAIMS') == '1'
        self.profile = profile
        self.surround = SurroundJobs(self)
        self.catalog = {t['name']: t for t in tool_specs(profile)}
        self.cache,self.evidence={},{}
        self.skills = None
        if profile=='workflow' or profile in PLANNING_PROFILES:
            self.plan,self.memory=[],{}
        if profile in {'skills','workflow','planning'}:
            self.skills=SkillLibrary(recorder.output)
        recorder.run['config']['agent_profile'] = profile
        recorder.run['episode_deadline_unix']=self.deadline.unix
        self.deadline.check()
        self.snapshot=self.refresh()
        recorder.run.update(backend=backend.provenance(),budget=asdict(budget),skill_bundle_sha256=self.skills.digest if self.skills else None,
                            observation_contract=("rgb_semantic_magic_v1" if profile=='semantic' else
                                                  "rgb_four_camera_same_state_v1" if 'capture' in self.snapshot else "rgb_only_v1"))
        recorder.run['execution_limit_policy']={'clock':'episode_execution_excludes_initialization',
            'managed_action_step_limit':'max_actions_and_max_sim_steps',
            'unmanaged_action_step_limit':budget.max_steps_per_action,
            'finish_interrupt':'cooperative_owner_thread_checkpoint',
            'episode_budget_policy':'action_steps_with_wall_watchdog',
            'limits_still_configured':{'actions':budget.max_actions,'tool_calls':budget.max_calls,
                                       'environment_steps':budget.max_sim_steps},
            'legacy_limits_active':True}
        recorder.run['termination_contract']={
            'false_time_claims': ('clock_claim_correction_v1' if self.correct_false_clock_claims
                                 else 'legacy_demo_only'),
            'early_blocked_guard': bool(getattr(backend,'high_level_harness',False)),
            'private_achieved_feedback': bool(getattr(backend,'high_level_harness',False))}
        write_json(recorder.output/'run.json',recorder.run)
        recorder.event('episode_started',{'observation':self.snapshot})

    def tool_specs(self): return list(self.catalog.values())

    def sync_execution_clock(self): return LegacyPlanHelpers.sync_execution_clock(self)
    def start_standalone_clock(self): return LegacyPlanHelpers.start_standalone_clock(self)

    def initialize(self):
        """Deliver the ready episode snapshot without reset, physics or rendering."""
        return {'initialized': True, 'observation': copy.deepcopy(self.snapshot)}

    def refresh(self):
        self.snapshot=public_observation(self.backend.observe(),self.revision)
        return copy.deepcopy(self.snapshot)

    def image_bytes(self, image_ref): return self.backend.image_bytes(image_ref)

    def _false_time_exhaustion_claim(self, outcome, reason):
        """Reject only a concrete false clock claim, without using goal state."""
        remaining=self.deadline.remaining()
        enabled = getattr(self.backend,'demo_motion',False) or self.correct_false_clock_claims
        native_limits_available = (self.actions < self.budget.max_actions
                                   and self.calls < self.budget.max_calls
                                   and self.backend.steps < self.budget.max_sim_steps
                                   and not self.deadline.stop_requested)
        terms = TIME_EXHAUSTION_CLAIMS + (SESSION_EXHAUSTION_CLAIMS
                                        if self.correct_false_clock_claims else ())
        return (enabled and outcome=='blocked'
                and (not self.correct_false_clock_claims or native_limits_available)
                and remaining is not None and remaining>600
                and any(term in reason.casefold() for term in terms))

    def _premature_blocked_finish(self, outcome):
        """A recoverable visual error is not a terminal result for this long task."""
        return (getattr(self.backend,'high_level_harness',False) and outcome=='blocked'
                and self.actions < self.budget.max_actions
                and self.calls < self.budget.max_calls
                and self.backend.steps < self.budget.max_sim_steps
                and not self.deadline.expired and not self.deadline.stop_requested
                and self._last_action_error not in TERMINAL_ACTION_ERRORS)

    def _model_aborted_finish(self, outcome, request_id):
        return (getattr(self.backend,'high_level_harness',False) and outcome=='aborted'
                and request_id!=SUPERVISOR_FINISH_ID)

    def _validation_arguments(self, name, arguments, request_id):
        # The supervisor may close a controller failure without impersonating
        # a model decision. All model-visible planning calls require one.
        if (self.profile in PLANNING_PROFILES and name=='finish' and
                request_id==SUPERVISOR_FINISH_ID and isinstance(arguments,dict) and
                'decision' not in arguments):
            return {**arguments,'decision':{
                'state':'Controller stopped before formal finish',
                'active_subgoal':'Close the episode',
                'reason':'Supervisor cleanup',
                'expected':'Independent evaluation is recorded'}}
        return arguments

    def request_finish(self, arguments, request_id):
        """Accept closure intent from HTTP without touching the simulator."""
        if self.closed or not isinstance(request_id,str) or not 0<len(request_id)<=200:
            return
        try:
            validate(self._validation_arguments('finish',arguments,request_id),
                     self.catalog['finish']['inputSchema'])
            fingerprint=json.dumps(['finish',arguments],sort_keys=True,allow_nan=False)
        except (SkillError,ValueError,TypeError):
            return
        cached=self.cache.get(request_id)
        if cached is not None and cached[0]!=fingerprint:
            return
        # An achieved claim with a carried object is recoverable. Do not
        # cancel the episode before the owner thread can reject the claim.
        if (arguments['outcome']=='achieved' and
                getattr(self.backend,'ideal_carry',False) and
                getattr(self.backend,'_ideal_held',None) is not None):
            return
        if self._false_time_exhaustion_claim(arguments['outcome'],arguments['reason']):
            return
        if self._model_aborted_finish(arguments['outcome'],request_id):
            return
        if self._premature_blocked_finish(arguments['outcome']):
            return
        # Achieved is checked against the private goal state on the owner thread.
        # Do not cancel an active action before that check can reject the claim.
        if getattr(self.backend,'high_level_harness',False) and arguments['outcome']=='achieved':
            return
        self.deadline.request_stop()

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
        self.sync_execution_clock()
        self.recorder.event('tool_call',{'name':name,'arguments':arguments,'request_id':request_id})
        if hasattr(self.backend, 'mark_video_tool'):
            self.backend.mark_video_tool(name, arguments, request_id)
        try:
            if self.closed: raise SkillError('episode_closed','The episode is closed')
            if name not in self.catalog: raise SkillError('unknown_tool','Tool is not enabled in this agent profile')
            # Compatibility for archived callers; model schema explicitly carries
            # nullable action options. Original input remains in the event ledger.
            if name=='act' and isinstance(arguments,dict):
                arguments={'placement_yaw_degrees':None,'wait_seconds':None,
                           'confirm_bulk_grasp':False,**arguments}
            validate(self._validation_arguments(name,arguments,request_id),
                     self.catalog[name]['inputSchema'])
            if name!='finish': self.deadline.check()
            if name!='finish' and (self.calls>self.budget.max_calls or
                    (not self.deadline.managed and self.budget.wall_seconds is not None and time.monotonic()-self.started>self.budget.wall_seconds)):
                raise SkillError('budget_exhausted','Call/time budget exhausted; finish the episode')
            execution_args = {k:v for k,v in arguments.items() if k != 'decision'}
            context=ToolContext(self, request_id=request_id, tool_name=name)
            result={'ok':True,**REGISTRY[name].handler(context,**execution_args)}
        except SkillError as exc:
            result={'ok':False,'error':{'code':exc.code,'message':str(exc)}}
            result['observation']=self.snapshot
        if self.deadline.managed and self.deadline.unix is not None:
            result['time_remaining_seconds']=round(self.deadline.remaining(),1)
        event_id=self.recorder.event('tool_result',{'name':name,'request_id':request_id,'result':result})
        result['evidence_id']=event_id
        self.evidence[event_id]={'name':'act' if name in {'act','act_semantic','look'} else name,'ok':result['ok'],'revision':self.revision}
        self.cache[request_id]=(fingerprint,copy.deepcopy(result))
        # Batch archive / the CPU replay worker renders after the RPC returns.
        # Large evaluator records must not delay closure or cached finish replies.
        return result

    def perform(self,primitive,target,revision,**kwargs):
        self.deadline.check()
        if revision!=self.revision: raise SkillError('stale_observation','Use the latest observation revision')
        if self.actions>=self.budget.max_actions or self.backend.steps>=self.budget.max_sim_steps:
            raise SkillError('budget_exhausted','Action/step budget exhausted')
        semantic = isinstance(target, str) or self.profile == 'semantic'
        no_target=primitive in {'look','release','wait'}
        if no_target!=(target is None): raise SkillError('invalid_target','Use a target, or null for release/wait')
        if semantic and self.profile != 'semantic':
            raise SkillError('invalid_target','Semantic names require the semantic agent profile')
        if semantic and target is not None and target not in self.snapshot['semantic_targets']:
            raise SkillError('unknown_semantic_target','Use a name from the current semantic_targets list')
        if not semantic and target and target['image_ref'] not in {i['image_ref'] for i in self.snapshot['images']}:
            raise SkillError('stale_image_ref','Select a point in the latest returned image')
        self.actions+=1
        remaining=(DEADLINE_STEP_SENTINEL if self.execution_deadline_only else self.budget.max_sim_steps)-self.backend.steps
        # A managed episode has one execution deadline. Counters remain recorded,
        # but neither episode totals nor the legacy per-action limit end it early.
        limit=remaining if self.deadline.managed else min(self.budget.max_steps_per_action,remaining)
        error=None
        try:
            details=(self.backend.execute_semantic(primitive,target,limit,**kwargs)
                     if semantic else self.backend.execute_visual(primitive,target,limit,**kwargs))
            self.recorder.event('private_executor_result',{'primitive':primitive,'details':details})
        except SkillError as exc:
            self.recorder.event('private_executor_error',{'code':exc.code,'detail':str(exc)})
            self._last_action_error=exc.code
            error=public_execution_error(exc)
        else:
            self._last_action_error=None
        finally:
            self.revision+=1
            # A timed-out action may have changed the world. Invalidate its RGB,
            # but do not spend the expired task budget rendering another capture.
            if not self.deadline.expired and not self.deadline.stop_requested: self.refresh()
        remaining_actions=max(0, self.budget.max_actions-self.actions)
        # Once the final allowed action has returned its fresh observation,
        # cooperatively stop further non-finish calls. `finish` remains allowed
        # so the model can report achieved or blocked and the evaluator can run.
        if remaining_actions == 0 or self.backend.steps >= self.budget.max_sim_steps:
            self.deadline.request_stop()
        if error:
            return {'ok':False,'error':error,'observation':self.snapshot,
                    'actions_used':self.actions,'actions_remaining':remaining_actions}
        return {'effect':public_action_effect(primitive, details),
                'observation':self.snapshot,'actions_used':self.actions,
                'actions_remaining':remaining_actions}

    def update_plan(self,reason,subgoals): return LegacyPlanHelpers._tool_update_plan(self,reason,subgoals)
    def remember(self,key,text,revision): return LegacyPlanHelpers._tool_remember(self,key,text,revision)
    def recall(self): return LegacyPlanHelpers._tool_recall(self)
    def finish(self,outcome,reason,request_id=None):
        if self._false_time_exhaustion_claim(outcome,reason):
            raise SkillError('time_budget_available',
                             f'About {int(self.deadline.remaining())} seconds remain. '
                             + (f'{self.budget.max_actions-self.actions} actions remain; '
                                'the harness has reported no separate session limit. '
                                if self.correct_false_clock_claims else '') +
                             'Inspect the current RGB and try a materially different recovery, '
                             'or name the actual technical blocker without claiming time ran out.')
        if (outcome=='achieved' and getattr(self.backend,'ideal_carry',False) and
                getattr(self.backend,'_ideal_held',None) is not None):
            raise SkillError('hand_occupied','Place or release the carried object before reporting achieved')
        if self._model_aborted_finish(outcome,request_id):
            raise SkillError('supervisor_only',
                             'Aborted is reserved for the episode supervisor. Continue the task '
                             'or report blocked when a terminal condition is reached.')
        if self._premature_blocked_finish(outcome):
            raise SkillError('premature_finish',
                             f'{self.budget.max_actions-self.actions} actions remain. '
                             'A failed visual target or placement is recoverable: inspect fresh RGB '
                             'and continue with another point, viewpoint, or task branch. '
                             'Report blocked only after the action/call/step budget is exhausted '
                             'or a nonrecoverable simulator error occurs.')
        if (getattr(self.backend,'high_level_harness',False) and outcome=='achieved'
                and not self.backend.evaluate()['task_success']):
            raise SkillError('premature_finish',
                             'Task completion could not be verified. Continue from the latest RGB; '
                             'no goal details are available from finish.')
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
