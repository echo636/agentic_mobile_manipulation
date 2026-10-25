"""Avoid the reproduced GPU articulation sleep-transition crash in Isaac 5.1.

R303 crashes immediately after two articulations sleep; R299 and R300 survive
with articulation sleeping disabled. PhysX #551 describes a matching staging
defect, also present in the public 5.6.1 source. This is a runtime workaround,
not a claim that the packaged library's exact invalid instruction is proven.
"""
from functools import wraps
from importlib import metadata


class ArticulationSleepPolicy:
    """Process-local hooks, installed before scene load and kept through resets.

    The pinned schema has no scene-wide disableSleeping attribute. Threshold zero
    prevents natural sleeping; redirecting explicit sleep also covers saved-state
    restoration. Plain rigid bodies retain their normal sleep behavior. Keeping
    articulations awake can change settling and costs additional physics work.
    """

    def __init__(self, *, gpu_dynamics):
        self._originals = None
        self.explicit_sleep_requests = 0
        self.record = dict(enabled=False, policy='native', gpu_dynamics=bool(gpu_dynamics))
        if not gpu_dynamics:
            return
        version = metadata.version('isaacsim')
        self.record['isaacsim'] = version
        if version != '5.1.0.0':
            return
        import omni.kit.app
        from omnigibson.prims.entity_prim import EntityPrim

        extension = omni.kit.app.get_app().get_extension_manager().get_enabled_extension_id('omni.physx')
        self.record['physx_extension'] = extension
        if extension != 'omni.physx-107.3.26':
            raise RuntimeError('GPU articulation sleep workaround requires validated PhysX 107.3.26')
        original_post_load = EntityPrim._post_load
        original_sleep = EntityPrim.sleep
        threshold = EntityPrim.sleep_threshold
        if getattr(original_sleep, '_mas_no_articulation_sleep', False):
            raise RuntimeError('An articulation sleep policy is already active in this simulator process')

        @wraps(original_post_load)
        def post_load(obj):
            result = original_post_load(obj)
            if obj.articulated:
                threshold.fset(obj, 0.0)
            return result

        def set_threshold(obj, value):
            return threshold.fset(obj, 0.0 if obj.articulated else value)

        @wraps(original_sleep)
        def sleep(obj):
            if obj.articulated:
                self.explicit_sleep_requests += 1
                return obj.wake()
            return original_sleep(obj)

        sleep._mas_no_articulation_sleep = True
        self._originals = (EntityPrim, original_post_load, original_sleep, threshold)
        EntityPrim._post_load = post_load
        EntityPrim.sleep_threshold = property(threshold.fget, set_threshold, threshold.fdel, threshold.__doc__)
        EntityPrim.sleep = sleep
        self.record.update(enabled=True, policy='articulations_always_awake',
                           validation_reference='long_horizon_full_v2_20261022/R299,R300',
                           upstream_candidate='https://github.com/NVIDIA-Omniverse/PhysX/issues/551',
                           task_predicates_changed=False, native_library_modified=False)

    def validate_scene(self, scene):
        if not self.record['enabled']:
            return
        objects = [dict(name=obj.name, links=len(obj.links),
                        threshold=float(obj.sleep_threshold), asleep=bool(obj.is_asleep))
                   for obj in scene.objects if obj.articulated]
        self.record['initial_articulations'] = objects
        if any(row['threshold'] != 0.0 or row['asleep'] for row in objects):
            raise RuntimeError('Articulation sleep workaround was not applied during scene initialization')

    def describe(self):
        return dict(self.record, explicit_sleep_requests_redirected=self.explicit_sleep_requests)

    def close(self):
        if self._originals is not None:
            entity, post_load, sleep, threshold = self._originals
            entity._post_load, entity.sleep, entity.sleep_threshold = post_load, sleep, threshold
            self._originals = None
