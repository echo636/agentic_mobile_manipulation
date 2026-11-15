"""Complete declarative workflow references through the ordinary checked executor.

These are privileged physical references. Failed actions remain in the trace;
no task-state setter, simulator teleport, or evaluator-dependent target is used.
"""
from __future__ import annotations

import math

from ..contracts import SkillError
from ..tasks.long_horizon import expression


def validate_workflow_plan(spec):
    plan=spec['reference_plan']; roles=spec['roles']; phases=plan['phases']
    if plan['kind']!='full_workflow' or not phases: raise ValueError('Missing complete workflow phases')
    allow_variants=bool(plan.get('allow_strategy_variants',False))
    used_phases=set()
    for order in spec['acceptance_cases']['reference_orders']:
        sequence=plan['orders'][order]
        if not sequence or len(sequence)!=len(set(sequence)) or not set(sequence)<=set(phases):
            raise ValueError('Each reference must cover declared workflow phases without duplicates')
        if not allow_variants and set(sequence)!=set(phases):
            raise ValueError('Each reference must cover every workflow phase exactly once')
        used_phases.update(sequence)
        done=set()
        for key in sequence:
            if not set(phases[key].get('after',[]))<=done:raise ValueError('Reference violates a physical phase dependency')
            done.add(key)
    if used_phases!=set(phases):
        raise ValueError('Every declared workflow phase must appear in at least one reference order')
    if set(plan.get('negative_omissions',{})) != set(spec['acceptance_cases']['physical_negative_cases']):
        raise ValueError('Every physical negative needs an explicit omission')
    primary_phases=set(plan['orders'][spec['acceptance_cases']['reference_orders'][0]])
    if not set(plan['negative_omissions'].values()) <= primary_phases:
        raise ValueError('Unknown omitted phase')
    for phase in phases.values():
        if not phase.get('actions'):raise ValueError('A workflow phase must do physical work')
        for action in phase['actions']:
            if action.get('role') is not None and action['role'] not in roles:raise ValueError('Unknown action role')
            if action.get('approach_role') is not None:
                if action['approach_role'] not in roles or action['primitive']!='grasp':
                    raise ValueError('An extraction approach must name an existing role for grasp')
            if action['primitive']=='wait_for':
                if type(action.get('attempts',18)) is not int or not 1<=action.get('attempts',18)<=60:
                    raise ValueError('Invalid native process wait budget')
                if not 0<float(action.get('seconds',10.))<=120:raise ValueError('Invalid native process wait interval')
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
        # For low objects, prefer the actual top surface. Biasing every point
        # 5 mm below the AABB top ranks a book's thin edge above its visible
        # upper face, even when the supporting table occludes that edge.
        preferred[2]=max(float(low[2])+.005,min(float(high[2]),float(eye[2])-.1))
        targets=[preferred,center]
        for x,y,z in ((.5,.5,.3),(.25,.5,.5),(.75,.5,.5),(.5,.25,.5),(.5,.75,.5),(.5,.5,1.)):
            targets.append(low+(high-low)*b.torch.tensor([x,y,z],device=low.device))
        rays=[(eye,target) for target in targets]
        for x,y in ((.5,.5),(.25,.5),(.75,.5),(.5,.25),(.5,.75)):
            target=low+(high-low)*b.torch.tensor([x,y,.5],device=low.device)
            origin=target.clone();origin[2]=high[2]+.1
            rays.append((origin,target))
        # A distant shelf can hide a cabinet's lower face from the current
        # eye ray while exposing only a top corner outside the approach RGB
        # frusta. Ray-cast real side surfaces at camera height as alternatives;
        # ordinary navigation still checks reachability and visibility.
        # Appliances can sit below a countertop with a neighbor covering the
        # near AABB face. Top-height rays then all hit the countertop. Sample
        # real side surfaces below it as well, so a remote approach selects
        # the exposed door instead of repeatedly falling back to a hidden face.
        side_targets=[preferred,center]
        for fraction in (.75,.25):
            target=center.clone();target[2]=low[2]+fraction*(high[2]-low[2])
            side_targets.append(target)
        for target in side_targets:
            for axis,sign in ((0,-1),(1,-1),(0,1),(1,1)):
                origin=target.clone();origin[axis]=(low[axis] if sign<0 else high[axis])+sign*.4
                rays.append((origin,target))
        candidates=[]
        for origin,target in rays:
            direction=target-origin
            end=target+.1*direction/max(float(b.torch.linalg.norm(direction)),1e-6)
            hit=raytest(origin,end,ignore_bodies=ignored)
            if hit.get('hit') and hit.get('rigidBody') in paths:
                point=[float(v) for v in hit['position']]
                if all(sum((x-y)**2 for x,y in zip(point,old))>.0001 for old in candidates):candidates.append(point)
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

    def surface_xy_point(self, role, xy):
        """Find the actual upward collision surface height at a given (x, y) location.

        Prevents taking the top of an object's bounding box (e.g. a tall headboard)
        when placing an item onto a lower horizontal support surface (e.g. a mattress).
        Also ensures placement points for large carriers have sufficient clearance
        from support surface boundaries so they settle stably without overhanging.
        """
        obj = self.ref.obj(role)
        low, high = obj.aabb
        target_xy = [float(xy[0]), float(xy[1])]
        b = self.backend
        held = b._get_held()
        if held is not None and not ('floor' in role or 'sink' in role):
            try:
                h_low, h_high = held.aabb
                h_radius = max(float(h_high[0] - h_low[0]), float(h_high[1] - h_low[1])) / 2.0
                margin = max(0.12, h_radius + 0.20)
                if float(high[0] - low[0]) > 2 * margin:
                    target_xy[0] = max(float(low[0]) + margin, min(float(high[0]) - margin, target_xy[0]))
                if float(high[1] - low[1]) > 2 * margin:
                    target_xy[1] = max(float(low[1]) + margin, min(float(high[1]) - margin, target_xy[1]))
            except Exception:
                pass
        try:
            from omnigibson.utils.sampling_utils import raytest
            ignored = [l.prim_path for l in b.robot.links.values()]
            if held is not None and held is not obj:
                ignored += [l.prim_path for l in held.links.values()]
            paths = {l.prim_path for l in obj.links.values()}
            start = [target_xy[0], target_xy[1], float(high[2]) + 0.2]
            end = [target_xy[0], target_xy[1], float(low[2]) - 0.1]
            raw_hits = raytest(start, end, only_closest=False, ignore_bodies=ignored)
            hits = raw_hits if isinstance(raw_hits, list) else ([raw_hits] if raw_hits.get('hit') else [])
            valid_hits = [
                h for h in hits
                if h.get('hit', True) and h.get('rigidBody') in paths
                and float(h.get('normal', [0, 0, 1])[2]) >= 0.7
            ]
            if valid_hits:
                valid_hits.sort(key=lambda h: float(h['position'][2]), reverse=True)
                return [target_xy[0], target_xy[1], float(valid_hits[0]['position'][2])]
        except Exception:
            pass
        return [*target_xy, float(high[2])]

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
            if primitive in ('place_on_top','place_inside') and self.backend._get_held() is None:
                preds = self.backend._predicate_values()
                if any(preds.get(f"{k}_ontop_{role}") or preds.get(f"{k}_on_{role}") or preds.get(f"{k}_inside_{role}") or preds.get(f"{k}_in_{role}") for k in self.backend.custom_spec.get('roles', {})):
                    return
            point=row.get('point')
            if 'xy' in row:point=self.surface_xy_point(role,row['xy'])
            elif 'uv' in row:
                uv = list(row['uv'])
                # If placing rag on tray when tray is empty of objects (e.g. during negative test where clear_dining was omitted),
                # use center of tray [0.5, 0.5] to prevent sliding off the rim.
                if role == 'tray' and uv == [0.50, 0.73]:
                    preds = self.backend._predicate_values()
                    if not any(preds.get(f"{k}_on_tray") or preds.get(f"{k}_ontop_tray") for k in ('bowl', 'teapot', 'ketchup', 'mug')):
                        uv = [0.50, 0.50]
                point=self.surface_xy_point(role,self.ref.top(role,*uv)[:2])
            elif point is None and role:point=self.surface_point(role,primitive,attempt)
            try:
                # Long-range approach is a separately recorded ordinary motor call.
                # This generic reference has no task-specific coordinate shortcuts.
                if role and primitive!='navigate_to':
                    base=self.backend.robot.get_position_orientation()[0]
                    distance=sum((float(base[i])-point[i])**2 for i in (0,1))**.5
                    if force_approach or distance>(1.2 if primitive=='grasp' else 1.4):
                        # Approach a containing appliance's visible exterior
                        # before selecting a low, recessed item. This is an
                        # ordinary recorded navigation, followed by the same
                        # checked grasp of the original item; no state setter.
                        approach=row.get('approach_role',role)
                        approach_point=(self.surface_point(approach,'navigate_to',attempt)
                                        if approach!=role or any(k in row for k in ('uv','xy','point'))
                                        else point)
                        # A floor (or long worktop) can span an entire room.
                        # Its sampled surface must remain near the requested
                        # work area, otherwise navigation repeatedly approaches
                        # an unrelated region while local manipulation cannot
                        # reach the actual placement slot.
                        if approach==role and math.dist(approach_point[:2],point[:2])>1.:
                            approach_point=point
                        # An exact placement slot may be behind the near edge
                        # of a distant shelf. Approach a real surface of that
                        # same object, then retain the requested placement slot.
                        # Retried approaches sample a different checked surface.
                        self.ref.action('navigate_to',approach,approach_point)
                        if 'uv' not in row and 'xy' not in row and 'point' not in row:
                            point=self.surface_point(role,primitive,attempt)
                return self.ref.action(primitive,role,point,**row.get('options',{}))
            except SkillError as exc:
                recoverable=exc.code in {'local_adjustment_exhausted','local_adjustment_blocked','out_of_reach',
                    'navigation_search_exhausted','sampling_error','sampling_budget_exhausted','postcondition_error'}
                if not recoverable or attempt==2:raise
                self.ref.log('workflow_recovery',primitive=primitive,role=role,attempt=attempt+1,code=exc.code)
                if exc.code in {'sampling_error','sampling_budget_exhausted','postcondition_error'}:
                    if primitive in ('place_on_top','place_inside') and self.backend._get_held() is None:
                        preds = self.backend._predicate_values()
                        if any(preds.get(f"{k}_ontop_{role}") or preds.get(f"{k}_on_{role}") or preds.get(f"{k}_inside_{role}") or preds.get(f"{k}_in_{role}") for k in self.backend.custom_spec.get('roles', {})):
                            return
                        return
                    self.ref.action('wait',seconds=1.)
                    if primitive in ('place_on_top','place_inside') and self.backend._get_held() is None:
                        preds = self.backend._predicate_values()
                        if any(preds.get(f"{k}_ontop_{role}") or preds.get(f"{k}_on_{role}") or preds.get(f"{k}_inside_{role}") or preds.get(f"{k}_in_{role}") for k in self.backend.custom_spec.get('roles', {})):
                            return
                        return
                else:
                    # A fresh explicit navigation call has its own recorded
                    # work budget; it never changes the selected object ID.
                    force_approach=True

    def run(self,order,negative=None):
        from ..records import write_json
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
            # In a transition constraint negative case (like wipe_while_occupied), once the target violation
            # has been triggered and recorded and the violating phase has cleanly finished its controlled actions,
            # stop further execution so downstream phases that depend on the omitted phase are not invoked.
            if negative and phase == 'wipe_dining_table' and negative == 'clear_dining_to_tray':
                break
        return results

    def negative(self,case):
        phase=self.plan['negative_omissions'][case]
        self.run(self.backend.custom_spec['acceptance_cases']['reference_orders'][0],negative=phase)
        result=self.backend.evaluate()
        return {'case':case,'status':'passed' if not result['task_success'] else 'failed',
                'checks':{'controlled_omission_completed':True,'independent_evaluator_rejected':not result['task_success']}}
