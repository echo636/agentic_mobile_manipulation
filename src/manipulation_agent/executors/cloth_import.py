"""Adapt grouped cloth visuals to the pinned native cloth importer."""
from functools import wraps
import hashlib
import json
from pathlib import Path


def mesh_with_optional_normals(native, mesh_prim, include_normals=True, include_texcoord=True):
    # USD normals are optional. The pinned converter otherwise turns None into
    # an object-dtype array and fails before native cloth remeshing. Trimesh
    # computes missing normals from the unchanged points and topology.
    if include_normals and mesh_prim.GetAttribute('normals').Get() is None:
        include_normals = False
    return native(mesh_prim, include_normals=include_normals, include_texcoord=include_texcoord)


def prepare_nested_cloth_visual(root, copy_prim):
    """Expose a unique nested visual to EntityPrim's direct-child lookup.

    Called immediately before native _post_load, while USD is editable. The
    native importer copies this mesh to the object root and deletes the old
    link. Preserve the transform at that final destination, including the
    intermediate visual group and link transforms.
    """
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    links = [p for p in root.GetChildren() if p.GetTypeName() == 'Xform']
    if len(links) != 1:
        raise ValueError(f'Cloth {root.GetPath()} needs one link; found {len(links)}')
    link = links[0]
    is_visual = lambda p: p.GetTypeName() == 'Mesh' and not p.HasAPI(UsdPhysics.CollisionAPI)
    direct = [p for p in link.GetChildren() if is_visual(p)]
    if direct:
        # Already supported by the native importer, including its ambiguity
        # assertion. Do not alter existing direct-mesh assets.
        return None
    meshes = [p for p in Usd.PrimRange(link) if is_visual(p)]
    if len(meshes) != 1:
        raise ValueError(f'Cloth {root.GetPath()} needs one visual mesh; found {len(meshes)}')
    mesh = meshes[0]
    destination = link.GetPath().AppendChild('_mas_cloth_visual')
    stage = root.GetStage()
    if stage.GetPrimAtPath(destination):
        raise ValueError(f'Cloth import destination already exists: {destination}')
    cache = UsdGeom.XformCache()
    transform = cache.GetLocalToWorldTransform(mesh) * cache.GetLocalToWorldTransform(root).GetInverse()
    material, _ = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
    visibility = UsdGeom.Imageable(mesh).ComputeVisibility()
    purpose = UsdGeom.Imageable(mesh).ComputePurpose()
    copy_prim(mesh.GetPath(), destination)
    copied = stage.GetPrimAtPath(destination)
    if not copied or copied.GetTypeName() != 'Mesh':
        raise RuntimeError(f'Cloth mesh copy failed: {destination}')
    UsdGeom.Xformable(copied).MakeMatrixXform().Set(transform)
    UsdGeom.Xformable(copied).SetResetXformStack(False)
    UsdGeom.Imageable(copied).CreateVisibilityAttr().Set(visibility)
    UsdGeom.Imageable(copied).CreatePurposeAttr().Set(purpose)
    if material:
        UsdShade.MaterialBindingAPI.Apply(copied).Bind(material)
    return dict(object=str(root.GetPath()), source_mesh=str(mesh.GetPath()),
                prepared_mesh=str(destination), change='expose_nested_cloth_visual',
                geometry_and_collision_meshes_changed=False)


def install_nested_cloth_import(output):
    import omnigibson as og
    import omnigibson.lazy as lazy
    from omnigibson.prims import entity_prim as module
    from omnigibson.utils.constants import PrimType
    from omnigibson.utils import usd_utils

    native_mesh = usd_utils.mesh_prim_mesh_to_trimesh_mesh
    if not getattr(native_mesh, '_mas_optional_mesh_normals', False):
        @wraps(native_mesh)
        def convert_mesh(mesh_prim, include_normals=True, include_texcoord=True):
            return mesh_with_optional_normals(native_mesh, mesh_prim, include_normals, include_texcoord)
        convert_mesh._mas_optional_mesh_normals = True
        usd_utils.mesh_prim_mesh_to_trimesh_mesh = convert_mesh

    original = module.EntityPrim._post_load
    if not getattr(original, '_mas_nested_cloth_import', False):
        @wraps(original)
        def post_load(entity):
            if entity._prim_type == PrimType.CLOTH:
                def copy_prim(source, destination):
                    lazy.omni.kit.commands.execute('CopyPrim', path_from=source, path_to=str(destination))
                with og.sim.editing_usd():
                    record = prepare_nested_cloth_visual(entity._prim, copy_prim)
                if record is not None:
                    with (Path(output) / 'cloth_imports.jsonl').open('a') as stream:
                        stream.write(json.dumps(record) + '\n')
            return original(entity)

        post_load._mas_nested_cloth_import = True
        module.EntityPrim._post_load = post_load
    return dict(status='configured', scope='nested_visual_cloth_import',
                optional_normals='derive missing normals from native mesh geometry',
                upstream_path=module.__file__,
                upstream_sha256=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                task_predicates_changed=False)
