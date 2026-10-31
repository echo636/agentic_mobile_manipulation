"""Opt-in compatibility for native cloth assets with nested visual meshes.

Only the runtime USD copy is normalized. Geometry, material coordinates and the
cloth primitive type remain intact; the installed simulator loader and remesher
perform the actual conversion. Simulator imports stay inside the entry point.
"""
from __future__ import annotations

import json
from pathlib import Path

PROTOCOL = 'nested_single_visual_cloth_mesh_normals_v2'


def missing_vertex_normals(points, counts, indices):
    import numpy as np
    import trimesh

    points = np.asarray(points, dtype=np.float64)
    counts = np.asarray(counts, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.int64)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError('Malformed cloth vertices')
    if counts.ndim != 1 or indices.ndim != 1 or (counts < 3).any() or counts.sum() != len(indices):
        raise ValueError('Malformed cloth face indices')
    if len(indices) == 0 or indices.min() < 0 or indices.max() >= len(points):
        raise ValueError('Cloth face references an absent vertex')
    faces, offset = [], 0
    for count in counts:
        for j in range(1, int(count) - 1):
            faces.append([indices[offset], indices[offset+j], indices[offset+j+1]])
        offset += int(count)
    normals = np.asarray(trimesh.Trimesh(vertices=points, faces=faces, process=False).vertex_normals,
                         dtype=np.float32)
    if normals.shape != points.shape or not np.isfinite(normals).all():
        raise ValueError('Unable to compute finite cloth vertex normals')
    return normals


def install(record_path):
    import numpy as np
    import omnigibson as og
    import omnigibson.lazy as lazy
    from omnigibson.prims.entity_prim import EntityPrim
    from omnigibson.utils.constants import PrimType

    original = EntityPrim._post_load
    if getattr(original, '_manipulation_cloth_compatibility', None):
        raise RuntimeError('Cloth compatibility already installed in this process')
    records, record_path = [], Path(record_path)
    record_path.write_text(json.dumps({'protocol': PROTOCOL, 'operations': records}, indent=2) + '\n')

    def adapted(obj):
        if obj.prim_type == PrimType.CLOTH:
            links = [p for p in obj._prim.GetChildren() if str(p.GetTypeName()) == 'Xform']
            if len(links) == 1:
                link = links[0]
                visual = lambda p: str(p.GetTypeName()) == 'Mesh' and not p.HasAPI(lazy.pxr.UsdPhysics.CollisionAPI)
                direct = [p for p in link.GetChildren() if visual(p)]
                if not direct:
                    candidates = [p for p in lazy.pxr.Usd.PrimRange(link) if visual(p)]
                    if len(candidates) != 1:
                        raise ValueError('Cloth compatibility requires exactly one nested visual mesh')
                    mesh = candidates[0]
                    stamp = lazy.pxr.Usd.TimeCode.Default()
                    relative = (lazy.pxr.UsdGeom.Xformable(mesh).ComputeLocalToWorldTransform(stamp)
                                * lazy.pxr.UsdGeom.Xformable(link).ComputeLocalToWorldTransform(stamp).GetInverse())
                    destination = str(link.GetPath()) + '/compatibility_visual_mesh'
                    with og.sim.editing_usd():
                        lazy.omni.kit.commands.execute('CopyPrim', path_from=mesh.GetPath(), path_to=destination)
                        copied = og.sim.stage.GetPrimAtPath(destination)
                        if not copied.IsValid():
                            raise RuntimeError('Cloth compatibility mesh copy failed')
                        lazy.pxr.UsdGeom.Xformable(copied).MakeMatrixXform().Set(relative)
                        for key in ('points', 'faceVertexCounts', 'faceVertexIndices', 'primvars:st'):
                            if not np.array_equal(np.asarray(mesh.GetAttribute(key).Get()),
                                                  np.asarray(copied.GetAttribute(key).Get())):
                                raise ValueError('Cloth geometry or texture changed during compatibility copy')
                        generated = copied.GetAttribute('normals').Get() is None
                        if generated:
                            normals = missing_vertex_normals(
                                copied.GetAttribute('points').Get(),
                                copied.GetAttribute('faceVertexCounts').Get(),
                                copied.GetAttribute('faceVertexIndices').Get())
                            copied.GetAttribute('normals').Set(lazy.pxr.Vt.Vec3fArray.FromNumpy(normals))
                            lazy.pxr.UsdGeom.Mesh(copied).SetNormalsInterpolation('vertex')
                    records.append(dict(object=obj.name, source=str(mesh.GetPath()), destination=destination,
                                        geometry_and_texture_arrays_unchanged=True,
                                        relative_transform_preserved=True, generated_normals=generated,
                                        original_asset_unmodified=True))
                    record_path.write_text(json.dumps({'protocol': PROTOCOL, 'operations': records}, indent=2) + '\n')
        return original(obj)

    adapted._manipulation_cloth_compatibility = PROTOCOL
    EntityPrim._post_load = adapted
    return original
