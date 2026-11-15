"""RGB-only model boundary, workflow tools, bounded actions and private evaluation."""
import copy
import json
import threading
import time
from dataclasses import asdict
from ..contracts import Budget, SkillError, validate
from .deadline import DEADLINE_STEP_SENTINEL, FINISH_GRACE_SECONDS, EpisodeDeadline
from .harness import Harness as LegacyPlanHelpers
from ..observations.boundary import public_observation, public_execution_error
from ..records import write_json
from ..agent.skill_runtime import SkillLibrary
from ..tools import REGISTRY, tool_specs
from ..tools.intent import validate_intent, PROTOCOL as INTENT_PROTOCOL, LEGACY_PROTOCOL
from .tool_context import ToolContext
from ..observations.surround import SurroundJobs
from .closure import EpisodeClosure
from ..agent.task_notes import TaskNotes, model_planning_reply
from ..agent.schedule_planner import PublicScheduleLedger
from ..agent.state_memory import StateMemory
from ..agent.task_graph_planner import TaskGraphSession

# Pre-execution validation / non-mutating refusal codes that never touch physics or render a new capture.
NO_WORLD_CHANGE_CODES = frozenset({
    'stale_observation', 'stale_image_ref', 'invalid_target', 'invalid_arguments',
    'unknown_tool', 'unknown_skill_resource', 'invalid_schedule_input', 'invalid_task_graph_input',
})

ACTION_FEEDBACK_HINTS = {
    'no_removable_material': (
        ' Check the Click echo crop and returned RGB: your pixel may have landed on the adjacent floor, wall, '
        'or countertop just outside the dirty surface edge rather than on the intended object itself.'
    ),
    'fixed_object': (
        ' Check two common causes in RGB and Click echo: (1) if the intended item is inside a closed cabinet, '
        'appliance, drawer, or glass door, your ray hit the closed door panel — call act(primitive="open") on '
        'that door/container while your hand is empty first; (2) if you tried to move far away or clicked beside '
        'a movable item, use act(primitive="navigate_to") to walk to distant areas/doorways, or reselect the '
        'interior body of the movable item itself.'
    ),
    'pre_condition_error': (
        ' Verify three prerequisites in RGB: (1) hand state matches the action (open/close and unheld switches '
        'require robot.hand == "empty"; cut/clean/treat require holding the proper tool); (2) for open/close, '
        'click the operable door panel or drawer front rather than the fixed outer frame, countertop, or an '
        'already-open door panel — if a swung-open door blocks your view of the interior shelves, call '
        'act(primitive="navigate_to") onto floor space past/facing the open cavity and inspect all 4 views; '
        '(3) for distant rooms, walk closer with navigate_to before operating.'
    ),
    'unsupported_relation': (
        ' Check the receiving object type and action primitive: place_inside requires an open container/compartment; '
        'place_on_top requires a flat upward support surface; attach requires a compatible mounting peg/socket/fixture.'
    ),
    'cut_target_unsupported': (
        ' Check Click echo: your pixel likely hit the cutting board or countertop under/beside the food/workpiece '
        'rather than the uncut item itself, or the item is already sliced.'
    ),
    'hand_occupied': (
        ' open/close and unheld switches require robot.hand == "empty". Stage the held item on a nearby clear '
        'surface first, perform open/close/toggle, then re-grasp.'
    ),
    'container_closed': (
        ' If your hand is occupied, stage the held item on a nearby surface first so your hand is empty, '
        'open the container, then pick the item back up and place_inside.'
    ),
}

class VisionHarness:
    def __init__(self, backend, recorder, budget=Budget(), *, profile='skills', agent_step_limit=None,
                 goal_skill='hierarchical', unbounded_episode=False, require_tool_intent=False,
                 scheduling_mode='none', planning_mode='off', auxiliary_feedback=False, state_memory=False):
        if profile == 'basic-skills': goal_skill = 'none'
        if profile == 'goal-tree': goal_skill = 'tree'
        if goal_skill not in {'hierarchical','tree','none'}: raise ValueError('Unknown goal skill mode')
        if scheduling_mode not in {'none', 'text', 'program'}: raise ValueError('Unknown scheduling mode')
        if planning_mode not in {'off', 'task_graph'}: raise ValueError('Unknown planning mode')
        self.goal_skill=goal_skill
        self.scheduling_mode=scheduling_mode
        self.planning_mode=planning_mode
        self.auxiliary_feedback=bool(auxiliary_feedback)
        self.state_memory_enabled=bool(state_memory)
        self.state_memory=StateMemory(output_dir=recorder.output) if self.state_memory_enabled else None
        self.schedule_ledger=PublicScheduleLedger()
        self.task_graph_session=TaskGraphSession()
        self.require_tool_intent=require_tool_intent
        self.task_notes=TaskNotes(recorder.output, auto_select=goal_skill == 'hierarchical')
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
        self._consecutive_refunds=0
        self.closure=EpisodeClosure(recorder.output)
        self.profile = profile
        self.surround = SurroundJobs(self)
        self.catalog = {t['name']: t for t in tool_specs(
            profile, require_intent=require_tool_intent,
            scheduling_mode=scheduling_mode, planning_mode=planning_mode)}
        self.cache,self.evidence={},{}
        self.skills = None
        if profile == 'workflow':
            self.plan,self.memory=[],{}
        if profile in {'basic-skills','goal-tree','skills','skills_inline','workflow'}:
            # skills_inline freezes the same bundle for provenance; its prompt carries the text.
            planning_skill = {'hierarchical': 'goal-decomposition', 'tree': 'goal-tree'}.get(goal_skill)
            excluded = {'goal-decomposition', 'goal-tree'} - {planning_skill}
            if scheduling_mode != 'text':
                excluded = excluded | {'time-space-scheduling'}
            if planning_mode != 'task_graph':
                excluded = excluded | {'task-graph-planning'}
            self.skills=SkillLibrary(recorder.output,exclude=excluded)
        recorder.run['config']['agent_profile'] = profile
        recorder.run['config'].update(goal_skill=goal_skill,
            scheduling_mode=scheduling_mode,
            planning_mode=planning_mode,
            auxiliary_feedback=self.auxiliary_feedback,
            state_memory=self.state_memory_enabled,
            require_tool_intent=require_tool_intent,
            tool_intent_protocol=INTENT_PROTOCOL if require_tool_intent else LEGACY_PROTOCOL)
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
            result['control']={'manipulation':
                'Select the visible target directly; the robot can approach it before manipulating it.'
                if self.backend.local_manipulation else
                'Move within reach with navigate_to before manipulating a target.'}
        if 'task_notes' in self.catalog: result['notes']=self.task_notes.read()
        if self.require_tool_intent: result['tool_intent_protocol']=INTENT_PROTOCOL
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
            if self.require_tool_intent: validate_intent(arguments)
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

    def _public_hand_state(self):
        """Robot's own gripper proprioception ('empty' or 'holding_item'), zero object identity."""
        getter = getattr(self.backend, '_get_held', None)
        if getter is None:
            return None
        try:
            return 'holding_item' if getter() is not None else 'empty'
        except Exception:
            return None

    def _public_robot_time(self):
        """Public robot_clock accounting entry for the action that just executed, if available."""
        clock = getattr(self.backend, 'robot_clock', None)
        last = getattr(clock, 'last_action', None)
        return copy.deepcopy(last) if isinstance(last, dict) else None

    def _normalize_auxiliary_args(self, name: str, arguments):
        """Harness-only permissive argument normalization (active ONLY when self.auxiliary_feedback is True).

        Eliminates schema friction on lazy-MCP CLI clients (`call_mcp_tool`) without changing
        default-off Benchmark/Harness contracts. Original raw arguments remain recorded in events.jsonl.
        """
        if not self.auxiliary_feedback or not isinstance(arguments, dict):
            return arguments
        out = dict(arguments)
        if name == 'read_skill':
            lower_map = {str(k).lower(): v for k, v in out.items()}
            norm = {}
            skill_name = lower_map.get('name') or lower_map.get('skill_name') or lower_map.get('skill')
            res_name = lower_map.get('resource')
            if isinstance(skill_name, str):
                if '/' in skill_name and not res_name:
                    parts = skill_name.split('/', 1)
                    norm['name'] = parts[0]
                    norm['resource'] = parts[1]
                else:
                    norm['name'] = skill_name
                    norm['resource'] = res_name if isinstance(res_name, str) and res_name else 'SKILL.md'
                return norm
        elif name == 'act':
            if 'primitive' not in out and isinstance(out.get('action'), str):
                out = {k: v for k, v in out.items() if k != 'action'}
                out['primitive'] = arguments['action']
        return out

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
        call_started = time.monotonic()
        observed_sim_start = self.snapshot.get('capture', {}).get('sim_time_seconds')
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
            if self.require_tool_intent: validate_intent(arguments)
            arguments = self._normalize_auxiliary_args(name, arguments)
            # Compatibility for archived callers; model schema explicitly carries
            # nullable action options. Original input remains in the event ledger.
            if name=='act' and isinstance(arguments,dict):
                arguments={'placement_yaw_degrees':None,'placement_roll_degrees':None,'wait_seconds':None,**arguments}
            validate(arguments,self.catalog[name]['inputSchema'])
            if name!='finish': self.deadline.check()
            if name!='finish' and (
                    (self.legacy_limits_active and self.calls>self.budget.max_calls) or
                    (self.budget.wall_seconds is not None and not self.deadline.managed and time.monotonic()-self.started>self.budget.wall_seconds)):
                raise SkillError('budget_exhausted','Call/time budget exhausted; finish the episode')
            execution_args = {k:v for k,v in arguments.items() if k not in {'decision', 'intent'}}
            context=ToolContext(self, request_id=request_id, tool_name=name)
            result={'ok':True,**REGISTRY[name].handler(context,**execution_args)}
            if name in {'act', 'look'}:
                self._consecutive_refunds = 0
        except SkillError as exc:
            result={'ok':False,'error':{'code':exc.code,'message':str(exc)}}
            result['observation']=self.snapshot
            if (self.auxiliary_feedback and name in {'act','look'} and exc.code in NO_WORLD_CHANGE_CODES
                    and self.agent_step_limit is not None and self.agent_steps>0 and not self.closed):
                self._consecutive_refunds += 1
                if self._consecutive_refunds <= 6:
                    self.agent_steps-=1
                    self.recorder.run['agent_steps']=self.agent_steps
                    write_json(self.recorder.output/'run.json',self.recorder.run)
                    self.recorder.event('agent_step_refunded',{'step':self.agent_steps+1,'tool':name,
                        'request_id':request_id,'code':exc.code})
                    result['error']['action_step_used']=False
                    result['error']['current_image_refs']=[image['image_ref'] for image in self.snapshot['images']]
                    result.pop('observation',None)
                else:
                    result['error']['action_step_used']=True
                    result['error']['message'] = (
                        str(result['error'].get('message', '')) +
                        ' Repeated schema/reference errors now consume step budget. '
                        'For `look`, pass `{"yaw_degrees": 45, "revision": <latest_revision>}`; '
                        'for `act`, pass `{"primitive": "...", "target": {"image_ref": "...", "point": [x, y]}, "revision": <latest_revision>}`.'
                    )
            elif name in {'act', 'look'}:
                self._consecutive_refunds = 0
        if name in {'act', 'look'} and not self.closed:
            if self.auxiliary_feedback or self.state_memory_enabled:
                hand = self._public_hand_state()
                if hand is not None:
                    result['robot'] = {'hand': hand}
            if self.auxiliary_feedback:
                if not result.get('ok') and isinstance(result.get('error'), dict):
                    err_code = result['error'].get('code')
                    if err_code in ACTION_FEEDBACK_HINTS:
                        result['error']['message'] = str(result['error'].get('message', '')) + ACTION_FEEDBACK_HINTS[err_code]
            if (self.auxiliary_feedback or self.scheduling_mode == 'program'
                    or self.state_memory_enabled or self.planning_mode == 'task_graph'):
                rt_public = self._public_robot_time()
                if rt_public is not None:
                    result['robot_time'] = rt_public
            if self.scheduling_mode == 'program':
                rt_for_ledger = result.get('robot_time') or self._public_robot_time()
                ledger_input = result if 'robot_time' in result else {**result, **({'robot_time': rt_for_ledger} if rt_for_ledger else {})}
                self.schedule_ledger.observe_public_action(name, arguments if isinstance(arguments, dict) else None, ledger_input)
                result['schedule_status'] = self.schedule_ledger.public_summary()
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
        if name in {'act', 'look'}:
            result['runtime'] = {'tool_seconds': round(time.monotonic() - call_started, 3)}
            observed_sim_end = self.snapshot.get('capture', {}).get('sim_time_seconds')
            if observed_sim_start is not None and observed_sim_end is not None:
                result['runtime']['observed_simulation_seconds'] = max(0., observed_sim_end - observed_sim_start)
        if self.state_memory_enabled and self.state_memory is not None and not self.closed:
            if name == 'initialize':
                hand = self._public_hand_state()
                if hand is not None and 'robot' not in result:
                    result['robot'] = {'hand': hand}
                rt_init = self._public_robot_time()
                if rt_init is not None and 'robot_time' not in result:
                    result['robot_time'] = rt_init
            self.state_memory.observe(name, arguments if isinstance(arguments, dict) else None, result)
            if name in {'initialize', 'act', 'look'}:
                result['state_memory'] = self.state_memory.public_view(
                    include_handoff=(name == 'initialize' and self.agent_steps > 0)
                )
        if self.planning_mode == 'task_graph' and name in {'act', 'look'} and not self.closed:
            rt_for_tg = result.get('robot_time') or self._public_robot_time()
            tg_input = result if 'robot_time' in result else {**result, **({'robot_time': rt_for_tg} if rt_for_tg else {})}
            self.task_graph_session.sync_from_state_memory(getattr(self, 'state_memory', None))
            self.task_graph_session.observe_public_action(name, arguments if isinstance(arguments, dict) else None, tg_input)
            result['task_graph_status'] = self.task_graph_session.public_status()
        if ('task_notes' in self.catalog and self.goal_skill == 'hierarchical'
                and origin == 'controller' and name in {'initialize', 'task_notes', 'act', 'look'}):
            pending = None
            if name in {'act', 'look'}:
                pending = {'primitive': arguments.get('primitive', name) if isinstance(arguments, dict) else name,
                           **result['runtime'],
                           **{key: result[key] for key in ('ok', 'effect', 'error') if key in result}}
            result['planning'] = self.task_notes.planning(pending)
            if pending is not None:
                # Checkpoint comparisons and the previous interval were already
                # returned with notes. Keep them available on read/resume, not
                # duplicated after every action within the selected operation.
                result['planning'].pop('checkpoint_comparison', None)
                result['planning'].pop('previous_execution', None)
        event_id=self.recorder.event('tool_result',{'name':name,'request_id':request_id,'result':result,'origin':origin})
        result['evidence_id']=event_id
        if name in {'act','look'} and 'task_notes' in self.catalog and origin=='controller':
            self.task_notes.record_action(name,arguments,result,self.agent_steps)
        self.evidence[event_id]={'name':'act' if name in {'act','look'} else name,'ok':result['ok'],'revision':self.revision}
        # Archive the complete authored/generated graph above, but do not echo
        # it in every model response. read/initialize still restore full notes.
        if origin == 'controller':
            result = model_planning_reply(result)
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
        if primitive=='open' and details.get('other_panels_not_operated') is True:
            effect['opening']={'other_panels_not_operated':True,
                'message':'The opening command applied only to part of this object. '
                          'Other doors or drawers were not operated. Inspect access in the returned RGB.'}
        if primitive=='cut':
            # Report this action's transformation, without object/material IDs
            # or task-goal state. Particle counts are not physical piece counts.
            if details.get('transition')=='SlicingRule':
                effect['cut']={'result':'sliced','pieces_created':int(details['created_objects'])}
            elif details.get('transition')=='DicingRule':
                effect['cut']={'result':'diced_material'}
        if primitive in {'pour','scoop'}:
            materials=details.get('materials',[])
            rigid=details.get('rigid_transfer')
            if materials or rigid:
                remaining=details.get('source_has_more_material')
                effect['transfer']={
                    'portion_transferred':True,
                    'source_has_more_material':remaining if isinstance(remaining,bool) else None,
                    'held_object_retained':True,
                    'message':'Contents were transferred. Check remaining contents and the receiver in the returned RGB.'}
                receiver = details.get('receiver_kind')
                if receiver in {'container', 'surface'}:
                    effect['transfer']['receiver_kind'] = receiver
                    effect['transfer']['message'] = (
                        f'Contents were transferred to the selected {receiver}. '
                        'Check remaining contents and the receiver in the returned RGB.')
                if rigid:
                    effect['transfer'].update(rigid)
                partial = []
                if details.get('some_material_not_transferred'):
                    effect['transfer']['some_material_not_transferred'] = True
                    partial.append('some material could not be transferred')
                if any(not item.get('identities_changed_during_settling') and
                       item.get('retained_particles', item['moved_particles']) < item['moved_particles']
                       for item in materials):
                    effect['transfer']['some_material_not_retained'] = True
                    partial.append('some material did not remain at the receiver')
                if partial:
                    effect['transfer']['message'] = (
                        'Transfer was partial: ' + '; '.join(partial) + '. '
                        'Inspect the receiver, source and surrounding surfaces in the returned RGB.')
        local=details.get('local_manipulation')
        if local:
            effect['local_adjustment']={'repositions':int(local['repositions']),
                'primitive_attempts':int(local['primitive_attempts']),
                'operation_committed':True,
                'message':'Operation completed on the selected target. Inspect the returned RGB.'}
        if details.get('public_process'):
            effect['process']=details['public_process']
        if details.get('public_tool_cleanliness'):
            effect['tool_cleanliness']=details['public_tool_cleanliness']
        if primitive=='navigate_to':
            # Only an action outcome crosses the RGB boundary, never private
            # coordinates, target distances, object identities or map geometry.
            messages={
                'endpoint_applied':'Navigation applied. Inspect the returned RGB for the actual position and surroundings.',
                'reached':'Reached the selected point. Inspect the updated RGB.',
                'approached':'Moved to a feasible approach toward the selected point. This does not verify manipulation reach; inspect the updated RGB.',
                'incomplete':'The move did not reach the selected destination. Inspect the returned RGB before choosing the next action.',
                'already_at_closest_approach':'Already at the closest approach found by this navigation search. Repeating the same click will not move closer; inspect the updated RGB for an alternative.',
            }
            status=details.get('nav_status')
            if status in messages:effect.update(navigation_status=status,message=messages[status])
            if status=='incomplete':effect['status']='incomplete'
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
