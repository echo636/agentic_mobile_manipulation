"""Private, low-overhead component timings; never reported as model observations."""
from contextlib import contextmanager
from functools import wraps
import json
import time
from ..records import now

@contextmanager
def component(backend, name):
    start=time.perf_counter()
    try:yield
    finally:
        totals=getattr(backend,'_component_totals',None)
        if totals is None:totals=backend._component_totals={}
        row=totals.setdefault(name,{'seconds':0.,'calls':0})
        row['seconds']+=time.perf_counter()-start;row['calls']+=1

def action_profile(method):
    @wraps(method)
    def wrapped(self, primitive, *args, **kwargs):
        start=time.perf_counter();before={k:dict(v) for k,v in getattr(self,'_component_totals',{}).items()};status='failed'
        try:
            result=method(self,primitive,*args,**kwargs);status='passed';return result
        finally:
            after=getattr(self,'_component_totals',{})
            row={'at':now(),'primitive':primitive,'status':status,'wall_seconds':time.perf_counter()-start,
                 'components':{k:{field:v[field]-before.get(k,{}).get(field,0) for field in v} for k,v in after.items()},
                 'scope':'Nested inclusive component timings, not additive; private executor evidence'}
            with (self.output/'performance.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
    return wrapped
