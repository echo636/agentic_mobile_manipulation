"""Private partial-order task scoring. No simulator imports or policy guidance.

Final predicates are evaluated together. Public historical requirements are
checked against committed observations, independent of model notes and plans.
The reference decomposition is deliberately absent from this evaluator.
"""
from __future__ import annotations

import copy


def expression(expr, values):
    if isinstance(expr, str):
        value = values[expr]
        if type(value) is not bool:
            raise ValueError('Predicate value must be a boolean: ' + expr)
        return value
    if not isinstance(expr, dict) or len(expr) != 1:
        raise ValueError('Expected predicate name or one boolean operator')
    op, children = next(iter(expr.items()))
    if op == 'not': return not expression(children, values)
    if op not in {'all', 'any'} or not isinstance(children, list) or not children:
        raise ValueError('Expected nonempty all/any expression')
    results = [expression(child, values) for child in children]
    return all(results) if op == 'all' else any(results)


class TemporalGoalEvaluator:
    def __init__(self, spec):
        self.spec = copy.deepcopy(spec)
        self.milestones = {}; self.events = []; self.violations = []
        self.previous_terminal = {}; self.last_sequence = -1
        predicates = {key: False for key in spec['predicates']}
        if not predicates or not spec['terminal']:
            raise ValueError('A task needs explicit predicates and terminal goals')
        for expr in spec['terminal'].values(): expression(expr, predicates)
        pending = dict(spec.get('milestones', {})); ordered = []
        while pending:
            ready = [key for key, row in pending.items() if set(row.get('after', [])) <= set(ordered)]
            if not ready: raise ValueError('Milestone cycle or unknown dependency')
            for key in ready:
                expression(pending[key]['when'], predicates)
                ordered.append(key); del pending[key]
        self.order = ordered
        guards = [*spec.get('motion_guards', []), *spec.get('state_guards', []), *spec.get('carry_guards', [])]
        if len({rule['id'] for rule in guards}) != len(guards):
            raise ValueError('Constraint IDs must be unique')
        for rule in spec.get('motion_guards', []):
            if rule['since'] not in ordered or rule['until'] not in ordered:
                raise ValueError('Motion guard references unknown milestones')
            expression(rule['require'], predicates)
        for rule in spec.get('state_guards', []):
            if rule['until'] is not None and rule['until'] not in ordered:
                raise ValueError('State guard references unknown milestone')
            expression(rule['require'], predicates)

    def observe(self, values, sequence, *, env_step, initial=False):
        if sequence <= self.last_sequence: raise ValueError('Observation sequence must increase')
        self.last_sequence = sequence
        # Simultaneous prerequisites are allowed: event order reflects physical
        # conditions, not an artificial demand for one model action per goal.
        if not initial:
            for rule in self.spec.get('state_guards', []):
                if rule['until'] not in self.milestones and not expression(rule['require'], values) \
                        and not any(v['id']==rule['id'] for v in self.violations):
                    self.violations.append({'id':rule['id'],'env_step':env_step,'sequence':sequence,
                                            'kind':'public_ordering_constraint'})
            for key in self.order:
                row = self.spec['milestones'][key]
                if key not in self.milestones and all(dep in self.milestones for dep in row.get('after', [])) \
                        and expression(row['when'], values):
                    event = {'kind': 'milestone', 'id': key, 'sequence': sequence, 'env_step': env_step}
                    self.milestones[key] = event; self.events.append(event)
        terminal = {key: expression(expr, values) for key, expr in self.spec['terminal'].items()}
        for key, value in terminal.items():
            if self.previous_terminal.get(key) != value:
                self.events.append({'kind': 'terminal_state', 'id': key, 'satisfied': value,
                                    'sequence': sequence, 'env_step': env_step, 'initial': initial})
        self.previous_terminal = terminal
        return terminal

    def check_motion(self, values, moved_roles, *, env_step):
        for rule in self.spec.get('motion_guards', []):
            if rule['object'] in moved_roles and rule['since'] in self.milestones \
                    and rule['until'] not in self.milestones and not expression(rule['require'], values):
                # Preserve first violation for each publicly declared constraint.
                if not any(v['id'] == rule['id'] for v in self.violations):
                    self.violations.append({'id': rule['id'], 'env_step': env_step,
                                            'kind': 'public_transport_constraint',
                                            'predicate_witness': copy.deepcopy(values)})

    def result(self, values):
        terminal = {key: expression(expr, values) for key, expr in self.spec['terminal'].items()}
        history = {key: key in self.milestones for key in self.order}
        violations = {v['id'] for v in self.violations}
        constraints = {rule['id']:rule['id'] not in violations for rule in
                       [*self.spec.get('motion_guards',[]),*self.spec.get('state_guards',[]),
                        *self.spec.get('carry_guards',[])]}
        satisfied = sum(terminal.values()) + sum(history.values()) + sum(constraints.values())
        total = len(terminal) + len(history) + len(constraints)
        return {'task_success': all(terminal.values()) and all(history.values()) and not self.violations,
                'terminal_conditions': terminal, 'historical_conditions': history,
                'constraint_conditions': constraints,
                'terminal_satisfaction_fraction': sum(terminal.values()) / len(terminal),
                'goal_satisfaction_fraction': satisfied / total,
                'public_constraint_violations': copy.deepcopy(self.violations),
                'milestone_events': copy.deepcopy(self.milestones),
                'goal_events': copy.deepcopy(self.events),
                'score_type': self.spec.get('score_type', 'custom_joint_terminal_and_public_history_v1'),
                'official_submission_eligible': False,
                'protocol': 'custom_rgb_autonomous_partial_order_task',
                'model_notes_used_for_scoring': False}
