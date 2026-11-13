"""Recorded native-instance model selection and sampling diagnostics only."""
from contextlib import contextmanager
import json
from pathlib import Path
import time


def select_eligible_models(eligible, category, binding, selections):
    """Narrow the original sampler's eligible set; never bypass its checks."""
    if binding not in selections:
        return eligible
    selected = selections[binding]
    if selected['category'] != category:
        return set()
    result = set(eligible).intersection(selected['models'])
    if not result:
        raise ValueError('Declared model is not eligible for '+binding)
    return result


@contextmanager
def sampling_diagnostics(sampler_class, directory, options):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    selections=options.get('object_models',{})
    if any(binding!='tray.n.01_4' or row!={'category':'tray','models':['huwhjg']} for binding,row in selections.items()):
        raise ValueError('This diagnostic revision only selects the probed pancake tray')
    original_filter=sampler_class._filter_model_choices_by_attached_states
    original_sample=sampler_class._sample_predicate
    snapshot_saved=False
    count=0
    def append(name,row):
        with (directory/name).open('a') as stream:stream.write(json.dumps(row)+'\n')
    def filter_models(self,eligible,category,binding):
        original=original_filter(self,eligible,category,binding)
        if self._activity_conditions.behavior_activity!='prepare_a_breakfast_bar':return original
        result=select_eligible_models(original,category,binding,selections)
        if binding in selections:
            append('model_selection.jsonl',{'binding':binding,'category':category,'original_eligible':sorted(original),'selected_eligible':sorted(result),'scale_changed':False})
        return result
    def sample(self,predicate_cls,*args,**kwargs):
        nonlocal count,snapshot_saved
        if self._activity_conditions.behavior_activity!='prepare_a_breakfast_bar':return original_sample(self,predicate_cls,*args,**kwargs)
        count+=1;event={'index':count,'predicate':getattr(predicate_cls,'__name__',str(predicate_cls)),'arguments':[str(x) for x in args],'kwargs':kwargs,'started_at_unix':time.time()}
        event['objects']={}
        for name in args:
            obj=self._object_scope.get(name) if isinstance(name,str) else None
            if obj is not None and hasattr(obj,'aabb'):
                event['objects'][name]={'name':obj.name,'category':obj.category,'model':getattr(obj,'model',None),'scale':obj.scale.tolist(),'aabb':[x.tolist() for x in obj.aabb]}
        append('sampling_calls.jsonl',dict(event,phase='before'))
        if not snapshot_saved and any(str(x).startswith('buttermilk_pancake.') for x in args):
            snapshot_saved=True
            try:
                self._env.scene.save(json_path=str(directory/'before_pancake_sampling.json'))
                (directory/'bindings.json').write_text(json.dumps({k:getattr(v,'name',None) for k,v in self._object_scope.items()},indent=2)+'\n')
            except Exception as exc:append('diagnostic_errors.jsonl',{'snapshot_error':str(exc)})
        begin=time.monotonic()
        try:
            result=original_sample(self,predicate_cls,*args,**kwargs)
            append('sampling_calls.jsonl',dict(event,phase='after',seconds=time.monotonic()-begin,result=bool(result)))
            return result
        except BaseException as exc:
            append('sampling_calls.jsonl',dict(event,phase='error',seconds=time.monotonic()-begin,error=str(exc)))
            raise
    sampler_class._filter_model_choices_by_attached_states=filter_models
    sampler_class._sample_predicate=sample
    try:yield
    finally:
        sampler_class._filter_model_choices_by_attached_states=original_filter
        sampler_class._sample_predicate=original_sample
