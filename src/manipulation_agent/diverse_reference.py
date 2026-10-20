"""Task-layer references using only existing checked actions, never state setters."""


class DiverseReference:
    def __init__(self, reference):
        self.ref=reference
        self.backend=reference.backend
        self.plan=self.backend.custom_spec['reference_plan']

    def transfer(self,item,target,xy=None):
        self.ref.move('grasp',item)
        point=None if xy is None else [*xy,float(self.ref.obj(target).aabb[1][2])]
        self.ref.move('place_on_top',target,point)

    def acquire(self,tool=None):
        tool=tool or self.plan['tool']
        self.ref.move('grasp',tool)
        if self.plan.get('toggle') and tool==self.plan['tool']:
            self.ref.move('toggle_on',tool)

    def return_tool(self,tool=None,xy=None):
        tool=tool or self.plan['tool']
        if self.plan.get('toggle') and tool==self.plan['tool']:
            self.ref.move('toggle_off',tool)
        home=self.plan['tool_home']
        self.ref.move('place_on_top',home,[*(xy or self.plan['tool_xy']),float(self.ref.obj(home).aabb[1][2])])

    def run(self,order,negative=None):
        if self.plan['kind']=='branching_library':
            from .branching_reference import BranchingLibraryReference
            return BranchingLibraryReference(self.ref).run(order,negative)
        if order not in self.backend.custom_spec['acceptance_cases']['reference_orders']:
            raise ValueError('Unknown constructed-task reference order')
        plan=self.plan
        if plan['kind']=='service_items':
            work=list(plan['work'])
            if order=='reverse':work.reverse()
            for index,w in enumerate(work):
                chosen=index==len(work)-1
                if not(chosen and negative=='skip_stage'):
                    self.transfer(w['item'],w['stage'],w['stage_xy'])
                if not(chosen and negative=='skip_work'):
                    self.acquire();self.ref.move(w['primitive'],w['item']);self.return_tool()
                if not(chosen and negative=='missing_delivery'):
                    self.transfer(w['item'],w['destination'],w['destination_xy'])
        elif plan['kind']=='clear_clean_restore':
            rooms=list(plan['rooms'])
            if order=='reverse':rooms.reverse()
            for index,room in enumerate(rooms):
                chosen=index==len(rooms)-1
                work=list(room['work'])
                if order=='reverse':work.reverse()
                for i,w in enumerate(work):
                    if not(chosen and i==len(work)-1 and negative=='skip_stage'):
                        self.transfer(w['item'],w['stage'],w['stage_xy'])
                if not(chosen and negative=='skip_work'):
                    self.acquire();self.ref.move('vacuum',room['floor']);self.return_tool()
                for i,w in enumerate(work):
                    if not(chosen and i==len(work)-1 and negative=='missing_delivery'):
                        self.transfer(w['item'],w['destination'],w['destination_xy'])
            for lamp in plan['lights']:self.ref.move('toggle_off',lamp)
        elif plan['kind']=='cut_store':
            self.acquire(plan['broom']);self.ref.move('sweep',plan['sweep_floor'])
            self.return_tool(plan['broom'],plan['broom_xy'])
            work=list(plan['work'])
            if order=='reverse':work.reverse()
            for index,w in enumerate(work):
                chosen=index==len(work)-1
                if not(chosen and negative=='skip_stage'):
                    self.transfer(w['item'],w['stage'],w['stage_xy'])
                if chosen and negative=='skip_work':continue
                self.acquire();self.ref.move('cut',w['item']);self.return_tool()
                for i,child in enumerate(w['children']):
                    if not(chosen and i==len(w['children'])-1 and negative=='missing_delivery'):
                        self.transfer(child,w['destination'],w['destination_xy'][i])
        else:raise ValueError('Unknown constructed-task reference kind')

    def negative(self,case):
        if self.plan['kind']=='branching_library':
            from .branching_reference import BranchingLibraryReference
            return BranchingLibraryReference(self.ref).negative(case)
        if case not in self.backend.custom_spec['acceptance_cases']['physical_negative_cases']:
            raise ValueError('Unknown physical negative case')
        self.run('forward',negative=case)
        result=self.backend.evaluate()
        # These checks inspect actual simulator-scored predicates after a full
        # controlled omission. Exceptions do not count as a successful negative.
        if case=='skip_stage':
            witness=bool(result['public_constraint_violations']) and not all(result['historical_conditions'].values())
        else:
            witness=not all(result['terminal_conditions'].values())
        checks={'controlled_omission_completed':True,'physical_witness':witness,
                'independent_evaluator_rejected':result['task_success'] is False}
        return {'case':case,'status':'passed' if all(checks.values()) else 'failed','checks':checks}
