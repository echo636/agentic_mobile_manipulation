"""Check referenced scene/model paths without importing or starting OmniGibson."""
import json
from pathlib import Path

from .omnigibson_backend import select_compatible_scene, restore_static_floor_geometry


def asset_identifier(value):
    if not isinstance(value, str) or not value or '/' in value or value in ('.', '..'):
        raise ValueError('Invalid dataset asset identifier')
    return value


def object_part_models(metadata):
    """Official misc JSON uses a list; USD customData exposes numeric dict keys."""
    if not isinstance(metadata, dict):
        raise ValueError('Object metadata must be a dictionary')
    parts = metadata.get('object_parts', [])
    if isinstance(parts, dict):
        parts = list(parts.values())
    if not isinstance(parts, list):
        raise ValueError('Object parts must be a list or dictionary')
    return {(asset_identifier(part['category']), asset_identifier(part['model'])) for part in parts}


def inspect_model_assets(models, asset_root, *, follow_object_parts=True):
    """Follow only object_parts references, never infer a half-object model ID."""
    asset_root = Path(asset_root)
    initial = set(models)
    pending = list(initial)
    seen, dependencies, dynamic, unavailable, metadata_errors = set(), set(), set(), [], []
    while pending:
        category, model = pending.pop()
        category, model = asset_identifier(category), asset_identifier(model)
        if (category, model) in seen:
            continue
        seen.add((category, model))
        relative = f'objects/{category}/{model}/usd/{model}.encrypted.usd'
        dependencies.add(relative)
        if (category, model) not in initial:
            dynamic.add(relative)
        if not follow_object_parts:
            continue
        metadata_path = asset_root / 'objects' / category / model / 'misc/metadata.json'
        if not metadata_path.is_file():
            unavailable.append(str(metadata_path.relative_to(asset_root)))
            continue
        try:
            pending.extend(object_part_models(json.loads(metadata_path.read_text())))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            metadata_errors.append({'path': str(metadata_path.relative_to(asset_root)),
                                    'error_type': type(exc).__name__})
    missing = [p for p in sorted(dependencies) if not (asset_root / p).is_file()]
    return {'status': 'failed' if missing or metadata_errors else 'passed',
            'model_usds_checked': len(dependencies), 'missing_model_usds': missing,
            'dynamic_model_usds': sorted(dynamic), 'metadata_unavailable': sorted(unavailable),
            'metadata_errors': metadata_errors,
            'dynamic_metadata_complete': not unavailable and not metadata_errors}


def inspect_particle_system_assets(system_name, asset_root):
    """Mirror create_system_from_metadata's selected particle template, without initialization."""
    asset_root = Path(asset_root)
    directory = asset_root / 'systems' / asset_identifier(system_name)
    metadata_path = directory / 'metadata.json'
    required = [metadata_path]
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text())
        system_type = metadata['type']
        # Fluid systems do not invoke the particle-template factory.
        if system_type in {'granular', 'macro_physical_particle', 'macro_visual_particle'}:
            models = sorted(p.name for p in directory.iterdir() if p.name != 'metadata.json')
            if models:
                model = asset_identifier(models[0])
                required.append(directory / model / 'usd' / f'{model}.encrypted.usd')
            elif system_type == 'macro_visual_particle':
                # Exact upstream fallback in system_base.create_system_from_metadata.
                required.append(asset_root / 'systems/stain/ahkjul/usd/ahkjul.encrypted.usd')
            # Asset-free granular / physical systems use a procedural sphere.
        elif system_type != 'fluid':
            raise ValueError('Unrecognized particle system metadata type')
    return {'system': system_name,
            'missing_assets': [str(p.relative_to(asset_root)) for p in required if not p.is_file()]}


def inspect_scene_assets(template, instance, asset_root, scene, *, task_name=None):
    asset_root = Path(asset_root)
    deferred = []
    selected, data, _ = select_compatible_scene(
        Path(template), Path(instance), task_name=task_name, deferred_bindings=deferred)
    full = selected.with_name(selected.name.replace('-partial_rooms', ''))
    if full != selected and full.is_file():
        restore_static_floor_geometry(data, json.loads(full.read_text()))
    models = set()
    for entry in data['objects_info']['init_info'].values():
        if entry.get('class_name') != 'DatasetObject':
            continue
        args = entry['args']
        category, model = args['category'], args['model']
        models.add((category, model))
    dependencies = inspect_model_assets(models, asset_root)
    layout = asset_root / 'scenes' / scene / 'layout'
    return {**dependencies,
            'status': 'passed' if dependencies['status'] == 'passed' and layout.is_dir() else 'failed',
            'level': 'template_models_object_parts_metadata_closure_and_scene_layout_paths_only',
            'selected_template': str(selected), 'scene_layout': str(layout),
            'deferred_bindings': deferred,
            'scene_layout_exists': layout.is_dir(), 'full_simulator_load_verified': False}
