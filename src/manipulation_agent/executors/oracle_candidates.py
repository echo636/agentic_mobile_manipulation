"""Select a ranked approach and plan/execute each pose before the next."""
from copy import deepcopy

from ..contracts import SkillError
from ..manip_programs import ENGINE, checked_output
from .oracle_open_loop import OpenLoopPickTrial
from .oracle_pose import ModelPoseManip


class ModelCandidateManip(ModelPoseManip):
    def execute_candidates(self, generated, program):
        batch = checked_output(generated)
        if not isinstance(batch, dict):
            raise SkillError('invalid_program_output', 'Expected ranked candidates')
        candidates = batch['candidates']
        goal_source = 'model_authored_program' if program.get('author', 'model') == 'model' else 'model_selected_program'
        self.arm = candidates[0]['phases'][0]['arm']
        self.trial = None
        attempts, selected = [], None
        try:
            self.preflight(None, 'hold')
            if batch['prepare_gripper'] and self.arm in self.retained:
                raise SkillError('hand_occupied', 'Release the held object in a separate call before preparing an empty grasp')
            self.reference = tuple(v.clone() for v in self.b.robot.get_position_orientation())
            profile = batch.get('planning_profile')
            if profile == 'geometry_pick_v1':
                if self.retained:
                    raise SkillError('hand_occupied', 'Historical grasp planning requires empty hands')
                self.enable_legacy_planning()
            self.event('model_candidates', program=program, candidates=candidates,
                       candidate_count=len(candidates), selection_order='program_list_order',
                       planning_profile=profile, automatic_goal_generation=False)
            with OpenLoopPickTrial(self.b, self.event) as trial:
                self.trial = trial
                trial.joint_limit_checks = self.execution_timing != 'model_authored_v1'
                trial.manifest.update(engine=ENGINE, goal_source=goal_source,
                    timing_policy=self.execution_timing, joint_limit_checks=trial.joint_limit_checks,
                    native_mechanical_limits='unchanged',
                    program=program, candidates=deepcopy(candidates), arm=self.arm,
                    planning_profile=profile,
                    selection_order='program_list_order', automatic_goal_generation=False,
                    planning_scope='plan, execute and verify each pose before planning the next from measured state',
                    selection_basis='first_plannable_phase', fully_preplanned=False,
                    candidate_attempts=attempts, selected_candidate=None)
                if batch['prepare_gripper']:
                    self.set_phase('prepare_gripper/open')
                    self.b._set_gripper(self.arm, 'open')
                self.set_phase('planner_prepare')
                self.get_planner(self.arm)
                result = {'engine': ENGINE, 'goal_source': goal_source,
                          'program': program, 'arm': self.arm, 'frame': 'robot_base',
                          'candidate_attempts': attempts, 'selected_candidate': None,
                          'phases': [], 'reached': False, 'verified': False,
                          'reason': 'no_reachable_candidate'}
                for index, candidate in enumerate(candidates):
                    self.b.deadline.check(changed=bool(trial.steps))
                    attempt = {'index': index, 'name': candidate['name'], 'score': candidate['score'],
                               'phases': [], 'feasible': False, 'execution_started': False}
                    attempts.append(attempt)
                    outcomes, planned_count = [], 0
                    if profile == 'geometry_pick_v1':
                        self.set_legacy_target(candidate['phases'][-1]['contact_target'])
                    for phase in candidate['phases']:
                        q, pose, planning = self.plan_phase(phase, prefix=candidate['name'])
                        if q is None:
                            outcome = {'name': phase['name'], 'planning': planning,
                                       'reached': False, 'verified': False,
                                       'reason': 'model_goal_planning_failed'}
                        else:
                            planned_count += 1
                            if selected is None:
                                # Commit before the first arm motion. A later
                                # failure returns to the model, never to another
                                # candidate generated from the old scene state.
                                selected = candidate
                                result['selected_candidate'] = candidate['name']
                                trial.manifest['selected_candidate'] = candidate['name']
                                self.event('candidate_selected', name=candidate['name'],
                                    candidate_index=index, fully_preplanned=False,
                                    selection_basis='first_plannable_phase')
                            attempt['execution_started'] = True
                            outcome = self.execute_planned_phase(phase, q, pose, planning,
                                                                 prefix=candidate['name'])
                        outcomes.append(outcome)
                        attempt['phases'].append(deepcopy(outcome))
                        if outcome['verified'] is not True:
                            break
                    attempt['feasible'] = planned_count == len(candidate['phases'])
                    self.event('candidate_plan_result', **attempt)
                    if selected is not None:
                        result.update({key: value for key, value in outcomes[-1].items()
                                       if key not in ('name', 'planning')})
                        result['phases'] = outcomes
                        break
                trial.accepted = result['verified'] is True
                if trial.accepted and profile == 'geometry_pick_v1':
                    self.b._program_grasp_planning = dict(profile=profile, arm=self.arm,
                        target=deepcopy(selected['phases'][-1]['contact_target']))
                trial.manifest.update(candidate_attempts=deepcopy(attempts),
                                      selected_candidate=result['selected_candidate'])
                if not trial.accepted:
                    trial.manifest['postcondition_failure'] = result['reason']
            result['execution'] = dict(trial.manifest, trial=str(trial.root))
            return result
        except SkillError:
            raise
        except Exception as exc:
            self.event('exception', exception=type(exc).__name__, message=str(exc)[:1200])
            raise SkillError('model_candidate_failed', f'{self.phase}: {type(exc).__name__}: {str(exc)[:500]}',
                             changed=bool(getattr(self.trial, 'steps', 0))) from exc
        finally:
            self.b._trace_arm = self.b._trace_target = self.b._trace_waypoint = None
