"""Freeze native remeshing output together with cloth particle state."""
from pathlib import Path
import hashlib
import json

PROTOCOL = 'native_remeshed_cloth_snapshot_v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(directory, record_path, *, capture=False, manifest_sha256=None):
    import numpy as np
    import torch
    import omnigibson as og
    import omnigibson.lazy as lazy
    from omnigibson.prims.cloth_prim import ClothPrim, CLOTH_CONFIGURATIONS

    directory = Path(directory)
    record_path = Path(record_path)
    if capture:
        directory.mkdir(exist_ok=False)
        manifest = {'protocol': PROTOCOL, 'files': {}}
    else:
        path = directory / 'manifest.json'
        if digest(path) != manifest_sha256:
            raise ValueError('Cloth topology manifest hash mismatch')
        manifest = json.loads(path.read_text())
        if manifest['protocol'] != PROTOCOL:
            raise ValueError('Unsupported cloth topology protocol')
        for filename, sha in manifest['files'].items():
            if Path(filename).name != filename or digest(directory / filename) != sha:
                raise ValueError('Cloth topology input hash mismatch')
    records = []
    original = ClothPrim._remesh

    def remesh(cloth):
        filename = hashlib.sha256(cloth.name.encode()).hexdigest() + '.json'
        path = directory / filename
        if capture:
            original(cloth)
            fields = {}
            for name in ('points', 'faceVertexCounts', 'faceVertexIndices', 'normals', 'primvars:st'):
                attr = cloth.prim.GetAttribute(name)
                value = attr.Get()
                fields[name] = None if value is None else np.asarray(value).tolist()
            data = {'protocol': PROTOCOL, 'name': cloth.name, 'scale': cloth.scale.tolist(),
                    'attributes': fields,
                    'normals_interpolation': str(lazy.pxr.UsdGeom.Mesh(cloth.prim).GetNormalsInterpolation()),
                    'uv_interpolation': str(lazy.pxr.UsdGeom.PrimvarsAPI(cloth.prim).GetPrimvar('st').GetInterpolation())}
            path.write_text(json.dumps(data, separators=(',', ':')) + '\n')
            manifest['files'][filename] = digest(path)
            (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        else:
            if filename not in manifest['files']:
                raise ValueError('No frozen native topology for ' + cloth.name)
            data = json.loads(path.read_text())
            if data['name'] != cloth.name or not np.allclose(data['scale'], cloth.scale.cpu().numpy(), atol=1e-7, rtol=0):
                raise ValueError('Frozen native cloth identity or scale changed')
            fields = data['attributes']
            with og.sim.editing_usd():
                for config in CLOTH_CONFIGURATIONS:
                    if cloth.prim.HasAttribute('points_' + config):
                        cloth.prim.RemoveProperty('points_' + config)
                for name in ('faceVertexCounts', 'faceVertexIndices', 'normals', 'primvars:st'):
                    values = fields[name]
                    if values is None:
                        continue
                    array = np.asarray(values, dtype=np.float32 if name in ('normals', 'primvars:st') else np.int32)
                    if name == 'normals':
                        array = lazy.pxr.Vt.Vec3fArray.FromNumpy(array)
                    elif name == 'primvars:st':
                        array = lazy.pxr.Vt.Vec2fArray.FromNumpy(array)
                    cloth.prim.GetAttribute(name).Set(array)
                lazy.pxr.UsdGeom.Mesh(cloth.prim).SetNormalsInterpolation(data['normals_interpolation'])
                if fields['primvars:st'] is not None:
                    lazy.pxr.UsdGeom.PrimvarsAPI(cloth.prim).GetPrimvar('st').SetInterpolation(data['uv_interpolation'])
            cloth.save_configuration('default', torch.tensor(fields['points'], dtype=torch.float32))
            cloth.reset_points_to_configuration('default')
        records.append({'object': cloth.name, 'mode': 'capture_native_remesh' if capture else 'restore_frozen_native_remesh',
                        'topology_sha256': digest(path), 'vertices': len(data['attributes']['points']),
                        'triangles': len(data['attributes']['faceVertexIndices']) // 3})
        record_path.write_text(json.dumps({'protocol': PROTOCOL, 'objects': records}, indent=2) + '\n')

    ClothPrim._remesh = remesh
    return records
