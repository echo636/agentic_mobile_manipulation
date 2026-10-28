"""Honor every authored heating element without multiplying overlapping heat."""
import hashlib
from pathlib import Path


def update_heat_source_links(state):
    import torch
    import omnigibson as og
    from omnigibson.object_states import Temperature
    from omnigibson.utils.constants import PrimType

    affected = set()
    if state.get_value():
        def overlap(hit):
            obj = state.obj.scene.object_registry('prim_path', hit.rigid_body.rsplit('/', 1)[0])
            if obj is not None and obj is not state.obj and obj in Temperature.OBJ_IDXS:
                affected.add(obj)
            return True

        positions = [link.aabb_center if link is state._default_link
                     else link.get_position_orientation()[0] for link in state.links.values()]
        for position in positions:
            og.sim.psqi.overlap_sphere(radius=state.distance_threshold,
                                      pos=position.tolist(), reportFn=overlap)
        # Match native cloth handling: cloth is absent from PhysX overlap queries.
        for obj in state.obj.scene.object_registry('prim_type', PrimType.CLOTH, []):
            if (obj in Temperature.OBJ_IDXS and obj is not state.obj
                    and any(torch.norm(obj.get_position_orientation()[0] - position)
                            <= state.distance_threshold for position in positions)):
                affected.add(obj)
    state._affected_objects = affected
    if affected:
        Temperature.update_temperature_from_heatsource_or_sink(
            objs=affected, temperature=state.temperature, rate=state.heating_rate)


def install_heat_source_links():
    from omnigibson.object_states import heat_source_or_sink as module
    cls = module.HeatSourceOrSink
    if not getattr(cls, '_mas_all_heat_links', False):
        native_update = cls._update

        def update(state):
            if state.requires_inside or len(state.links) <= 1:
                return native_update(state)
            return update_heat_source_links(state)

        cls._update = update
        cls._mas_all_heat_links = True
    return dict(status='installed', repair='all_authored_heat_source_links',
                scope='Multiple distance-based heat links; native single-link and enclosed heating unchanged',
                heating='Union of native overlap queries, one native temperature update per object',
                radius_rate_and_temperature_unchanged=True, task_predicates_changed=False,
                upstream_path=module.__file__,
                upstream_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest())
