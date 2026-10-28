"""Keep MaxTemperature's dependency rows attached to the same live object."""
import hashlib
import json
from pathlib import Path


def install_temperature_index_repair(output):
    from omnigibson.object_states import max_temperature as module
    from omnigibson.object_states.temperature import Temperature
    from omnigibson.object_states.tensorized_value_state import TensorizedValueState
    cls = module.MaxTemperature
    if not getattr(cls, '_mas_dependency_rows_repaired', False):
        def remove_object(state_cls, obj):
            # Remove this state's own row. The Temperature removal callback
            # adjusts dependency indices when its separate array loses a row.
            # Decrementing here as well shifts surviving objects twice.
            index = state_cls.OBJ_IDXS[obj]
            state_cls.TEMPERATURE_IDXS = module.torch_delete(state_cls.TEMPERATURE_IDXS, [index])
            TensorizedValueState._remove_obj.__func__(state_cls, obj)
        cls._remove_obj = classmethod(remove_object)
        cls._mas_dependency_rows_repaired = True
    changes = []
    if cls.IDX_OBJS:
        old = cls.TEMPERATURE_IDXS
        correct = old.new_tensor([Temperature.OBJ_IDXS[obj] for obj in cls.IDX_OBJS])
        changes = [dict(object=obj.name, old_index=int(old[i]), temperature_index=int(correct[i]))
                   for i, obj in enumerate(cls.IDX_OBJS) if int(old[i]) != int(correct[i])]
        # Only repair the lookup cache. Current and historical temperature
        # values are untouched; ordinary native updates advance them later.
        cls.TEMPERATURE_IDXS = correct
    report = dict(status='installed', repair='max_temperature_dependency_identity',
        mechanism='Temperature callback exclusively shifts dependent indices; MaxTemperature removes only its own row',
        repaired_existing_rows=changes, temperature_values_changed=False,
        max_temperature_values_changed=False, task_predicates_changed=False,
        upstream_path=module.__file__, upstream_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest())
    Path(output, 'temperature_index_compatibility.json').write_text(json.dumps(report, indent=2)+'\n')
    return report
