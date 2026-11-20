"""Host-side boundaries for a frozen controller's single Manip call.

The recipe executes in the existing offline sandbox. The broker validates its
inputs and child permissions, and records actual boundaries and request IDs.
"""
from copy import deepcopy
import json
import uuid
from .manip_runtime import ManipRecipes, RecipeContext, checked_invocation
from .records import now


class FrozenManip:
    def __init__(self, bundle, call, snapshot, trace):
        self.bundle, self.call, self.snapshot, self.trace = bundle, call, snapshot, trace
        self.active = None
        self.failed = False
        self.records = []

    def event(self, kind, **values):
        record = {'kind': kind, 'id': 'procedure-'+uuid.uuid4().hex, 'at': now(), **values}
        self.records.append(record)
        with self.trace.open('a') as stream:
            stream.write(json.dumps(record, allow_nan=False)+'\n')

    def begin(self, arguments, request_id):
        if self.active is not None or self.failed:
            raise ValueError('Nested or failed procedures cannot start another Manip')
        entry = ManipRecipes.frozen_project(self.bundle)
        snapshot = self.snapshot()
        self.event('procedure_call', name='manip', request_id=request_id,
                   arguments=deepcopy(arguments), source_sha256=entry['source_sha256'],
                   controller_sha256=entry['controller_sha256'], observation=deepcopy(snapshot),
                   origin='frozen_controller')
        try:
            invocation = checked_invocation(entry, arguments)
            if snapshot is None:
                snapshot = self.call('observe_scene', {}, request_id+'-snapshot').get('observation')
            if snapshot is None or invocation['revision'] != snapshot['revision']:
                raise ValueError('Stale Manip revision')
        except Exception as exc:
            self.failed = True
            self.event('procedure_result', name='manip', request_id=request_id,
                       result={'ok': False, 'phase': 'validate_parameters', 'error': str(exc),
                               'observation': deepcopy(self.snapshot())}, origin='frozen_controller')
            raise
        self.active = {'request_id': request_id, 'entry': entry,
                       'context': RecipeContext(self.call, self.snapshot, invocation['constraints'])}
        return {'ok': True, 'invocation': invocation}

    def child(self, name, arguments, request_id):
        if self.active is None:
            if self.failed:
                raise ValueError('Controller stopped after a failed Manip')
            return self.call(name, arguments, request_id)
        self.event('procedure_child', parent_request_id=self.active['request_id'], request_id=request_id)
        return self.active['context'].invoke(name, arguments, request_id)

    def end(self, result, request_id):
        if self.active is None or request_id != self.active['request_id']:
            raise ValueError('No matching Manip call')
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            result = {'ok': False, 'error': 'Manip must return a dict with boolean ok'}
        result = deepcopy(result)
        if self.active['context'].failed:
            result['ok'] = False
        result.update(recipe=self.active['entry']['recipe'],
                      source_sha256=self.active['entry']['source_sha256'],
                      observation=deepcopy(self.snapshot()))
        self.event('procedure_result', name='manip', request_id=request_id, result=result,
                   origin='frozen_controller')
        self.failed = result['ok'] is False
        self.active = None
        return result

    def incomplete(self):
        if self.active is not None:
            self.end({'ok': False, 'error': 'Sandbox exited before Manip returned'}, self.active['request_id'])
            return True
        return False
