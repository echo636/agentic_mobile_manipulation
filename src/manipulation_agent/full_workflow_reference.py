"""Complete declarative workflow references through the ordinary checked executor.

These are privileged physical references. Failed actions remain in the trace;
no task-state setter, simulator teleport, or evaluator-dependent target is used.
"""
from __future__ import annotations

from .contracts import SkillError
from .long_horizon import expression


def validate_workflow_plan(spec):
    plan=spec['reference_plan']; roles=spec['roles']; phases=plan['phases']
    if plan['kind']!='full_workflow' or not phases: raise ValueError('Missing complete workflow phases')
    for order in spec['acceptance_cases']['reference_orders']:
        sequence=plan['orders'][order]
        if len(sequence)!=len(phases) or set(sequence)!=set(phases):
            raise ValueError('Each reference must cover every workflow phase exactly once')
        done=set()
        for key in sequence:
            if not set(phases[key].get('after',[]))<=done:raise ValueError('Reference violates a physical phase dependency')
            done.add(key)
    if set(plan.get('negative_omissions',{})) != set(spec['acceptance_cases']['physical_negative_cases']):
        raise ValueError('Every physical negative needs an explicit omission')
    if not set(plan['negative_omissions'].values()) <= set(phases):
        raise ValueError('Unknown omitted phase')
    for phase in phases.values():
        if not phase.get('actions'):raise ValueError('A workflow phase must do physical work')
        for action in phase['actions']:
            if action.get('role') is not None and action['role'] not in roles:raise ValueError('Unknown action role')
            if action['primitive']=='wait_for':
                if type(action.get('attempts',18)) is not int or not 1<=action.get('attempts',18)<=60:
                    raise ValueError('Invalid native process wait budget')
                if not 0<float(action.get('seconds',10.))<=30:raise ValueError('Invalid native process wait interval')
                expression(action['condition'],dict.fromkeys(spec['evaluation']['predicates'],False))
            elif action['primitive'] not in {'grasp','place_on_top','place_inside','open','close','toggle_on',
                    'toggle_off','wipe','sweep','vacuum','spray','soak','spread','cut','attach','hang','wait','navigate_to'}:
                raise ValueError('Unknown workflow primitive')
            elif action['primitive'] not in ('wait',) and action.get('role') is None:
                raise ValueError('Targeted workflow action needs an entity role')
    return {'phase_count':len(phases),'reference_actions':sum(len(p['actions']) for p in phases.values())}


class FullWorkflowReference:
    def __init__(self,ref):
        self.ref=ref;self.backend=ref.backend;self.plan=self.backend.custom_spec['reference_plan']
        validate_workflow_plan(self.backend.custom_spec)

    def surface_point(self,role,primitive,attempt=0):
        """Use an actual selected-object collision surface instead of its hidden origin."""
        from omnigibson.utils.sampling_utils import raytest
        obj=self.ref.obj(role);b=self.backend;low,high=obj.aabb
        center=(low+high)/2;eye=b.robot.get_position_orientation()[0].clone()
        eye[2]+=b.rig_height
        ignored=[l.prim_path for l in b.robot.links.values()]
        held=b._get_held()
        if held is not None and held is not obj:ignored += [l.prim_path for l in held.links.values()]
        paths={l.prim_path for l in obj.links.values()}
        preferred=center.clone()
        preferred[2]=max(float(low[2])+.005,min(float(high[2])-.005,float(eye[2])-.1))
        targets=[preferred,center]
        for x,y,z in ((.5,.5,.3),(.25,.5,.5),(.75,.5,.5),(.5,.25,.5),(.5,.75,.5),(.5,.5,1.)):
            targets.append(low+(high-low)*b.torch.tensor([x,y,z],device=low.device))
        rays=[(eye,target) for target in targets]
        # A distant shelf can hide a cabinet's lower face from the current
        # eye ray while exposing only a top corner outside the approach RGB
        # frusta. Ray-cast real side surfaces at camera height as alternatives;
        # ordinary navigation still checks reachability and visibility.
        for axis,sign in ((0,-1),(1,-1),(0,1),(1,1)):
            origin=preferred.clone();origin[axis]=(low[axis] if sign<0 else high[axis])+sign*.4
            rays.append((origin,preferred))
        candidates=[]
        for origin,target in rays:
            direction=target-origin
            end=target+.1*direction/max(float(b.torch.linalg.norm(direction)),1e-6)
            hit=raytest(origin,end,ignore_bodies=ignored)
            if hit.get('hit') and hit.get('rigidBody') in paths:
                point=[float(v) for v in hit['position']]
                if all(sum((x-y)**2 for x,y in zip(point,old))>.0025 for old in candidates):candidates.append(point)
        if candidates:
            candidates.sort(key=lambda p:(abs(p[2]-float(preferred[2])),
                                          sum((p[i]-float(eye[i]))**2 for i in (0,1))))
            return candidates[attempt%len(candidates)]
        # A physically meaningful top/near-face point remains subject to the
        # executor's normal visibility, collision, reach and departure checks.
        if primitive in ('wipe','cut','grasp','spray','spread','vacuum','sweep'):
            return [float(center[0]),float(center[1]),float(high[2])]
        from .reference_tasks import appliance_approach_point
        return appliance_approach_point((low,high),eye)

    def action(self,row):
        primitive=row['primitive'];role=row.get('role')
        if primitive=='wait_for':
            for attempt in range(row.get('attempts',18)+1):
                if expression(row['condition'],self.backend._predicate_values()):return
                if attempt==row.get('attempts',18):raise SkillError('process_incomplete','Native process did not reach the declared physical condition')
                self.ref.action('wait',seconds=row.get('seconds',10.))
            return
        force_approach=False
        for attempt in range(3):
            point=row.get('point')
            if 'xy' in row:point=[*row['xy'],float(self.ref.obj(role).aabb[1][2])]
            elif 'uv' in row:point=self.ref.top(role,*row['uv'])
            elif point is None and role:point=self.surface_point(role,primitive,attempt)
            try:
                # Long-range approach is a separately recorded ordinary motor call.
                # This generic reference has no task-specific coordinate shortcuts.
                if role and primitive!='navigate_to':
                    base=self.backend.robot.get_position_orientation()[0]
                    distance=sum((float(base[i])-point[i])**2 for i in (0,1))**.5
                    if force_approach or distance>(1.2 if primitive=='grasp' else 1.4):
                        self.ref.action('navigate_to',role,point)
                        if 'uv' not in row and 'xy' not in row and 'point' not in row:
                            point=self.surface_point(role,primitive,attempt)
                return self.ref.action(primitive,role,point,**row.get('options',{}))
            except SkillError as exc:
                recoverable=exc.code in {'local_adjustment_exhausted','local_adjustment_blocked','out_of_reach',
                    'navigation_search_exhausted','sampling_error','sampling_budget_exhausted','postcondition_error'}
                if not recoverable or attempt==2:raise
                self.ref.log('workflow_recovery',primitive=primitive,role=role,attempt=attempt+1,code=exc.code)
                if exc.code in {'sampling_error','sampling_budget_exhausted','postcondition_error'}:
                    self.ref.action('wait',seconds=1.)
                else:
                    # A fresh explicit navigation call has its own recorded
                    # work budget; it never changes the selected object ID.
                    force_approach=True

    def run(self,order,negative=None):
        from .records import write_json
        self.ref.log('full_workflow_started',order=order,task=self.backend.custom_spec['id'],
                     phase_count=len(self.plan['phases']),model_used=False)
        results={phase:{'status':'planned'} for phase in self.plan['orders'][order]}
        write_json(self.ref.output/'workflow_phases.json',results)
        for phase in self.plan['orders'][order]:
            definition=self.plan['phases'][phase]
            self.ref.log('workflow_phase_started',phase=phase)
            if negative==phase:
                results[phase]={'status':'omitted_for_negative'};write_json(self.ref.output/'workflow_phases.json',results);continue
            try:
                results[phase]={'status':'running'}
                write_json(self.ref.output/'workflow_phases.json',results)
                for row in definition['actions']:self.action(row)
                results[phase]={'status':'passed'}
            except Exception as exc:
                results[phase]={'status':'failed','error':str(exc),'code':getattr(exc,'code',None)}
                self.ref.log('workflow_phase_finished',phase=phase,**results[phase])
                write_json(self.ref.output/'workflow_phases.json',results)
                # Preserve the complete scenario and the actual failure. A
                # broken reference is never called a completed trajectory.
                raise
            self.ref.log('workflow_phase_finished',phase=phase,**results[phase])
            write_json(self.ref.output/'workflow_phases.json',results)
        return results

    def negative(self,case):
        phase=self.plan['negative_omissions'][case]
        self.run(self.backend.custom_spec['acceptance_cases']['reference_orders'][0],negative=phase)
        result=self.backend.evaluate()
        return {'case':case,'status':'passed' if not result['task_success'] else 'failed',
                'checks':{'controlled_omission_completed':True,'independent_evaluator_rejected':not result['task_success']}}
